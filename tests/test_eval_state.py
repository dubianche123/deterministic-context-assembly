import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from scripts.eval_state import (ConfigurationMismatchError, CorruptStateError,
                                RequestNotDispatchedError, RunLockedError, RunState, RunStateError,
                                UncertainRequestError)


class EvalStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "report.json"
        self.configuration = {"models": ["fixture-model"], "workers": 2, "tokens": 6000}

    def test_completed_samples_resume_and_are_deep_copies(self):
        with RunState(self.output, self.configuration) as state:
            result = state.execute("session", "one", lambda: {"turns": [{"ok": True}]})
            self.assertEqual(result["kind"], "session")
            self.assertEqual(result["cache_key"], "one")
            result["turns"][0]["ok"] = False
            self.assertTrue(state.get("session", "one")["turns"][0]["ok"])
            records = state.records("session")
            records[0]["turns"].clear()
            self.assertEqual(len(state.get("session", "one")["turns"]), 1)
        self.output.write_text("old report", encoding="utf-8")
        with RunState(self.output, self.configuration, resume=True) as state:
            self.assertTrue(state.execute("session", "one", lambda: self.fail("must reuse"))["turns"][0]["ok"])
            self.assertIsNone(state.get("judge", "missing"))
        self.assertEqual(self.output.read_text(), "old report")

    def test_fresh_run_refuses_output_or_samples(self):
        self.output.write_text("report")
        with self.assertRaises(RunStateError):
            with RunState(self.output, self.configuration):
                pass
        self.output.unlink()
        with RunState(self.output, self.configuration):
            pass
        with self.assertRaises(RunStateError):
            with RunState(self.output, self.configuration):
                pass

    def test_manifest_matches_canonical_configuration_and_mismatch_does_not_modify_log(self):
        with RunState(self.output, self.configuration) as state:
            samples = state.samples
        before = samples.read_bytes()
        with self.assertRaises(ConfigurationMismatchError):
            with RunState(self.output, {**self.configuration, "tokens": 100}, resume=True):
                pass
        self.assertEqual(before, samples.read_bytes())
        with RunState(self.output, dict(reversed(list(self.configuration.items()))), resume=True):
            pass

    def test_concurrent_execution_of_one_key_calls_function_once(self):
        entered, release = threading.Event(), threading.Event()
        calls = []

        def work():
            calls.append(1)
            entered.set()
            self.assertTrue(release.wait(3))
            return {"answer": "fixture"}

        with RunState(self.output, self.configuration) as state:
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                futures = [pool.submit(state.execute, "judge", "same", work) for _ in range(6)]
                self.assertTrue(entered.wait(3))
                release.set()
                rows = [future.result(timeout=3) for future in futures]
            self.assertEqual(len(calls), 1)
            self.assertEqual(rows, [rows[0]] * 6)
            lines = [json.loads(line) for line in state.samples.read_text().splitlines()]
            self.assertEqual([line.get("state") for line in lines], [None, "pending", "finished"])

    def test_distinct_keys_can_execute_concurrently(self):
        barrier = threading.Barrier(2)
        with RunState(self.output, self.configuration) as state:
            def work():
                barrier.wait(timeout=3)
                return {"ok": True}
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                rows = list(pool.map(lambda key: state.execute("judge", key, work), ("one", "two")))
            self.assertEqual(len(rows), 2)
            self.assertEqual(len(state.records("judge")), 2)

    def test_failed_request_stays_pending_and_resume_does_not_call_again(self):
        def fail():
            raise RuntimeError("fixture request interrupted")
        with RunState(self.output, self.configuration) as state:
            with self.assertRaises(RuntimeError):
                state.execute("judge", "uncertain", fail)
            self.assertIsNone(state.get("judge", "uncertain"))
            with self.assertRaises(UncertainRequestError):
                state.execute("judge", "uncertain", lambda: self.fail("must not redispatch"))
        with RunState(self.output, self.configuration, resume=True) as state:
            with self.assertRaises(UncertainRequestError):
                state.execute("judge", "uncertain", lambda: self.fail("must not redispatch"))

    def test_undispatched_request_can_be_executed_after_resume(self):
        def no_dispatch():
            raise RequestNotDispatchedError("fixture budget was exhausted before dispatch")
        with RunState(self.output, self.configuration) as state:
            samples = state.samples
            with self.assertRaises(RequestNotDispatchedError):
                state.execute("judge", "safe-to-retry", no_dispatch)
            self.assertIsNone(state.get("judge", "safe-to-retry"))
            self.assertEqual(state.records("judge"), [])
        entries = [json.loads(line) for line in samples.read_text().splitlines()]
        self.assertEqual([row.get("state") for row in entries], [None, "pending", "not_dispatched"])
        calls = []
        with RunState(self.output, self.configuration, resume=True) as state:
            row = state.execute("judge", "safe-to-retry", lambda: (calls.append(1) or {"ok": True}))
            self.assertTrue(row["ok"])
        self.assertEqual(calls, [1])
        with RunState(self.output, self.configuration, resume=True) as state:
            self.assertTrue(state.execute("judge", "safe-to-retry", lambda: self.fail("already finished"))["ok"])
        entries = [json.loads(line) for line in samples.read_text().splitlines()]
        self.assertEqual([row.get("state") for row in entries],
                         [None, "pending", "not_dispatched", "pending", "finished"])

    def test_undispatched_request_can_be_retried_in_the_same_process(self):
        with RunState(self.output, self.configuration) as state:
            with self.assertRaises(RequestNotDispatchedError):
                state.execute("generation", "one", lambda: (_ for _ in ()).throw(RequestNotDispatchedError()))
            self.assertEqual(state.execute("generation", "one", lambda: {"answer": "complete"})["answer"], "complete")

    def test_invalid_or_duplicate_undispatched_records_fail_closed(self):
        with RunState(self.output, self.configuration) as state:
            samples = state.samples
        manifest = samples.read_text()
        pending = {"kind": "judge", "cache_key": "one", "state": "pending"}
        undispatched = {**pending, "state": "not_dispatched"}
        finished = {**pending, "state": "finished", "row": {"kind": "judge", "cache_key": "one", "ok": True}}
        for tail in ([undispatched], [pending, undispatched, undispatched],
                     [pending, finished, undispatched],
                     [pending, {**undispatched, "cache_key": "different"}]):
            with self.subTest(tail=tail):
                wire = manifest + "".join(json.dumps(row) + "\n" for row in tail)
                samples.write_text(wire)
                with self.assertRaises(CorruptStateError):
                    with RunState(self.output, self.configuration, resume=True):
                        pass
                self.assertEqual(samples.read_text(), wire)
    def test_pending_checkpoint_is_flushed_before_request(self):
        with RunState(self.output, self.configuration) as state:
            def observe():
                records = [json.loads(line) for line in state.samples.read_text().splitlines()]
                self.assertEqual(records[-1]["state"], "pending")
                return {"ok": True}
            state.execute("judge", "one", observe)
            self.assertEqual(os.stat(state.samples).st_mode & 0o777, 0o600)

    def test_resume_repairs_only_incomplete_tail_and_preserves_uncertain_request(self):
        with RunState(self.output, self.configuration) as state:
            state.put("session", "one", {"ok": True})
            samples = state.samples
        clean = samples.read_bytes()
        samples.write_bytes(clean + b'{"kind":"judge","cache_key":"partial"')
        with RunState(self.output, self.configuration, resume=True) as state:
            self.assertEqual(state.samples.read_bytes(), clean)
            state.put("judge", "two", {"ok": True})
        with RunState(self.output, self.configuration, resume=True) as state:
            self.assertTrue(state.get("judge", "two")["ok"])

    def test_partial_completed_tail_keeps_the_durable_pending_marker(self):
        with RunState(self.output, self.configuration) as state:
            samples = state.samples
            with self.assertRaises(RuntimeError):
                state.execute("judge", "paid", lambda: (_ for _ in ()).throw(RuntimeError("interrupted")))
        pending = samples.read_bytes()
        samples.write_bytes(pending + b'{"kind":"judge","cache_key":"paid","state":"finished","row":')
        with RunState(self.output, self.configuration, resume=True) as state:
            self.assertEqual(samples.read_bytes(), pending)
            with self.assertRaises(UncertainRequestError):
                state.execute("judge", "paid", lambda: self.fail("must not spend again"))

    def test_resume_never_swallows_middle_or_newline_terminated_corruption(self):
        with RunState(self.output, self.configuration) as state:
            samples = state.samples
        manifest = samples.read_bytes()
        for suffix in (b'not json\n', b'not json\n{"state":'):
            with self.subTest(suffix=suffix):
                samples.write_bytes(manifest + suffix)
                before = samples.read_bytes()
                with self.assertRaises(CorruptStateError):
                    with RunState(self.output, self.configuration, resume=True):
                        pass
                self.assertEqual(samples.read_bytes(), before)

    def test_lock_contention_and_release_after_exception(self):
        with RunState(self.output, self.configuration):
            with self.assertRaises(RunLockedError):
                with RunState(self.output, self.configuration, resume=True):
                    pass
            code = ("from pathlib import Path; from scripts.eval_state import RunState, RunLockedError; "
                    "import sys; "
                    "state=RunState(Path(sys.argv[1]), {}, resume=True); "
                    "\ntry:\n state.__enter__()\nexcept RunLockedError:\n sys.exit(23)\n")
            result = subprocess.run([sys.executable, "-c", code, str(self.output)],
                                    cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 23, result.stderr.decode())
        with self.assertRaisesRegex(RuntimeError, "fixture"):
            with RunState(self.output, self.configuration, resume=True):
                raise RuntimeError("fixture")
        with RunState(self.output, self.configuration, resume=True):
            pass

    def test_log_does_not_capture_environment_credentials_or_exception_text(self):
        fixture_key = "fixture-private-key-do-not-log"
        previous = os.environ.get("NORN_API_KEY")
        os.environ["NORN_API_KEY"] = fixture_key
        try:
            with RunState(self.output, self.configuration) as state:
                samples = state.samples
                with self.assertRaises(RuntimeError):
                    state.execute("judge", "one", lambda: (_ for _ in ()).throw(RuntimeError(fixture_key)))
            self.assertNotIn(fixture_key, samples.read_text())
            manifest = json.loads(samples.read_text().splitlines()[0])
            self.assertEqual(set(manifest), {"kind", "schema", "configuration_hash"})
        finally:
            if previous is None:
                os.environ.pop("NORN_API_KEY", None)
            else:
                os.environ["NORN_API_KEY"] = previous

    def test_put_is_immutable_and_rejects_identity_conflicts(self):
        with RunState(self.output, self.configuration) as state:
            row = state.put("judge", "one", {"ok": True})
            self.assertEqual(state.put("judge", "one", {"ok": True}), row)
            with self.assertRaises(RunStateError):
                state.put("judge", "one", {"ok": False})
            with self.assertRaises(ValueError):
                state.put("judge", "two", {"cache_key": "different"})


if __name__ == "__main__":
    unittest.main()

"""Offline acceptance checks for the paid-evaluation CLI lifecycle."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import prose_eval


KEY = "fixture-private-evaluation-key"
MODEL = "fixture:deepseek"
JUDGE = "fixture:kimi"
READING = ("你留下的画面像是一段慢慢展开的旅程。也许吸引你的是其中可以停留的空间，"
           "也许只是这一刻想看看不同的风景。这些选择只属于这一次，你可以带着自己的感受，"
           "再留意下一个让你愿意停留的画面。")
VERDICT = {"better": "B", "grounded": {"A": 4, "B": 5},
           "resonance": {"A": 3, "B": 5}, "clarity": {"A": 4, "B": 5},
           "overclaim": {"A": False, "B": False}, "reason": "后者更清楚自然。"}


class InterruptedEvaluation(BaseException):
    """Emulate a process interruption outside ordinary provider exceptions."""


MIRRORED = {"better": "A", "grounded": {"A": 5, "B": 4}, "resonance": {"A": 5, "B": 3},
            "clarity": {"A": 5, "B": 4}, "overclaim": {"A": False, "B": False}, "reason": "前者更清楚自然。"}


class FakeTransportFactory:
    def __init__(self, *, interrupt_judge=None, verdict=None, forbid_requests=False, reading=READING,
                 respond=None):
        self.instances = []
        self.calls = []
        self.interrupt_judge = interrupt_judge
        self.verdict = VERDICT if verdict is None else verdict
        self.forbid_requests = forbid_requests
        self.reading = reading
        # respond(model, judge_call_number) -> verdict dict, or None for a transport failure
        self.respond = respond
        self.lock = threading.Lock()

    def __call__(self, base_url, api_key, **options):
        instance = FakeTransport(self, api_key, options)
        self.instances.append(instance)
        return instance


class FakeTransport:
    def __init__(self, factory, api_key, options):
        self.factory, self.api_key, self.options = factory, api_key, options
        self.calls = []

    def _record(self, kind, payload):
        if self.factory.forbid_requests:
            raise AssertionError("checkpoint reuse must not dispatch network work")
        with self.factory.lock:
            if len(self.calls) >= self.options.get("max_requests", 128):
                return None
            self.calls.append(kind)
            self.factory.calls.append({"kind": kind, "transport": self, "payload": deepcopy(payload)})
            count = sum(row["kind"] == "judge" for row in self.factory.calls)
        return count

    def complete(self, payload, **options):
        if self._record("generation", payload) is None:
            return None, [{"attempt": 1, "dispatched": False, "ok": False, "error": "budget_exhausted"}]
        return ({"id": "fixture-response", "model": payload["model"],
                 "choices": [{"finish_reason": "stop", "message": {"content": self.factory.reading}}],
                 "usage": {"prompt_tokens": 40, "completion_tokens": 20, "total_tokens": 60}},
                [{"attempt": 1, "dispatched": True, "ok": True, "http_status": 200,
                  "finish_reason": "stop", "usage": {"prompt_tokens": 40, "completion_tokens": 20}}])

    def chat(self, model, messages, **options):
        number = self._record("judge", {"model": model, "messages": messages, **options})
        if number is None:
            return None, None, [{"attempt": 1, "dispatched": False, "ok": False, "error": "budget_exhausted"}]
        if number == self.factory.interrupt_judge:
            raise InterruptedEvaluation()
        verdict = self.factory.respond(model, number) if self.factory.respond else self.factory.verdict
        if verdict is None:
            return None, None, [{"attempt": 1, "dispatched": True, "ok": False, "http_status": 503}]
        return (json.dumps(verdict, ensure_ascii=False), "stop",
                [{"attempt": 1, "dispatched": True, "ok": True, "http_status": 200,
                  "finish_reason": "stop", "usage": {"prompt_tokens": 50, "completion_tokens": 30}}])

    def snapshot(self):
        return {"requests": len(self.calls), "calls": len(self.calls),
                "succeeded": len(self.calls), "max_active": min(len(self.calls), 2)}


class ProseEvalCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "evaluation.json"
        self.fixtures = patch.object(prose_eval, "FIXTURES", ((4, 4, 11),))
        self.fixtures.start()
        self.addCleanup(self.fixtures.stop)

    def invoke(self, *extra, factory=None, output=None, judge=JUDGE):
        factory = factory or FakeTransportFactory()
        stdout, stderr = io.StringIO(), io.StringIO()
        args = ["--base-url", "http://127.0.0.1:17864/v1", "--model", MODEL,
                "--output", str(output or self.output), *extra]
        if judge is not None:
            args += ["--judge", judge]
        with patch.object(prose_eval, "EvalTransport", factory), patch.dict(os.environ, {"NORN_API_KEY": KEY}):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = prose_eval.main(args)
        return result, factory, stdout.getvalue(), stderr.getvalue()

    def samples(self, output=None):
        path = output or self.output
        return path.with_name(path.name + ".samples.jsonl")

    def entries(self, output=None):
        return [json.loads(line) for line in self.samples(output).read_text().splitlines()]

    def test_dry_run_uses_default_six_workers_without_reading_credentials_or_writing(self):
        missing_key = Path(self.temp.name) / "does-not-exist-secret.json"
        with patch.dict(os.environ, {}, clear=True):
            result, factory, stdout, _ = self.invoke("--dry-run", "--key-file", str(missing_key), judge=None)
        plan = json.loads(stdout)
        self.assertEqual(result, 0)
        self.assertEqual((plan["workers"], plan["max_in_flight"]), (6, 6))
        self.assertEqual(plan["judges"], ["cn:mimo-v2.6-pro"])  # no automatic HY4 fallback
        self.assertEqual(plan["judge_max_tokens"], 24000)
        self.assertEqual(plan["session_jobs"], 2)
        self.assertEqual(factory.calls, [])
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_fresh_run_shares_transport_and_records_each_paid_stage(self):
        with patch.object(prose_eval, "NornService", wraps=prose_eval.NornService) as service:
            result, factory, stdout, stderr = self.invoke()
        self.assertEqual(result, 0)
        self.assertEqual(service.call_count, 2)
        self.assertTrue(all(call.kwargs["algorithm_version"] == "norn.scoring.v4"
                            for call in service.call_args_list))
        actual = [instance for instance in factory.instances if instance.api_key]
        self.assertEqual(len(actual), 1)
        self.assertEqual({row["transport"] for row in factory.calls}, {actual[0]})
        self.assertEqual({row["kind"] for row in factory.calls}, {"generation", "judge"})
        report = json.loads(self.output.read_text())
        self.assertEqual(report["execution"]["workers"], 6)
        self.assertEqual(report["execution"]["max_in_flight"], 6)
        self.assertEqual(len(report["sessions"]), 2)
        self.assertTrue(all(turn["status"] == "ok" for session in report["sessions"] for turn in session["turns"]))
        self.assertEqual(len(report["verdicts"]), 2)
        self.assertIsNone(report["judge_reasoning_effort"])
        self.assertTrue(all(row["payload"]["reasoning"] is None
                            for row in factory.calls if row["kind"] == "judge"))
        self.assertTrue(all(row["v5_score"] is not None for row in report["verdicts"]))
        entries = self.entries()
        self.assertEqual(entries[0]["kind"], "run_manifest")
        completed = [row for row in entries if row.get("state") == "finished"]
        self.assertEqual({row["kind"] for row in completed},
                         {"session", "generation", "judge_request", "verdict"})
        for artifact in (self.output.read_text(), self.samples().read_text(), stdout, stderr):
            self.assertNotIn(KEY, artifact)

    def test_complete_resume_does_not_dispatch_or_replace_report(self):
        self.invoke()
        old_report = self.output.read_bytes()
        old_log = self.samples().read_bytes()
        result, factory, stdout, _ = self.invoke("--resume", "--workers", "4",
                                               factory=FakeTransportFactory(forbid_requests=True))
        self.assertEqual(result, 0)
        self.assertEqual(factory.calls, [])
        self.assertEqual(self.output.read_bytes(), old_report)
        self.assertEqual(self.samples().read_bytes(), old_log)
        self.assertEqual(json.loads(stdout)["network_requests_this_invocation"], 0)

    def test_rebuild_without_session_summaries_reuses_generation_and_judges(self):
        self.invoke()
        old_report = json.loads(self.output.read_text())
        self.output.unlink()
        self.output.with_name(self.output.name + ".sessions.json").unlink()
        # Simulate losing only derived summaries in this test's own temporary
        # artifacts, keeping the durable individual paid-request checkpoints.
        remaining = [row for row in self.entries() if row.get("kind") not in ("session", "verdict")]
        self.samples().write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in remaining))
        result, factory, _, _ = self.invoke("--resume", factory=FakeTransportFactory(forbid_requests=True))
        self.assertEqual(result, 0)
        self.assertEqual(factory.calls, [])
        rebuilt = json.loads(self.output.read_text())
        self.assertEqual([row["turns"] for row in rebuilt["sessions"]],
                         [row["turns"] for row in old_report["sessions"]])
        self.assertEqual([row["v5_score"] for row in rebuilt["verdicts"]],
                         [row["v5_score"] for row in old_report["verdicts"]])
        self.assertTrue(all(row["reused"] for session in rebuilt["sessions"] for row in session["wire_requests"]))
        self.assertTrue(all(call["reused"] for row in rebuilt["verdicts"] for call in row["calls"]))

    def test_interrupted_backward_judge_is_not_retried_and_forward_result_is_reused(self):
        interrupted = FakeTransportFactory(interrupt_judge=2)
        with self.assertRaises(InterruptedEvaluation):
            self.invoke(factory=interrupted)
        self.assertFalse(self.output.exists())
        before = self.entries()
        requests = [row for row in before if row.get("kind") == "judge_request"]
        self.assertEqual([row["state"] for row in requests], ["pending", "finished", "pending"])
        result, factory, _, _ = self.invoke("--resume", factory=FakeTransportFactory(forbid_requests=True))
        self.assertEqual(result, 0)
        self.assertEqual(factory.calls, [])
        report = json.loads(self.output.read_text())
        forward, backward = report["verdicts"]
        self.assertEqual(forward["v4_position"], "A")
        self.assertIsNotNone(forward["v5_score"])
        self.assertEqual(backward["v4_position"], "B")
        self.assertIsNone(backward["v5_score"])
        self.assertEqual(backward["calls"][0]["rejected"], "uncertain_previous_attempt")
        self.assertEqual([row for row in self.entries() if row.get("kind") == "judge_request"], requests)

    def test_budget_pause_keeps_paid_checkpoints_and_resume_only_sends_unfinished_work(self):
        result, factory, _, stderr = self.invoke("--workers", "1", "--max-requests", "1")
        self.assertEqual(result, 75)
        self.assertFalse(self.output.exists())
        self.assertIn('"event": "evaluation.paused"', stderr)
        self.assertEqual(len(factory.calls), 1)
        first_completed = [row for row in self.entries() if row.get("state") == "finished"]
        self.assertEqual([row["kind"] for row in first_completed], ["generation"])
        self.assertTrue(any(row.get("state") == "not_dispatched" for row in self.entries()))
        all_calls = list(factory.calls)

        # Increase the per-invocation budget and resume until a forward judge
        # result is durable. This also covers a session interrupted mid-dialogue.
        for _ in range(6):
            result, resumed, _, _ = self.invoke("--resume", "--workers", "1", "--max-requests", "2")
            all_calls.extend(resumed.calls)
            self.assertEqual(result, 75)
            self.assertFalse(self.output.exists())
            if any(row["kind"] == "judge" for row in resumed.calls):
                break
        else:
            self.fail("small synthetic evaluation never reached its judge")
        completed_requests = [row for row in self.entries()
                              if row.get("state") == "finished" and row["kind"] in ("generation", "judge_request")]
        self.assertEqual(sum(row["kind"] == "judge_request" for row in completed_requests), 1)

        result, final_transport, _, _ = self.invoke("--resume", "--workers", "1", "--max-requests", "128")
        self.assertEqual(result, 0)
        self.assertEqual([row["kind"] for row in final_transport.calls], ["judge"])
        all_calls.extend(final_transport.calls)
        report = json.loads(self.output.read_text())
        self.assertTrue(all(turn["status"] == "ok" for session in report["sessions"] for turn in session["turns"]))
        self.assertTrue(all(row["v5_score"] is not None for row in report["verdicts"]))
        # The fake deliberately gives both arms identical prose, so the two
        # required judge orderings have equal wire text despite distinct scopes.
        generation_wires = [json.dumps(row["payload"], sort_keys=True, ensure_ascii=False)
                            for row in all_calls if row["kind"] == "generation"]
        self.assertEqual(len(generation_wires), len(set(generation_wires)), "completed generations must never repeat")
        self.assertEqual(sum(row["kind"] == "judge" for row in all_calls), 2)
        final_finished = [row for row in self.entries()
                          if row.get("state") == "finished" and row["kind"] in ("generation", "judge_request")]
        self.assertEqual(len(final_finished), len(all_calls))

    def test_malformed_judge_json_is_not_counted_as_a_quality_verdict(self):
        malformed = {**deepcopy(VERDICT), "grounded": {"A": True, "B": 5}}
        self.invoke(factory=FakeTransportFactory(verdict=malformed))
        report = json.loads(self.output.read_text())
        judged = report["summary"]["judge"][MODEL]
        self.assertEqual((judged["decided_pairs"], judged["outcomes"]["abstain"]), (0, 1))
        self.assertTrue(all(row["v5_score"] is None for row in report["verdicts"]))
        self.assertTrue(all(row["calls"][0]["rejected"] == "unparsable verdict" for row in report["verdicts"]))

    def test_valid_fractional_judge_scores_keep_the_existing_rating_range(self):
        verdict = {**deepcopy(VERDICT), "grounded": {"A": 3.5, "B": 4.5}}
        self.invoke(factory=FakeTransportFactory(verdict=verdict))
        report = json.loads(self.output.read_text())
        self.assertTrue(all(row["v5_score"] is not None for row in report["verdicts"]))

    def test_production_wire_matches_the_worker_request(self):
        _, factory, _, _ = self.invoke("--wire", "production")
        from norn.cloud_provider import prepare_cloud_request
        generations = [row["payload"] for row in factory.calls if row["kind"] == "generation"]
        self.assertTrue(generations)
        for payload in generations:
            self.assertEqual(payload["thinking"], {"type": "disabled"})
            self.assertNotIn("reasoning_effort", payload)
            self.assertEqual(payload["model"], MODEL)
            self.assertLessEqual(payload["max_tokens"], 1600)
        report = json.loads(self.output.read_text())
        self.assertEqual(report["roles"]["candidates"]["wire"], "production")
        self.assertEqual(report["roles"]["production"]["thinking"], "disabled")
        compatible = self.invoke(output=Path(self.temp.name) / "compatible.json")[1]
        payload = next(row["payload"] for row in compatible.calls if row["kind"] == "generation")
        self.assertEqual((payload.get("reasoning_effort"), payload.get("thinking")), ("off", None))
        self.assertTrue(callable(prepare_cloud_request))

    def test_same_side_in_both_orders_is_an_order_disagreement_not_a_win(self):
        self.invoke()  # the default fake always answers "B", whichever text sits there
        judged = json.loads(self.output.read_text())["summary"]["judge"][MODEL]
        self.assertEqual(judged["outcomes"]["order_disagreement"], 1)
        self.assertEqual((judged["decided_pairs"], judged["coverage"]), (0, 0.0))
        self.assertIsNone(judged["v5_win_rate_decided"])
        self.assertEqual(judged["mean_scores_decided"], {})

    def test_agreeing_orders_decide_the_pair(self):
        respond = lambda model, number: VERDICT if number % 2 else MIRRORED  # v5 is B, then A
        self.invoke(factory=FakeTransportFactory(respond=respond))
        judged = json.loads(self.output.read_text())["summary"]["judge"][MODEL]
        self.assertEqual((judged["outcomes"]["v5"], judged["decided_pairs"], judged["v5_win_rate_decided"]), (1, 1, 1.0))
        self.assertEqual(judged["judges"], [JUDGE])

    def test_contradictory_verdict_abstains_and_does_not_fall_back(self):
        contradictory = {**deepcopy(VERDICT), "better": "A"}  # B scores higher everywhere, yet A "wins"
        factory = FakeTransportFactory(verdict=contradictory)
        self.invoke("--judge", JUDGE, "--judge", "fixture:second", factory=factory, judge=None)
        report = json.loads(self.output.read_text())
        self.assertTrue(all(row["issue"] == "winner_dominated_by_loser" and row["v5_score"] is None
                            for row in report["verdicts"]))
        self.assertEqual({row["payload"]["model"] for row in factory.calls if row["kind"] == "judge"}, {JUDGE})
        self.assertEqual(report["summary"]["judge"][MODEL]["abstentions"], {"winner_dominated_by_loser": 2})

    def test_second_order_stays_with_the_first_orders_judge(self):
        # The first judge answers the first order, then fails; the second judge must not fill in.
        respond = lambda model, number: None if (model == JUDGE and number > 1) else VERDICT
        factory = FakeTransportFactory(respond=respond)
        self.invoke("--judge", JUDGE, "--judge", "fixture:second", factory=factory, judge=None)
        report = json.loads(self.output.read_text())
        judges = [row["payload"]["model"] for row in factory.calls if row["kind"] == "judge"]
        self.assertEqual(judges, [JUDGE, JUDGE])
        forward, backward = report["verdicts"]
        self.assertEqual((forward["judge"], backward["issue"]), (JUDGE, "no_valid_verdict"))
        self.assertEqual(report["summary"]["judge"][MODEL]["outcomes"]["abstain"], 1)

    def test_generation_still_uses_the_production_output_guardrails(self):
        _, factory, _, _ = self.invoke(factory=FakeTransportFactory(reading="你的 MBTI 是 INTJ。"))
        report = json.loads(self.output.read_text())
        self.assertEqual([row["kind"] for row in factory.calls], ["generation", "generation"])
        self.assertEqual(report["verdicts"], [])
        self.assertTrue(all(session["turns"][0]["status"] == "invalid_output" for session in report["sessions"]))
        self.assertTrue(all("internal_type" in session["turns"][0]["reason_codes"] for session in report["sessions"]))
        self.assertTrue(all(row["guardrail_rejections"] == 1 for row in report["summary"]["by_arm"]))

    def test_legacy_sessions_can_be_imported_without_regenerating(self):
        self.invoke()
        rows = json.loads(self.output.read_text())["sessions"]
        for row in rows:
            row.pop("kind", None)
            row.pop("cache_key", None)
            row.pop("wire_requests", None)
        legacy = Path(self.temp.name) / "legacy.sessions.json"
        legacy.write_text(json.dumps(rows, ensure_ascii=False))
        imported_output = Path(self.temp.name) / "imported.json"
        result, factory, _, _ = self.invoke("--resume-sessions", str(legacy), output=imported_output)
        self.assertEqual(result, 0)
        self.assertEqual([row["kind"] for row in factory.calls], ["judge", "judge"])
        self.assertNotIn("generation", {row["kind"] for row in self.entries(imported_output)})
        self.assertEqual(len(json.loads(imported_output.read_text())["sessions"]), 2)

    def test_fresh_existing_report_or_samples_fails_before_any_dispatch(self):
        for existing in ("report", "samples"):
            with self.subTest(existing=existing):
                output = Path(self.temp.name) / (existing + ".json")
                path = output if existing == "report" else self.samples(output)
                path.write_text("original artifact")
                factory = FakeTransportFactory(forbid_requests=True)
                with self.assertRaises(SystemExit) as raised:
                    self.invoke(factory=factory, output=output)
                self.assertEqual(raised.exception.code, 2)
                self.assertEqual(factory.calls, [])
                self.assertEqual(path.read_text(), "original artifact")

    def test_interruption_cancels_queued_work_and_generation_and_judge_refuse_dispatch(self):
        factory = FakeTransportFactory(forbid_requests=True)
        args = SimpleNamespace(workers=1, cancelled=threading.Event(), judge=[JUDGE],
                               transport=factory("http://127.0.0.1:17864/v1", KEY),
                               state=object(), timeout=10, judge_reasoning="low", judge_max_tokens=6000)
        generate = prose_eval.session_generator(args, KEY, "fixture-job", [])
        first_started, release_first = threading.Event(), threading.Event()

        def check_entry_guards():
            with self.assertRaises(KeyboardInterrupt):
                generate("openai-compatible", {}, config={})
            with self.assertRaises(KeyboardInterrupt):
                prose_eval.judge(args, KEY, "真实画面", READING, READING, scope="fixture-scope")

        def task(index):
            if index == 0:
                first_started.set()
                self.assertTrue(release_first.wait(3))
                raise InterruptedEvaluation()
            # An executor can start its next task before the caller observes the
            # first failure. Synchronize that race, then check the real paid
            # dispatch entry points rather than assuming no task ever started.
            self.assertTrue(args.cancelled.wait(3))
            check_entry_guards()

        def release():
            if first_started.wait(3):
                release_first.set()

        releaser = threading.Thread(target=release)
        releaser.start()
        try:
            with self.assertRaises(InterruptedEvaluation):
                prose_eval.parallel_map(args, task, range(8))
        finally:
            release_first.set()
            releaser.join(timeout=3)
        self.assertTrue(args.cancelled.is_set())
        check_entry_guards()
        self.assertEqual(factory.calls, [])


QUALIFICATION = Path(__file__).resolve().parents[1] / "benchmarks" / "results"


class SavedJudgeRegressionTests(unittest.TestCase):
    """Zero-model checks against the verdicts saved by the 2026-10-21 judge qualification."""

    def rows(self, run):
        return json.loads((QUALIFICATION / run / "results.json").read_text(encoding="utf-8"))["rows"]

    def normalised(self, row):
        v5_side = next(side for side, label in row["side_labels"].items() if label == "v5")
        better = row["verdict"]["better"]
        return 0.5 if better == "tie" else float(better == v5_side)

    def test_saved_contradiction_and_order_reversal_are_caught(self):
        rows = {(row["case"], row["order"]): row for row in self.rows("judge-qualification-mimo-json-v1-20261021")}
        contradiction = rows[("natural-glm-5.3-flash-fixture-0", 0)]["verdict"]
        self.assertEqual(prose_eval.verdict_issue(contradiction), "winner_dominated_by_loser")
        reversal = [rows[("natural-glm-5.3-flash-fixture-4", order)] for order in (0, 1)]
        self.assertTrue(all(prose_eval.verdict_issue(row["verdict"]) is None for row in reversal))
        self.assertEqual(prose_eval.pair_outcome([{"v5_score": self.normalised(row)} for row in reversal]),
                         "order_disagreement")
        agreeing = [rows[("natural-deepseek-v4.1-flash-fixture-3", order)] for order in (0, 1)]
        self.assertEqual(prose_eval.pair_outcome([{"v5_score": self.normalised(row)} for row in agreeing]), "v5")

    def test_saved_controls_pass_the_consistency_check(self):
        for row in self.rows("judge-qualification-mimo-json-v1-20261021"):
            if row["kind"] != "natural":
                self.assertIsNone(prose_eval.verdict_issue(row["verdict"]), row["case"])

    def test_saved_malformed_json_is_not_a_verdict(self):
        broken = [row for row in self.rows("judge-qualification-v1-20261021")
                  if row["model"] == "cn:mimo-v2.6-pro" and not row["valid"]]
        self.assertTrue(broken)
        for row in broken:
            try:
                parsed = json.loads(row["raw_text"])
            except (TypeError, ValueError):
                parsed = None
            self.assertFalse(prose_eval.valid_verdict(parsed))


class JudgeFactsTests(unittest.TestCase):
    def test_facts_name_every_source_either_prompt_gave_the_writer(self):
        from norn.simulate import simulate_records
        journal = simulate_records(1, visits=(1, 1), rounds=(10, 12), seed=21)[0]["journal"]
        facts = prose_eval.facts_for(journal)
        for label in ("最终留下", "系统算出的视觉统计", "系统按选择推断的画面元素", "系统推荐、本次没有选过的画面",
                      "线索清楚程度", "元素："):
            self.assertIn(label, facts)

    def test_fact_reconstruction_explicitly_uses_the_frozen_scoring_version(self):
        from norn.profile import build_event_profile
        from norn.simulate import simulate_records
        journal = simulate_records(1, visits=(1, 1), rounds=(4, 5), seed=11)[0]["journal"]
        with patch.object(prose_eval, "build_event_profile", wraps=build_event_profile) as build:
            default = prose_eval.facts_for(journal)
            explicit = prose_eval.facts_for(journal, algorithm_version="norn.scoring.v4")
        self.assertEqual(default, explicit)
        self.assertEqual([call.kwargs for call in build.call_args_list],
                         [{"algorithm_version": "norn.scoring.v4"}] * 2)


if __name__ == "__main__":
    unittest.main()

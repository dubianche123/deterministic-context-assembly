"""Portable recordings through real service, HTTP and model-free CLI boundaries."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from norn.cli import main
from norn.core import engine_module
from norn.service import NornService, RequestError, SessionStore
from norn.server import make_server


def journal():
    rows = [
        {"type": "round_started", "round": 1, "card_ids": ["NORN_0001", "NORN_0008"]},
        {"type": "card_selected", "round": 1, "card_id": "NORN_0001"},
        {"type": "card_deselected", "round": 1, "card_id": "NORN_0001"},
        {"type": "card_selected", "round": 1, "card_id": "NORN_0001"},
        {"type": "round_completed", "round": 1, "duration_ms": 1000},
        {"type": "round_started", "round": 2, "card_ids": ["NORN_0008", "NORN_0002"]},
        {"type": "card_selected", "round": 2, "card_id": "NORN_0008"},
        {"type": "round_completed", "round": 2, "duration_ms": 20000},
        {"type": "session_completed"},
    ]
    return {"schema_version": "norn.events.v1", "events": [
        {"event_id": f"evt-{number:06}", "sequence": number, **row}
        for number, row in enumerate(rows, 1)]}


class ReplayIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        def render(provider, prompt, **kwargs):
            self.calls.append(deepcopy({"prompt": prompt, **kwargs}))
            return {"text": "A rendered reading.", "status": "ok", "provider": provider,
                    "simulated": True, "usage": {}, "provider_calls": 0}
        self.service = NornService(generate=render)

    def test_journal_export_replay_and_follow_up_keep_one_profile(self):
        source = journal()
        original = deepcopy(source)
        result = self.service.analyze({"journal": source})
        self.assertEqual(source, original)
        self.assertEqual(result["profile_source"], "event_journal")
        self.assertEqual(result["profile_version"], "norn.profile.v5")
        self.assertEqual(result["algorithm_version"], "norn.scoring.v5")
        self.assertEqual(result["preference_version"], "norn.choice.guarded-mnl.v1")
        self.assertTrue(result["events_hash"].startswith("sha256:"))
        bundle = self.service.export_session(result["session_id"])
        ledger = bundle["profile"]["analysis"]["attribution"]
        self.assertEqual([row["event_id"] for row in ledger["selections"]], ["evt-000004", "evt-000007"])
        self.assertEqual([row["event_id"] for row in ledger["revision"]["events"]], ["evt-000003"])
        count = len(self.calls)
        verified = self.service.replay(bundle)
        self.assertTrue(verified["verified"])
        self.assertEqual(len(self.calls), count)
        self.assertIsNone(verified["reconstructed_coords"], "v4 has no coordinate ledger to rebuild")
        self.assertIsNone(result["coords_avg"])
        reply = self.service.dialogue({"session_id": result["session_id"], "user_message": "why?"})
        self.assertEqual(reply["profile_hash"], result["profile_hash"])
        self.assertEqual(self.service.export_session(result["session_id"]), bundle)
        self.assertNotIn("norn.attribution", json.dumps(self.calls[-1]["prompt"]))
        self.assertNotIn("norn.events", json.dumps(self.calls[-1]["prompt"]))
        self.assertNotIn("reading", bundle["profile"])

    def test_export_is_detached_and_legacy_snapshot_is_not_a_journal(self):
        result = self.service.analyze({"selections": [{"id": "NORN_0001", "round": 1}], "total_rounds": 1})
        bundle = self.service.export_session(result["session_id"])
        self.assertIsNone(bundle["journal"])
        self.assertEqual(result["profile_source"], "legacy_snapshot")
        self.assertIsNone(result["events_hash"])
        self.assertNotIn("events_hash", bundle["profile"])
        self.service.replay(bundle)
        bundle["profile"]["analysis"]["attribution"]["selections"][0]["round"] = 999
        self.assertNotEqual(self.service.export_session(result["session_id"]), bundle)
        with self.assertRaises(RequestError):
            self.service.replay(bundle)

    def test_bad_event_records_stop_before_rendering(self):
        broken = journal()
        broken["events"][2]["card_id"] = "NORN_0008"  # Never selected.
        for body in ({"journal": broken}, {"journal": journal(), "selections": []}):
            with self.assertRaises(RequestError):
                self.service.analyze(body)
        self.assertEqual(self.calls, [])

    def test_non_ascii_profile_hash_is_a_validation_error(self):
        result = self.service.analyze({"journal": journal()})
        bundle = self.service.export_session(result["session_id"])
        bundle["profile"]["profile_hash"] = "无效"
        with self.assertRaises(RequestError):
            self.service.replay(bundle)

    def test_cli_replay_works_offline_and_does_not_overwrite_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.json"
            output = Path(directory) / "export.json"
            source.write_text(json.dumps(journal()))
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch("norn.providers.generate", side_effect=AssertionError("replay called a model")), \
                    patch("socket.create_connection", side_effect=AssertionError("replay used network")), \
                    redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(main(["replay", str(source), "--json", "--output", str(output)]), 0)
            summary = json.loads(stdout.getvalue())
            self.assertTrue(summary["verified"])
            self.assertEqual(summary["provider_calls"], 0)
            self.assertEqual(summary["event_count"], 9)
            original = output.read_bytes()
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["replay", str(output), "--output", str(output)]), 1)
            self.assertEqual(output.read_bytes(), original)
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["replay", str(output)]), 0)

    def test_cli_rejects_duplicate_keys_and_broken_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "bad.json"
            source.write_text('{"schema_version":"norn.events.v1","events":[],"events":[]}')
            with redirect_stderr(io.StringIO()):
                self.assertEqual(main(["replay", str(source)]), 1)
            result = self.service.analyze({"journal": journal()})
            bundle = self.service.export_session(result["session_id"])
            bundle["profile"]["algorithm_version"] = "norn.scoring.v999"
            source.write_text(json.dumps(bundle))
            with redirect_stderr(io.StringIO()):
                self.assertEqual(main(["replay", str(source)]), 1)


class ReplayHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server(port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, method, path, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            payload = None if body is None else json.dumps(body)
            connection.request(method, path, payload, {"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read()), dict(response.getheaders())
        finally:
            connection.close()

    def test_http_export_is_a_replayable_download(self):
        status, result, _ = self.request("POST", "/api/analyze", {"journal": journal()})
        self.assertEqual(status, 200)
        status, bundle, headers = self.request("GET", f"/api/sessions/{result['session_id']}/export")
        self.assertEqual(status, 200)
        self.assertIn("attachment;", headers["Content-Disposition"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        with patch("norn.providers.generate", side_effect=AssertionError("replay called provider")):
            status, replayed, _ = self.request("POST", "/api/replay", bundle)
        self.assertEqual(status, 200)
        self.assertTrue(replayed["verified"])
        self.assertEqual(replayed["profile"]["profile_hash"], result["profile_hash"])
        bundle["profile"]["analysis"]["attribution"]["selections"][0]["card_id"] = "NORN_0256"
        status, _, _ = self.request("POST", "/api/replay", bundle)
        self.assertEqual(status, 400)

    def test_missing_or_expired_session_does_not_export(self):
        status, _, _ = self.request("GET", "/api/sessions/nonexistent/export")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()

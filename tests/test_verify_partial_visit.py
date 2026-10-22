"""Safety boundaries and cleanup of the zero-model hosted partial-visit check."""
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from norn.actor_state import new_actor_service
from norn.collection import CONSENT_VERSION, SQLiteCollector
from norn.core import engine_module
from norn.service import RequestError
from norn.events import replay_events
from scripts.verify_partial_visit import (BASE, Client, VerificationError, _recovery_file,
                                         active_checkpoint, assert_empty_analysis, journal_prefix, main,
                                         run_check, seal_empty, trusted_origin)


class ServiceClient:
    base = "http://127.0.0.1:18878"

    def __init__(self, directory, *, lose_write=False, refuse_cleanup=False):
        self.store = SQLiteCollector(Path(directory) / "records.sqlite3")
        self.service = new_actor_service(clock=lambda: 1000, deck="pamela-v1")
        self.service.collection = self.store
        self.calls, self.visitor, self.partial_bodies, self.analysis_bodies = [], None, [], []
        self.lose_write, self.refuse_cleanup = lose_write, refuse_cleanup

    def request(self, path, body=None, headers=None):
        route = urlsplit(path).path
        self.calls.append({"method": "POST" if body is not None else "GET", "route": route})
        if route == "/api/health":
            result = {"status": "ok"}
        elif route == "/runtime-config.js":
            return 200, 'window.__NORN_CONFIG__ = ' + json.dumps(
                {"collect": True, "consentVersion": CONSENT_VERSION, "deck": "pamela-v1"})
        elif route == "/data/decks/pamela-v1.json":
            result = {"deck_version": engine_module("research_deck").load_deck("pamela-v1").version}
        elif route == "/api/archive/export":
            query = parse_qs(urlsplit(path).query)
            rows = self.store.page(query.get("after", [""])[0], int(query["limit"][0]))
            return 200, "".join(json.dumps(row) + "\n" for row in rows)
        else:
            try:
                if body.get("visitor"):
                    self.visitor = body["visitor"]
                if route == "/api/record/partial":
                    self.partial_bodies.append(json.loads(json.dumps(body)))
                    result = self.service.record_partial(body)
                    if self.lose_write:
                        self.lose_write = False
                        raise OSError("reply lost after commit")
                elif route == "/api/record/withdraw":
                    if self.refuse_cleanup:
                        raise OSError("cleanup unknown")
                    result = self.service.withdraw_record(body)
                elif route == "/api/analyze":
                    assert_empty_analysis(body)
                    self.analysis_bodies.append(json.loads(json.dumps(body)))
                    result = self.service.analyze(body)
                else:
                    raise AssertionError("Unexpected request")
            except RequestError as exc:
                return exc.status, json.dumps({"error": "invalid request"})
        return 200, json.dumps(result)


class PartialVisitVerifierTests(unittest.TestCase):
    def test_origin_is_restricted_and_invalid_origin_never_reads_token(self):
        self.assertEqual(trusted_origin(BASE + "/"), BASE)
        self.assertEqual(trusted_origin("http://127.0.0.1:8765/"), "http://127.0.0.1:8765")
        for value in ("https://evil.example", BASE + "/api", BASE + "?x=1", "http://localhost.evil.example",
                      "https://user:password@127.0.0.1", "http://127.0.0.1/#x"):
            with self.subTest(value=value), self.assertRaises(VerificationError):
                trusted_origin(value)
        with tempfile.TemporaryDirectory() as directory, \
                patch("scripts.verify_partial_visit.TOKEN_FILE") as token, redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--base", "https://evil.example", "--output", str(Path(directory) / "result.json")]), 1)
            token.read_text.assert_not_called()

    def test_existing_report_cannot_be_overwritten_or_read_the_operator_token(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch("scripts.verify_partial_visit.TOKEN_FILE") as token, redirect_stderr(io.StringIO()):
            output = Path(directory) / "existing.json"
            output.write_text("historical report", encoding="utf-8")
            with self.assertRaises(SystemExit) as failure:
                main(["--output", str(output)])
            self.assertEqual(failure.exception.code, 2)
            self.assertEqual(output.read_text(), "historical report")
            token.read_text.assert_not_called()

    def test_paid_routes_and_nonempty_analysis_fail_before_transport(self):
        client = Client(BASE)
        with patch("scripts.verify_partial_visit.http.client.HTTPSConnection", side_effect=AssertionError("transport")) as transport:
            for path, body in (("/api/dialogue", {}), ("/api/analyze", {}),
                               ("/api/analyze", {"journal": {"schema_version": "norn.events.v5", "events": [
                                   {"type": "card_selected"}, {"type": "session_completed"}]}}),
                               ("https://evil.example/api/record/withdraw", {})):
                with self.subTest(path=path), self.assertRaises(VerificationError):
                    client.request(path, body)
            transport.assert_not_called()

    def test_complete_check_uses_empty_reading_and_removes_all_test_content(self):
        with tempfile.TemporaryDirectory() as directory:
            client = ServiceClient(directory)
            self.addCleanup(client.store.close)
            with patch("norn.service.NornService._render", side_effect=AssertionError("Must not call a model")):
                report = run_check(client, "test-operator-token")
            self.assertTrue(report["passed"], report)
            self.assertEqual(report["model_calls"], 0)
            self.assertEqual(client.store.page(), [])
            self.assertNotIn(client.visitor, json.dumps(report))
            self.assertNotIn("test-operator-token", json.dumps(report))
            self.assertEqual(sum(call["route"] == "/api/analyze" for call in client.calls), 1)
            for check in ("open_round_choices_stored", "open_round_selection_and_undo_preserved",
                          "same_prefix_attention_advances", "stale_attention_rejected",
                          "stale_prefix_cannot_erase_active_choices", "latest_open_round_attention_retained",
                          "full_upgrade_keeps_visit_number", "first_snapshot_feedback_on_without_exposure",
                          "feedback_exposure_advances_with_prefix", "feedback_mode_flip_rejected",
                          "missing_experience_cannot_erase", "feedback_exposure_erasure_rejected",
                          "latest_partial_feedback_exposure_retained", "on_off_visits_distinct_in_archive",
                          "attention_upgrade_keeps_feedback_modes", "full_upgrade_preserves_feedback_exposure"):
                self.assertTrue(report["checks"][check], check)
            active = next(body for body in client.partial_bodies if "open_round" in body)
            self.assertEqual([event["type"] for event in active["journal"]["events"]][-4:],
                             ["round_ready", "card_selected", "card_deselected", "card_selected"])
            self.assertEqual(active["open_round"]["elapsed_ms"], 700)
            self.assertEqual(report["schema_version"], "norn.partial-visit-check.v4")
            self.assertEqual(report["experience_schema_version"], "norn.experience.v1")
            first = next(body for body in client.partial_bodies if "consent" in body)
            self.assertEqual(first["experience"], {"schema_version": "norn.experience.v1",
                             "round_feedback": "on", "exposures": []})
            advanced = next(body for body in client.partial_bodies if body.get("experience", {}).get("exposures"))
            expected = {"schema_version": "norn.experience.v1", "round_feedback": "on",
                        "exposures": [{"after_round": 1, "feedback": "blank", "elapsed_ms": 5000}]}
            self.assertEqual(advanced["experience"], expected)
            self.assertEqual(client.analysis_bodies[0]["experience"], expected,
                             "Sealing the second round must not invent a second painted hint")
            self.assertTrue(any(body.get("experience") == {"schema_version": "norn.experience.v1",
                                "round_feedback": "off", "exposures": []}
                                and body["session_key"] != first["session_key"] for body in client.partial_bodies))

    def test_receipt_without_archived_experience_fails_and_cleans_up(self):
        class MissingExperienceClient(ServiceClient):
            def request(self, path, body=None, headers=None):
                if path == "/api/record/partial" and body and "experience" in body:
                    body = {name: value for name, value in body.items() if name != "experience"}
                return super().request(path, body, headers)

        with tempfile.TemporaryDirectory() as directory:
            client = MissingExperienceClient(directory)
            self.addCleanup(client.store.close)
            with patch("norn.service.NornService._render", side_effect=AssertionError("Must not call a model")):
                report = run_check(client, "test-operator-token")
            self.assertFalse(report["passed"])
            self.assertTrue(report["checks"]["first_snapshot_stored"])
            self.assertFalse(report["checks"]["first_snapshot_feedback_on_without_exposure"])
            self.assertTrue(report["checks"]["cleanup_confirmed_no_test_rows"])
            self.assertEqual(client.store.page(), [])

    def test_empty_analysis_guard_allows_only_valid_experience_and_still_rejects_choices(self):
        import random
        deck = engine_module("research_deck").load_deck("pamela-v1")
        journal = seal_empty(journal_prefix(deck, 1, random.Random(1)))
        experience = {"schema_version": "norn.experience.v1", "round_feedback": "on",
                      "exposures": [{"after_round": 1, "feedback": "blank", "elapsed_ms": 5000}]}
        assert_empty_analysis({"journal": journal, "experience": experience})
        for invalid in (None, {**experience, "round_feedback": "off"},
                        {**experience, "exposures": [{"after_round": 3, "feedback": "blank", "elapsed_ms": 5000}]}):
            with self.subTest(experience=invalid), self.assertRaises(VerificationError):
                assert_empty_analysis({"journal": journal, "experience": invalid})
        chosen = json.loads(json.dumps(journal))
        chosen["events"].insert(-1, {"type": "card_selected", "round": 2, "card_id": deck.ids[0]})
        with self.assertRaises(VerificationError):
            assert_empty_analysis({"journal": chosen, "experience": experience})

    def test_active_checkpoint_is_valid_but_can_never_use_the_analysis_route(self):
        import random
        from norn.collection import validate_open_round
        deck = engine_module("research_deck").load_deck("pamela-v1")
        prefix = journal_prefix(deck, 0, random.Random(1))
        active, earlier, later = active_checkpoint(prefix)
        replay = replay_events(active, cards=deck.cards, require_complete=False)
        self.assertFalse(replay["complete"])
        self.assertEqual(validate_open_round(earlier, replay["journal"]), earlier)
        self.assertEqual(validate_open_round(later, replay["journal"]), later)
        self.assertLess(earlier["elapsed_ms"], later["elapsed_ms"])
        self.assertLess(earlier["hover"][-1][-1], later["hover"][-1][-1])
        self.assertEqual(prefix["events"][-1]["type"], "round_started")
        with self.assertRaises(VerificationError):
            assert_empty_analysis({"journal": active})
        with self.assertRaises(VerificationError):
            seal_empty(active)

    def test_success_receipt_without_archived_open_attention_fails_and_cleans_up(self):
        class MissingAttentionClient(ServiceClient):
            def request(self, path, body=None, headers=None):
                if path == "/api/record/partial" and body and "open_round" in body:
                    body = {key: value for key, value in body.items() if key != "open_round"}
                return super().request(path, body, headers)

        with tempfile.TemporaryDirectory() as directory:
            client = MissingAttentionClient(directory)
            self.addCleanup(client.store.close)
            with patch("norn.service.NornService._render", side_effect=AssertionError("Must not call a model")):
                report = run_check(client, "test-operator-token")
            self.assertFalse(report["passed"])
            self.assertTrue(report["checks"]["open_round_choices_stored"])
            self.assertFalse(report["checks"]["open_round_selection_and_undo_preserved"])
            self.assertTrue(report["checks"]["cleanup_confirmed_no_test_rows"])
            self.assertEqual(client.store.page(), [])

    def test_seal_empty_preserves_existing_ready_event_without_duplicating_it(self):
        import random
        deck = engine_module("research_deck").load_deck("pamela-v1")
        prefix = journal_prefix(deck, 1, random.Random(1))
        active, _, _ = active_checkpoint(prefix)
        ready_prefix = {**active, "events": active["events"][:-3]}
        sealed = seal_empty(ready_prefix)
        self.assertEqual(sum(event["type"] == "round_ready" and event["round"] == 2
                             for event in sealed["events"]), 1)
        self.assertEqual(sealed["events"][:len(ready_prefix["events"])], ready_prefix["events"])
        self.assertTrue(replay_events(sealed, cards=deck.cards)["complete"])
        self.assertEqual(ready_prefix["events"][-1]["type"], "round_ready")

    def test_lost_write_still_cleans_up_and_failed_cleanup_keeps_private_capability(self):
        with tempfile.TemporaryDirectory() as directory:
            client = ServiceClient(directory, lose_write=True)
            self.addCleanup(client.store.close)
            report = run_check(client, "test-token")
            self.assertFalse(report["passed"])
            self.assertTrue(report["checks"]["cleanup_confirmed_no_test_rows"])
            self.assertEqual(client.store.page(), [])
        with tempfile.TemporaryDirectory() as directory:
            client = ServiceClient(directory, lose_write=True, refuse_cleanup=True)
            self.addCleanup(client.store.close)
            path = Path(directory) / "private.json"
            with patch("scripts.verify_partial_visit._recovery_file", return_value=path), redirect_stderr(io.StringIO()):
                path.write_text("private recovery capability")
                report = run_check(client, "test-token")
            self.assertFalse(report["checks"]["cleanup_confirmed_no_test_rows"])
            self.assertTrue(path.exists())
            self.assertNotIn(client.visitor, json.dumps(report))
            self.assertNotIn(str(path), json.dumps(report))

    def test_recovery_file_is_private_and_contains_no_operator_credential(self):
        key = "test-random-visitor-000000001"
        path = _recovery_file(BASE, key)
        self.addCleanup(path.unlink, missing_ok=True)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(json.loads(path.read_text()), {"base": BASE, "visitor": key, "all": True})
        self.assertEqual(path.parent.resolve(), Path("/tmp").resolve())


if __name__ == "__main__":
    unittest.main()

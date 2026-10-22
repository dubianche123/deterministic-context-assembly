"""Release-check spending bounds and a real service/archive integration.

Only provider boundaries are fixtures: Jev uses its actual question parser and
receipt store, Norn uses actual analysis/operation stores and SQLite collection.
No network connection or paid model is used by these tests.
"""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

from norn.collection import SQLiteCollector
from norn.core import engine_module
from norn.events import replay_events
from norn.jev_live import LocalJev
from norn.service import NornService, RequestError
from scripts.verify_last_card import BASE, Client, VerificationError, fixture, run_check
from test_jev_live import Transport


class ServiceClient:
    """Exercise the shared host handlers while replacing only I/O boundaries."""
    base = "http://127.0.0.1:18879"

    def __init__(self, directory, *, live=False, corrupt_receipt=False):
        self.live, self.corrupt_receipt = live, corrupt_receipt
        self.store = SQLiteCollector(Path(directory) / "records.sqlite3")
        self.generated, self.calls = [], []
        self.service = NornService(provider="openai-compatible" if live else "mock",
            config={"base_url": "http://127.0.0.1:1/v1", "model": "fixture-model", "api_key": "fixture-only"},
            deck="pamela-v1", collector=self.store, generate=self.generate)
        self.transport = Transport()
        self.jev = LocalJev(self.service.deck, "fixture-private-key" if live else "", transport=self.transport)
        self.visitor = None

    def close(self):
        self.jev.connection.close()
        self.store.close()

    def generate(self, provider, prompt, **kwargs):
        if not self.live:
            raise AssertionError("The default release check must never render a model response")
        self.generated.append({"task": kwargs.get("task"), "prompt": deepcopy(prompt)})
        return {"text": "你选择了安静的光，也留下继续探索的余地。", "status": "ok", "blocked": False,
                "provider": provider, "simulated": False, "provider_calls": 1,
                "model": "fixture-model", "usage": {"input_tokens": 30, "output_tokens": 20}}

    def request(self, path, body=None, headers=None):
        route = urlsplit(path).path
        if body and body.get("visitor"):
            self.visitor = body["visitor"]
        try:
            if route == "/api/health":
                result = self.service.health()
            elif route == "/api/archive/export":
                query = parse_qs(urlsplit(path).query)
                rows = self.store.page(query.get("after", [""])[0], int(query["limit"][0]))
                self.calls.append({"route": route, "status": 200})
                return 200, "".join(json.dumps(row) + "\n" for row in rows)
            elif route == "/api/jev/start":
                result = self.jev.start(body)
            elif route == "/api/jev/finale":
                plan = self.service.finale_jev_plan(body)
                result = plan if plan.get("cached") else self.service.finale_settle_jev(
                    plan["challenge_id"], self.jev.predict(plan["body"]))
            else:
                handler = {"/api/record/partial": self.service.record_partial,
                           "/api/record/withdraw": self.service.withdraw_record,
                           "/api/finale/start": self.service.finale_start,
                           "/api/finale/choose": self.service.finale_choose,
                           "/api/analyze": self.service.analyze,
                           "/api/dialogue": self.service.dialogue}.get(route)
                if handler is None:
                    raise AssertionError("Unexpected verifier route")
                result = handler(body)
                if route == "/api/finale/choose" and self.corrupt_receipt:
                    result = {**result, "commitment": "sha256:changed"}
            status = 200
        except RequestError as exc:
            status, result = exc.status, {"error": "invalid test request"}
        self.calls.append({"route": route, "status": status})
        return status, json.dumps(result)


class LastCardVerifierTests(unittest.TestCase):
    def test_fixture_is_replayable_and_uses_four_distinct_unseen_choices_per_round(self):
        deck = engine_module("research_deck").load_deck("pamela-v1")
        journal = fixture(deck)
        self.assertEqual(journal, fixture(deck), "Release checks use the same declared synthetic fixture")
        replay = replay_events(journal, cards=deck.cards)
        self.assertTrue(replay["complete"])
        starts = [event for event in journal["events"] if event["type"] == "round_started"]
        self.assertEqual(len(starts), 4)
        self.assertTrue(all(len(event["card_ids"]) == len(set(event["card_ids"])) == 4 for event in starts))
        self.assertEqual(len({card for event in starts for card in event["card_ids"]}), 16)
        self.assertEqual(len([event for event in journal["events"] if event["type"] == "card_selected"]), 4)
        self.assertEqual(journal["events"][-1]["type"], "session_completed")

    def test_default_client_blocks_all_paid_routes_before_transport(self):
        client = Client(BASE)
        with patch("scripts.verify_last_card.http.client.HTTPSConnection") as network:
            for path in ("/api/jev/start", "/api/jev/finale", "/api/analyze", "/api/dialogue"):
                with self.subTest(path=path), self.assertRaises(VerificationError):
                    client.request(path, {"request_id": "fixture"})
            for path in ("https://evil.example/api/finale/start", "/api/finale/start?extra=1"):
                with self.subTest(path=path), self.assertRaises(VerificationError):
                    client.request(path, {})
            network.assert_not_called()

    def test_live_client_allows_only_two_identical_dispatches_of_each_paid_operation(self):
        for path in ("/api/jev/finale", "/api/analyze", "/api/dialogue"):
            with self.subTest(path=path):
                response = MagicMock(status=200)
                response.getheader.return_value = None
                response.read.return_value = b"{}"
                connection = MagicMock()
                connection.getresponse.return_value = response
                with patch("scripts.verify_last_card.http.client.HTTPSConnection", return_value=connection) as network:
                    client = Client(BASE, live=True)
                    body = {"request_id": "same-operation", "input": {"b": 2, "a": 1}}
                    self.assertEqual(client.request(path, body), (200, "{}"))
                    with self.assertRaises(VerificationError):
                        client.request(path, {**body, "input": {"a": 1, "b": 3}})
                    self.assertEqual(network.call_count, 1, "Changed input must be rejected before any dispatch")
                    self.assertEqual(client.request(path, {"input": {"a": 1, "b": 2}, "request_id": "same-operation"}), (200, "{}"))
                    with self.assertRaises(VerificationError):
                        client.request(path, body)
                    self.assertEqual(network.call_count, 2, "Receipt retries have a hard transport bound")

    def test_zero_model_run_checks_archive_receipts_and_withdraws_synthetic_visitor(self):
        with tempfile.TemporaryDirectory() as directory:
            client = ServiceClient(directory)
            self.addCleanup(client.close)
            recovery = Path(directory) / "cleanup.json"
            recovery.write_text("private fixture capability")
            with patch("scripts.verify_last_card._recovery_file", return_value=recovery), \
                    patch("norn.service.NornService._render", side_effect=AssertionError("No rendering in the free check")):
                report = run_check(client, "operator-token-sentinel")
            self.assertTrue(report["passed"], report)
            self.assertEqual(report["provider_calls"], {"jev": 0, "reading": 0, "followup": 0})
            self.assertEqual(client.generated, [])
            self.assertEqual(client.transport.calls, [])
            self.assertTrue(report["checks"]["test_visitor_withdrawn"])
            self.assertEqual(client.store.page(), [])
            self.assertFalse(recovery.exists())
            self.assertFalse({"/api/analyze", "/api/dialogue", "/api/jev/finale"} & {call["route"] for call in client.calls})
            self.assertNotIn(client.visitor, json.dumps(report))
            self.assertNotIn("operator-token-sentinel", json.dumps(report))

    def test_live_run_counts_one_real_fixture_dispatch_per_provider_despite_receipt_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            client = ServiceClient(directory, live=True)
            self.addCleanup(client.close)
            report = run_check(client, "operator-token-sentinel")
            self.assertTrue(report["passed"], report)
            self.assertEqual(report["provider_calls"], {"jev": 1, "reading": 1, "followup": 1})
            self.assertEqual(len(client.transport.calls), 1)
            self.assertEqual(len(client.generated), 2, "The identical analyze retry reuses its stored receipt")
            self.assertEqual(sum(call["route"] == "/api/analyze" for call in client.calls), 2)
            for check in ("jev_preentry_capability", "jev_ready_without_answer_leak", "reading_retry_no_new_call",
                          "reading_preserves_seal", "live_followup", "full_record_replaces_partial"):
                self.assertTrue(report["checks"][check])
            self.assertEqual(client.store.page(), [])
            self.assertNotIn("fixture-private-key", json.dumps(report))

    def test_invalid_commitment_fails_the_report_but_still_removes_the_test_visitor(self):
        with tempfile.TemporaryDirectory() as directory:
            client = ServiceClient(directory, corrupt_receipt=True)
            self.addCleanup(client.close)
            report = run_check(client, "operator-token")
            self.assertFalse(report["passed"])
            self.assertFalse(report["checks"]["receipt_opens_original_commitment"])
            self.assertTrue(report["checks"]["test_visitor_withdrawn"])
            self.assertEqual(client.store.page(), [])
            self.assertEqual(client.generated, [])
            self.assertEqual(client.transport.calls, [])


if __name__ == "__main__":
    unittest.main()

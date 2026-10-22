"""Opt-in play Jev: pre-choice input, durable bounded calls and no secret leaks."""

import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from norn import jev_live
from norn.service import RequestError
from tests.test_partial_records import prefix
from tests.test_research_deck import session, research

DECK = research.load_deck("pamela-v1")
KEY = "private-test-jev-secret"


def start_body(client="a" * 32):
    return {"enabled": True, "mode": "play", "deck": DECK.label, "client_session": client}


class Transport:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.stop_only = False

    async def __call__(self, url, raw, secret, timeout):
        self.calls.append((url, json.loads(raw), secret, timeout))
        if self.fail:
            raise RuntimeError("provider reflected a private secret")
        request = json.loads(raw)
        answers = {}
        for key, question in request["questions"].items():
            criteria = list(question["criteria"])
            probabilities = {item: (float(item == "__STOP__") if self.stop_only else 1 / len(criteria))
                             for item in criteria}
            answers[key] = {"type": "choice", "choice": max(probabilities, key=probabilities.get),
                            "confidence": .5, "probabilities": probabilities}
        response = {"model": jev_live.DEFAULT_MODEL, "answers": answers,
                    "usage": {"input_tokens": 120, "output_tokens": 10, "private": secret}}
        return 200, {}, json.dumps(response).encode()


class JevLiveTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 1000
        self.transport = Transport()
        self.live = jev_live.LocalJev(DECK, KEY, transport=self.transport, clock=lambda: self.now)
        self.addCleanup(self.live.connection.close)
        self.journal = session(research.RANDOM, rounds=10, seed=17)
        self.ticket = self.live.start(start_body())["jev_session"]

    async def predict(self, round=1, ticket=None, journal=None):
        return await jev_live.predict(self.live.store,
            {"jev_session": ticket or self.ticket, "journal": journal or prefix(self.journal, round - 1)},
            DECK, KEY, actor_id="browser-test", quota=self.live.quota, transport=self.transport)

    async def test_default_disabled_has_no_dispatch_or_capability(self):
        disabled = jev_live.LocalJev(DECK, transport=self.transport)
        self.addCleanup(disabled.connection.close)
        self.assertFalse(disabled.status()["available"])
        self.assertEqual(disabled.start(start_body())["status"], "unavailable")
        self.assertEqual(disabled.connection.execute("SELECT COUNT(*) FROM norn_jev_visits").fetchone()[0], 0)
        self.assertEqual(self.transport.calls, [])

    async def test_optin_is_play_only_and_bound_to_first_round(self):
        for fields in ({"enabled": False}, {"mode": "research"}, {"visitor": "must-not-be-accepted"},
                       {"deck": "old-deck"}, {"client_session": "invalid"}):
            with self.subTest(fields=fields), self.assertRaises(RequestError):
                self.live.start({**start_body(), **fields})
        with self.assertRaises(RequestError) as late:
            await self.predict(2)
        self.assertEqual(late.exception.status, 409)
        with self.assertRaises(RequestError):
            await jev_live.predict(self.live.store, {"jev_session": self.ticket,
                "journal": prefix(self.journal, 0), "consent": "must-not-be-accepted"}, DECK, KEY,
                actor_id="test", quota=self.live.quota, transport=self.transport)
        self.assertEqual(self.transport.calls, [])

    async def test_precommit_only_numeric_past_and_cards_conditioned_probabilities(self):
        first = await self.predict()
        self.assertEqual(first["status"], "ok")
        self.assertEqual(first["cutoff_rounds"], 0)
        self.assertEqual(first["candidate_ids"], self.journal["events"][0]["card_ids"])
        self.assertAlmostEqual(sum(first["probabilities"]), 1)
        self.assertTrue(first["wire_stop_conditioned_out"])
        self.assertEqual(first["usage"], {"input_tokens": 120, "output_tokens": 10})
        second = await self.predict(2)
        self.assertEqual(second["cutoff_rounds"], 1)
        url, request, secret, timeout = self.transport.calls[-1]
        self.assertEqual((url, secret, timeout), (jev_live.ENDPOINT, KEY, 15))
        self.assertEqual(len(request["state"]["past_completed_rounds"]), 1)
        wire = json.dumps(request)
        for field in ('"target"', '"duration_ms"', '"t_ms"', '"reading"', '"profile"', 'PAM_'):
            self.assertNotIn(field, wire)
        self.assertNotIn(KEY, json.dumps(second))
        self.assertIsNone(second["target_nll"])
        # A current-round ready/choice/close must never enter its own prediction.
        current = prefix(self.journal, 1)
        position = len(current["events"])
        current["events"].append(deepcopy(self.journal["events"][position]))
        with self.assertRaises(RequestError) as exposed:
            await self.predict(journal=current)
        self.assertEqual(exposed.exception.status, 409)
        self.assertEqual(len(self.transport.calls), 2)

    async def test_cached_response_and_failed_response_never_dispatch_twice(self):
        first = await self.predict()
        retry = await self.predict()
        self.assertEqual((retry["cached"], retry["provider_calls"], retry["original_provider_calls"]), (True, 0, 1))
        self.assertEqual(retry["probabilities"], first["probabilities"])
        self.transport.fail = True
        failed = await self.predict(2)
        self.assertEqual(failed["status"], "failed")
        self.assertNotIn("private", json.dumps(failed))
        self.assertEqual((await self.predict(2))["provider_calls"], 0)
        self.assertEqual(len(self.transport.calls), 2)

    async def test_pending_survives_new_store_and_does_not_retry(self):
        plan = jev_live.prepare(prefix(self.journal, 0), DECK)
        self.live.store.reserve(self.ticket, plan, DECK)
        restored = jev_live.JevStore(self.live.store.sql, self.live.store.transaction_sync, clock=lambda: self.now)
        with self.assertRaises(RequestError) as unknown:
            await jev_live.predict(restored, {"jev_session": self.ticket, "journal": prefix(self.journal, 0)},
                DECK, KEY, actor_id="restored", quota=self.live.quota, transport=self.transport)
        self.assertEqual(unknown.exception.status, 409)
        self.assertEqual(self.transport.calls, [])

    async def test_changed_history_same_round_conflicts_and_actor_budget_is_bounded(self):
        await self.predict()
        altered = deepcopy(prefix(self.journal, 0))
        altered["events"][0]["card_ids"].reverse()
        with self.assertRaises(RequestError):
            await self.predict(journal=altered)
        for round in range(2, 9):
            await self.predict(round)
        with self.assertRaises(RequestError) as limited:
            await self.predict(9)
        self.assertEqual(limited.exception.status, 429)
        new = self.live.start(start_body("b" * 32))["jev_session"]
        with self.assertRaises(RequestError):
            await self.predict(ticket=new)
        self.assertEqual(len(self.transport.calls), 8)

    async def test_inflight_guard_and_expiry_and_quota_denial_do_not_dispatch(self):
        plan = jev_live.prepare(prefix(self.journal, 0), DECK)
        self.live.store.reserve(self.ticket, plan, DECK)
        another = self.live.start(start_body("c" * 32))["jev_session"]
        with self.assertRaises(RequestError) as busy:
            await self.predict(ticket=another)
        self.assertEqual(busy.exception.status, 429)
        self.now += jev_live.TTL
        self.assertIsNone(self.live.store.next_expiry())
        with self.assertRaises(RequestError) as expired:
            await self.predict()
        self.assertEqual(expired.exception.status, 410)
        self.assertEqual(self.transport.calls, [])
        self.ticket = self.live.start(start_body())["jev_session"]
        class Quota:
            async def reserve(inner, attempt):
                return {"allowed": False, "reason": "daily_limit"}
        with self.assertRaises(RequestError):
            await jev_live.predict(self.live.store, {"jev_session": self.ticket, "journal": prefix(self.journal, 0)},
                DECK, KEY, actor_id="test", quota=Quota(), transport=self.transport)
        self.assertEqual(self.live.connection.execute("SELECT COUNT(*) FROM norn_jev_requests").fetchone()[0], 0)
        self.assertEqual((await self.predict())["status"], "ok")

    async def test_zero_card_mass_is_failure_and_stream_is_bounded_and_closed(self):
        self.transport.stop_only = True
        self.assertEqual((await self.predict())["status"], "failed")
        class Body:
            closed = False
            def __aiter__(self):
                return self
            async def __anext__(self):
                return b"x" * 16
            async def aclose(self):
                self.closed = True
        stream = Body()
        async def transport(*args):
            return 200, {}, stream
        with patch.object(jev_live, "MAX_RESPONSE_BYTES", 20):
            with self.assertRaises(jev_live.JevError):
                await jev_live._receive(b"{}", KEY, transport)
        self.assertTrue(stream.closed)


class JevWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_server_routes_stay_disabled_until_explicitly_configured_and_selected(self):
        import os
        import threading
        import urllib.request
        from norn.server import make_server
        with patch.dict(os.environ, {"NORN_JEV_API_KEY": "", "TYPESAFE_API_KEY": ""}):
            server = make_server(port=0, provider="mock", deck="pamela-v1")
        self.addCleanup(server.server_close)
        self.addCleanup(server.jev_live.connection.close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        origin = f"http://127.0.0.1:{server.server_port}"
        def request(path, body=None):
            encoded = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(origin + path, data=encoded,
                headers={"Content-Type": "application/json"} if encoded is not None else {})
            with urllib.request.urlopen(req, timeout=5) as response:
                return json.load(response)
        self.assertFalse(request("/api/jev/status")["available"])
        self.assertEqual(request("/api/jev/start", start_body())["status"], "unavailable")
        fake = Transport()
        server.jev_live = jev_live.LocalJev(DECK, KEY, transport=fake)
        self.addCleanup(server.jev_live.connection.close)
        self.assertTrue(request("/api/jev/status")["available"])
        ticket = request("/api/jev/start", start_body())["jev_session"]
        source = prefix(session(research.RANDOM, rounds=2), 0)
        response = request("/api/jev/predict", {"jev_session": ticket, "journal": source})
        self.assertEqual((response["status"], response["provider_calls"]), ("ok", 1))
        self.assertEqual(len(fake.calls), 1)

    async def test_worker_uses_separate_quota_and_does_not_hold_normal_actor_lock(self):
        from tests.test_cloudflare_entry import ENTRY, Request, Storage, data
        storage = Storage()
        self.addCleanup(storage.connection.close)
        local = jev_live.LocalJev(DECK, KEY)
        self.addCleanup(local.connection.close)
        names = []
        class Quota:
            async def reserve_jev(inner, attempt):
                return await local.quota.reserve(attempt)
            async def release(inner, attempt):
                return await local.quota.release(attempt)
        class Namespace:
            def getByName(inner, name):
                names.append(name)
                return Quota()
        env = SimpleNamespace(NORN_DECK="pamela-v1", NORN_JEV_API_KEY=KEY, NORN_BUDGET=Namespace())
        actor = ENTRY.NornSession(SimpleNamespace(storage=storage,
            id=SimpleNamespace(toString=lambda: "test-browser-actor")), env)
        ready = await actor.handle(Request("/api/jev/start", "POST", start_body()))
        self.assertEqual(ready.status, 200, ready.body)
        ticket = data(ready)["jev_session"]
        entered, finish = asyncio.Event(), asyncio.Event()
        fake = Transport()
        async def slow(*args):
            entered.set()
            await finish.wait()
            return await fake(*args)
        source = prefix(session(research.RANDOM, rounds=2), 0)
        with patch("norn.cloud_provider._worker_transport", slow):
            pending = asyncio.create_task(actor.handle(Request("/api/jev/predict", "POST",
                {"jev_session": ticket, "journal": source})))
            await entered.wait()
            normal = await asyncio.wait_for(actor.handle(Request("/api/trajectory", "POST",
                {"journal": source})), timeout=1)
            self.assertEqual(normal.status, 200, normal.body)
            finish.set()
            result = await pending
        self.assertEqual((result.status, data(result)["status"]), (200, "ok"), result.body)
        self.assertEqual(names, ["jev-play-v1"])
        again = await actor.handle(Request("/api/jev/predict", "POST", {"jev_session": ticket, "journal": source}))
        self.assertEqual(data(again)["provider_calls"], 0)
        self.assertEqual(len(fake.calls), 1)


if __name__ == "__main__":
    unittest.main()

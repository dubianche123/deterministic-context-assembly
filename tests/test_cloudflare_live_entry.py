"""Live-route gateway contracts with real SQLite and recreated Python actors."""

from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from norn.actor_state import (ACTOR_SCHEMA_VERSION, DEEPSEEK_GENERATION_ROUTE,
                              LIVE_ACTOR_SCHEMA_VERSION)
from test_cloudflare_entry import Request, Response, Storage, data, load_entry


ENTRY = load_entry()
SECRET = "sk-live-entry-sentinel-never-expose"


def selection(rounds=24, request_id="reading-1"):
    return {"total_rounds": rounds, "selections": [{"id": "NORN_0001", "round": 1}],
            "round_durations": [{"round": 1, "duration_ms": 1200}],
            "deselection_events": [], "request_id": request_id}


def context(storage, identity):
    return SimpleNamespace(storage=storage, id=SimpleNamespace(toString=lambda: identity))


class SessionNamespace:
    def __init__(self, env):
        self.env = env
        self.storages = {}
        self.names = []
        self.pre_rpc_reads = []

    def getByName(self, token):
        self.names.append(token)
        if token not in self.storages:
            self.storages[token] = Storage()
        storage = self.storages[token]

        async def handle(request):
            self.pre_rpc_reads.append(request.reader.reads)
            actor = ENTRY.NornSession(context(storage, "session-" + token), self.env)
            return await actor.handle(request)

        return SimpleNamespace(handle=handle)


class BudgetNamespace:
    def __init__(self, env):
        self.env = env
        self.storages = {}
        self.names = []

    def getByName(self, name):
        self.names.append(name)

        def actor():
            # Getting a stub does not start the remote DO; only an RPC does.
            if name not in self.storages:
                self.storages[name] = Storage()
            return ENTRY.NornBudget(context(self.storages[name], "budget-" + name), self.env)

        async def reserve(attempt):
            return await actor().reserve(attempt)

        async def release(attempt):
            return await actor().release(attempt)

        return SimpleNamespace(reserve=reserve, release=release)


class CloudflareLiveEntryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from norn.actor_state import new_actor_service
        from norn.cloud_quota import PaidReceipts, QuotaStore
        self.now = 1000
        self.calls = []
        self.upstream_status = "ok"
        self.output = io.StringIO()
        self.capture = redirect_stdout(self.output)
        self.capture.__enter__()
        self.addCleanup(lambda: self.capture.__exit__(None, None, None))
        self.env = SimpleNamespace(NORN_PROVIDER="openai-compatible", NORN_API_KEY=SECRET,
                                   ASSETS=SimpleNamespace(fetch=self.asset_fetch))
        self.sessions = SessionNamespace(self.env)
        self.budgets = BudgetNamespace(self.env)
        self.env.NORN_SESSION, self.env.NORN_BUDGET = self.sessions, self.budgets
        self.gateway = ENTRY.Default(SimpleNamespace(), self.env)
        self.cookie = None
        self.addCleanup(self.close_storages)

        def restore(state=None, **kwargs):
            return new_actor_service(state, **{**kwargs, "clock": lambda: self.now})

        patches = [
            patch("norn.actor_state.new_actor_service", side_effect=restore),
            patch("norn.cloud_quota.PaidReceipts", side_effect=lambda sql, transaction_sync:
                  PaidReceipts(sql, transaction_sync, clock=lambda: self.now)),
            patch("norn.cloud_quota.QuotaStore", side_effect=lambda sql, transaction_sync:
                  QuotaStore(sql, transaction_sync, clock=lambda: self.now)),
            patch("norn.cloud_host.generate_cloud", side_effect=self.generate),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def close_storages(self):
        for namespace in (self.sessions, self.budgets):
            for storage in namespace.storages.values():
                storage.connection.close()

    async def asset_fetch(self, request):
        return Response("asset", headers={"Content-Type": "text/plain"})

    async def generate(self, prompt, *, api_key):
        self.assertEqual(api_key, SECRET)
        self.calls.append(deepcopy(prompt))
        return {"text": "Live fixture answer." if self.upstream_status == "ok" else "在线模型暂时不可用。",
                "status": self.upstream_status, "blocked": False,
                "provider": "openai-compatible", "simulated": False, "provider_calls": 1,
                "model": "deepseek-flash", "usage": {"input_tokens": 120, "output_tokens": 30}}

    async def fetch(self, path, method="GET", body=None, *, headers=None, cookie=True):
        values = {**({"Cookie": self.cookie} if cookie and self.cookie else {}), **(headers or {})}
        response = await self.gateway.fetch(Request(path, method, body, values))
        if cookie and response.headers.get("Set-Cookie"):
            self.cookie = response.headers.get("Set-Cookie").split(";", 1)[0]
        return response

    def actor(self):
        token = self.cookie.split("=", 1)[1]
        return ENTRY.NornSession(context(self.sessions.storages[token], "session-" + token), self.env)

    async def reading(self, payload=None):
        response = await self.fetch("/api/analyze", "POST", payload or selection())
        self.assertEqual(response.status, 200, response.body)
        return data(response)

    async def test_health_and_missing_secret_fail_closed_without_dispatch_or_leaks(self):
        healthy = await self.fetch("/api/health")
        self.assertEqual(healthy.status, 200)
        self.assertEqual(data(healthy)["provider"], "openai-compatible")
        self.assertFalse(data(healthy)["simulated"])
        self.assertFalse(data(healthy)["offline"])
        self.assertEqual(data(healthy)["model"], "deepseek-flash")
        self.assertEqual(self.sessions.names, [])
        self.assertEqual(self.budgets.names, [])
        self.env.NORN_API_KEY = ""
        missing = await self.fetch("/api/health")
        self.assertEqual(missing.status, 503)
        self.assertEqual(data(missing)["status"], "unavailable")
        head = await self.fetch("/api/health", "HEAD")
        self.assertEqual(head.status, 503)
        self.assertIsNone(head.body)
        failure = await self.fetch("/api/analyze", "POST", selection())
        self.assertEqual(failure.status, 503)
        self.assertNotIn("session_id", data(failure))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.budgets.storages, {})
        self.assertIsNone(self.actor()._read_state())
        for response in (healthy, missing, failure):
            self.assertNotIn(SECRET, response.body)
            self.assertNotIn("NORN_API_KEY", response.body)
        self.assertNotIn(SECRET, self.output.getvalue())

    async def test_live_reading_three_turns_retry_inspect_and_export_survive_rpc_recreation(self):
        reading = await self.reading()
        session = reading["session_id"]
        self.assertEqual(reading["provider"], "openai-compatible")
        self.assertFalse(reading["simulated"])
        self.assertEqual(reading["provider_calls"], 1)
        first = data(await self.fetch(f"/api/sessions/{session}/inspect"))
        export = (await self.fetch(f"/api/sessions/{session}/export")).body
        self.assertEqual(data(await self.fetch("/api/analyze", "POST", selection())), reading)
        self.assertEqual(len(self.calls), 1)
        for turn in range(1, 4):
            payload = {"session_id": session, "user_message": f"Question {turn}?", "turn_id": f"turn-{turn}"}
            response = await self.fetch("/api/dialogue", "POST", payload)
            self.assertEqual(response.status, 200, response.body)
            self.assertEqual(data(response)["turn_count"], turn)
            self.assertFalse(data(response)["simulated"])
            self.assertEqual(data(await self.fetch("/api/dialogue", "POST", payload)), data(response))
            self.assertEqual(len(self.calls), 1 + turn)
        final = data(await self.fetch(f"/api/sessions/{session}/inspect?compact=1"))
        self.assertEqual(final["turn_count"], 3)
        self.assertEqual(final["profile_hash"], first["profile_hash"])
        self.assertEqual(final["limits"]["model_calls_reserved"], 4)
        self.assertEqual(final["limits"]["scope"], "browser_actor")
        self.assertEqual((await self.fetch(f"/api/sessions/{session}/export")).body, export)
        state = self.actor()._read_state()
        self.assertEqual(state["schema_version"], LIVE_ACTOR_SCHEMA_VERSION)
        self.assertEqual(state["sessions"][0]["generation_route"], DEEPSEEK_GENERATION_ROUTE)
        self.assertEqual(len(state["sessions"][0]["calls"]), 4)
        self.assertEqual(self.actor().ctx.storage.alarm, 2800_000)
        self.assertEqual(set(self.budgets.names), {"deepseek-public-v1"})
        self.assertTrue(all(reads == 0 for reads in self.sessions.pre_rpc_reads))
        stored = self.actor().ctx.storage.sql.exec("SELECT result_json FROM norn_paid_receipts").toArray()
        self.assertNotIn(SECRET, json.dumps([state, stored, final, self.output.getvalue()]))
        self.assertNotIn("api_key", json.dumps([state, stored, final]))

    async def test_v6_reading_ledger_replay_and_followup_survive_actor_recreation(self):
        from test_research_deck import session, research
        self.env.NORN_DECK = "pamela-v1"
        payload = {"journal": session(research.RANDOM, rounds=4), "request_id": "v6-reading", "language": "en"}
        reading = await self.reading(payload)
        key = reading["session_id"]
        self.assertEqual(reading["algorithm_version"], research.ALGORITHM_VERSION)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(data(await self.fetch("/api/analyze", "POST", payload)), reading)
        self.assertEqual(len(self.calls), 1)
        exported = data(await self.fetch(f"/api/sessions/{key}/export"))
        self.assertEqual(exported["schema_version"], "norn.research-replay.v1")
        ledger = exported["contribution_ledger"]
        self.assertEqual(len(ledger["cards"]), 256)
        state = self.actor()._read_state()
        self.assertEqual(state["sessions"][0]["replay_bundle"], exported)
        self.assertEqual(state["model_calls_reserved"], 1)
        inspected = await self.fetch(f"/api/sessions/{key}/inspect?compact=1")
        self.assertEqual(inspected.status, 200, inspected.body)
        trajectory = await self.fetch(f"/api/sessions/{key}/trajectory")
        self.assertEqual(trajectory.status, 200, trajectory.body)
        self.assertEqual(data(trajectory)["schema_version"], "norn.research-trajectory.v1")
        replayed = await self.fetch("/api/replay", "POST", exported)
        self.assertEqual(replayed.status, 200, replayed.body)
        self.assertTrue(data(replayed)["verified"])
        self.assertEqual(len(self.calls), 1, "Read-only routes must not dispatch a model")
        followup = {"session_id": key, "user_message": "Which choices led to this?", "turn_id": "v6-turn"}
        reply = await self.fetch("/api/dialogue", "POST", followup)
        self.assertEqual(reply.status, 200, reply.body)
        self.assertEqual(data(await self.fetch("/api/dialogue", "POST", followup)), data(reply))
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(data(await self.fetch(f"/api/sessions/{key}/export")), exported)
        self.assertEqual(self.actor()._read_state()["model_calls_reserved"], 2)
        self.assertNotIn(SECRET, json.dumps(self.actor()._read_state()))

    async def test_v6_poisoned_snapshot_ledger_is_rejected_before_new_dispatch(self):
        from test_research_deck import session, research
        self.env.NORN_DECK = "pamela-v1"
        reading = await self.reading({"journal": session(research.RANDOM, rounds=4), "request_id": "v6"})
        clean = self.actor()._read_state()
        for change in ("unknown_field", "contribution"):
            with self.subTest(change=change):
                state = deepcopy(clean)
                bundle = state["sessions"][0]["replay_bundle"]
                if change == "unknown_field":
                    bundle["unexpected"] = True
                else:
                    from norn.events import content_hash
                    ledger = bundle["contribution_ledger"]
                    ledger["cards"][0]["terms"][0] += 1
                    ledger["ledger_hash"] = content_hash({k: v for k, v in ledger.items() if k != "ledger_hash"})
                self.actor()._write_state(state)
                response = await self.fetch("/api/dialogue", "POST", {
                    "session_id": reading["session_id"], "user_message": "Why?", "turn_id": change})
                self.assertEqual(response.status, 503)
                self.assertEqual(len(self.calls), 1)
        self.actor()._write_state(clean)

    async def test_upstream_failure_is_durable_real_failure_without_mock_fallback(self):
        self.upstream_status = "provider_unavailable"
        payload = selection()
        failure = await self.fetch("/api/analyze", "POST", payload)
        self.assertEqual(failure.status, 503)
        failed = data(failure)
        self.assertEqual(failed["status"], "provider_unavailable")
        self.assertEqual(failed["provider"], "openai-compatible")
        self.assertFalse(failed["simulated"])
        self.assertNotIn("session_id", failed)
        self.assertTrue(failed["error"])
        repeated = await self.fetch("/api/analyze", "POST", payload)
        self.assertEqual(repeated.status, 503)
        self.assertEqual(data(repeated), failed)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.actor()._read_state()["model_calls_reserved"], 1)
        self.upstream_status = "ok"
        success = await self.reading(selection(request_id="explicit-new-attempt"))
        self.assertFalse(success["simulated"])
        self.assertEqual(len(self.calls), 2)

    async def test_failed_live_dialogue_does_not_advance_history_and_requires_new_attempt_id(self):
        reading = await self.reading()
        session = reading["session_id"]
        before = self.actor()._read_state()["sessions"][0]["history"]
        self.upstream_status = "provider_unavailable"
        payload = {"session_id": session, "user_message": "Question?", "turn_id": "failed-turn"}
        failed = await self.fetch("/api/dialogue", "POST", payload)
        self.assertEqual(failed.status, 503)
        self.assertEqual(data(failed)["turn_count"], 0)
        self.assertFalse(data(failed)["simulated"])
        self.assertEqual(self.actor()._read_state()["sessions"][0]["history"], before)
        again = await self.fetch("/api/dialogue", "POST", payload)
        self.assertEqual(data(again), data(failed))
        self.assertEqual(len(self.calls), 2)
        self.upstream_status = "ok"
        success = await self.fetch("/api/dialogue", "POST", {**payload, "turn_id": "new-turn"})
        self.assertEqual(success.status, 200)
        self.assertEqual(data(success)["turn_count"], 1)
        self.assertEqual(len(self.calls), 3)

    async def test_preexisting_v1_mock_session_remains_mock_after_live_deployment(self):
        self.env.NORN_PROVIDER = "mock"
        reading = await self.reading()
        self.assertEqual(reading["provider"], "mock")
        self.assertTrue(reading["simulated"])
        self.assertEqual(self.actor()._read_state()["schema_version"], ACTOR_SCHEMA_VERSION)
        session = reading["session_id"]
        self.env.NORN_PROVIDER = "openai-compatible"
        self.env.NORN_API_KEY = ""
        for turn in range(1, 4):
            payload = {"session_id": session, "user_message": "继续看看", "turn_id": f"old-turn-{turn}"}
            response = await self.fetch("/api/dialogue", "POST", payload)
            self.assertEqual(response.status, 200, response.body)
            self.assertEqual(data(response)["provider"], "mock")
            self.assertTrue(data(response)["simulated"])
            self.assertEqual(data(response)["provider_calls"], 0)
            self.assertEqual(data(response)["turn_count"], turn)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.budgets.storages, {})
        self.assertEqual(self.actor()._read_state()["schema_version"], ACTOR_SCHEMA_VERSION)

    async def test_invalid_payloads_and_missing_operation_ids_never_dispatch(self):
        payload = selection()
        invalid = [({**payload, "provider": "mock"}, 400),
                   ({key: value for key, value in payload.items() if key != "request_id"}, 400),
                   ({**payload, "request_id": "contains space"}, 400),
                   ({**payload, "total_rounds": True}, 400),
                   ([], 400), (b'{"request_id":"x","request_id":"y"}', 400)]
        for body, expected in invalid:
            with self.subTest(body=body):
                response = await self.fetch("/api/analyze", "POST", body)
                self.assertEqual(response.status, expected, response.body)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.budgets.storages, {})
        reading = await self.reading()
        calls = len(self.calls)
        for extra in ({}, {"turn_id": ""}, {"turn_id": "bad space"}, {"turn_id": "turn", "provider": "mock"}):
            response = await self.fetch("/api/dialogue", "POST", {
                "session_id": reading["session_id"], "user_message": "Question?", **extra})
            self.assertEqual(response.status, 400, response.body)
        self.assertEqual(len(self.calls), calls)

    async def test_shared_budget_is_one_sqlite_atom_across_distinct_cookie_actors(self):
        budget = self.budgets.getByName("deepseek-public-v1")
        for index in range(98):
            attempt = f"preexisting-{index}"
            self.assertTrue(json.loads(await budget.reserve(attempt))["allowed"])
            await budget.release(attempt)
        cookies = []
        for index in range(2):
            self.cookie = None
            reading = await self.reading(selection(1))
            self.assertFalse(reading["simulated"])
            cookies.append(self.cookie)
        self.assertNotEqual(cookies[0], cookies[1])
        self.cookie = None
        denied = await self.fetch("/api/analyze", "POST", selection(1))
        self.assertEqual(denied.status, 429)
        self.assertEqual(len(self.calls), 2)
        self.assertIsNone(self.actor()._read_state())
        self.assertEqual(self.actor().ctx.storage.sql.exec("SELECT COUNT(*) AS count FROM norn_paid_receipts").toArray(),
                         [{"count": 0}])
        self.assertEqual(len(self.budgets.storages), 1)
        storage = self.budgets.storages["deepseek-public-v1"]
        self.assertEqual(storage.sql.exec("SELECT used FROM norn_budget_days").toArray(), [{"used": 100}])
        for cookie in cookies:
            token = cookie.split("=", 1)[1]
            self.assertNotIn(token, denied.body)

    async def test_settlement_write_failure_returns_safe_error_and_retry_never_dispatches_twice(self):
        original = ENTRY.NornSession._write_state

        def fail_final_snapshot(actor, state, *, atomic=True):
            original(actor, state, atomic=atomic)
            if not atomic:
                raise RuntimeError("Storage failure containing " + SECRET)

        with patch.object(ENTRY.NornSession, "_write_state", fail_final_snapshot):
            failure = await self.fetch("/api/analyze", "POST", selection())
        self.assertEqual(failure.status, 503)
        self.assertNotIn(SECRET, failure.body)
        self.assertEqual(len(self.calls), 1)
        state = self.actor()._read_state()
        self.assertEqual(state["model_calls_reserved"], 1)
        self.assertEqual(state["sessions"], [])
        rows = self.actor().ctx.storage.sql.exec("SELECT state, result_json FROM norn_paid_receipts").toArray()
        self.assertEqual(rows, [{"state": "pending", "result_json": None}])
        again = await self.fetch("/api/analyze", "POST", selection())
        self.assertEqual(again.status, 409)
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn(SECRET, self.output.getvalue())


if __name__ == "__main__":
    unittest.main()

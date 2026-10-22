"""Real SQLite paid-host tests, recreating service state before each request.

Only outbound generation and Durable Object RPC are asynchronous fakes. Session
snapshots, receipts, quota reservations, and failure rollbacks use actual SQLite.
"""

from copy import deepcopy
import json
import sqlite3
from types import SimpleNamespace
import unittest

from norn.actor_state import (DEEPSEEK_GENERATION_ROUTE, LIVE_ACTOR_SCHEMA_VERSION,
                              MAX_LIVE_MODEL_CALLS, new_actor_service)
from norn.cloud_host import HostedGenerator
from norn.cloud_quota import PaidReceipts, QuotaStore
from norn.events import content_hash
from norn.service import RequestError


SECRET = "sk-fixture-secret-must-never-be-persisted"


def selection(rounds=24, card="NORN_0001", request_id="reading-1"):
    return {"total_rounds": rounds, "selections": [{"id": card, "round": 1}],
            "round_durations": [{"round": 1, "duration_ms": 1200}],
            "deselection_events": [], "request_id": request_id}


class SQL:
    def __init__(self, connection):
        self.connection = connection
        self.fail_prefix = None

    def exec(self, query, *bindings):
        if self.fail_prefix and query.startswith(self.fail_prefix):
            raise RuntimeError("Injected receipt write failure")
        cursor = self.connection.execute(query, bindings)
        names = [column[0] for column in cursor.description or []]
        rows = [dict(zip(names, row)) for row in cursor.fetchall()]
        return SimpleNamespace(toArray=lambda: rows)


class Storage:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:", isolation_level=None)
        self.sql = SQL(self.connection)
        self.transaction_active = False

    def transaction_sync(self, callback):
        if self.transaction_active:
            raise AssertionError("Snapshot writes must reuse the receipt transaction")
        self.transaction_active = True
        self.connection.execute("BEGIN")
        try:
            result = callback()
            self.connection.commit()
            return result
        except BaseException:
            self.connection.rollback()
            raise
        finally:
            self.transaction_active = False

    def rows(self, query, *bindings):
        return self.connection.execute(query, bindings).fetchall()


class AsyncQuota:
    """A short RPC adapter whose only surviving state is shared SQLite."""
    def __init__(self, storage, clock):
        self.storage, self.clock = storage, clock
        self.reservations = []
        self.releases = []
        self.ambiguous_reservation = False
        self.string_results = True

    def store(self):
        return QuotaStore(self.storage.sql, self.storage.transaction_sync, clock=self.clock)

    async def reserve(self, attempt):
        self.reservations.append(attempt)
        result = self.store().reserve(attempt)
        if self.ambiguous_reservation:
            raise RuntimeError("Quota RPC disconnected after reservation")
        return json.dumps(result) if self.string_results else result

    async def release(self, attempt):
        self.releases.append(attempt)
        result = self.store().release(attempt)
        return json.dumps(result) if self.string_results else result


class AsyncRenderer:
    def __init__(self):
        self.calls = []
        self.before_return = None
        self.status = "ok"

    async def __call__(self, prompt, *, api_key):
        if api_key != SECRET:
            raise AssertionError("Credential should only reach the transport")
        self.calls.append(deepcopy(prompt))
        if self.before_return:
            self.before_return()
        return {"text": f"Fixture answer {len(self.calls)}.", "status": self.status,
                "blocked": False, "provider": "openai-compatible", "simulated": False,
                "provider_calls": 1, "model": "deepseek-flash",
                "usage": {"input_tokens": 120, "output_tokens": 20}}


class Browser:
    def __init__(self, storage, quota, renderer, *, actor_id="actor-one", clock=lambda: 1000):
        self.storage, self.quota, self.renderer = storage, quota, renderer
        self.actor_id, self.clock = actor_id, clock
        self.schedule_count = 0
        self.snapshot_modes = []
        self.fail_snapshot_mode = None
        self.storage.sql.exec("""CREATE TABLE test_actor_snapshot (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1), body TEXT NOT NULL)""")

    def snapshot(self):
        rows = self.storage.rows("SELECT body FROM test_actor_snapshot WHERE singleton = 1")
        return json.loads(rows[0][0]) if rows else None

    def write_snapshot(self, state, *, atomic=True):
        self.snapshot_modes.append(atomic)

        def write():
            self.storage.sql.exec("""INSERT INTO test_actor_snapshot (singleton, body) VALUES (1, ?)
                ON CONFLICT(singleton) DO UPDATE SET body = excluded.body""",
                json.dumps(state, ensure_ascii=False, allow_nan=False))
            if self.fail_snapshot_mode is atomic:
                raise RuntimeError("Injected snapshot failure")

        if atomic:
            return self.storage.transaction_sync(write)
        if not self.storage.transaction_active:
            raise AssertionError("Non-atomic snapshot must be inside receipt transaction")
        return write()

    async def schedule(self, service):
        self.schedule_count += 1

    def receipts(self):
        return PaidReceipts(self.storage.sql, self.storage.transaction_sync, clock=self.clock)

    def host(self, *, api_key=SECRET):
        # Every request forgets Python objects and restores only committed JSON.
        service = new_actor_service(self.snapshot(), provider="openai-compatible", clock=self.clock,
                                    config={"base_url": "https://api.deepseek.com/v1",
                                            "model": "deepseek-flash", "api_key": ""})
        return HostedGenerator(service, self.receipts(), self.quota,
                               actor_id=self.actor_id, api_key=api_key,
                               write_snapshot=self.write_snapshot, schedule=self.schedule,
                               generate=self.renderer)


class HostedGeneratorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 1000
        self.storages = []
        self.quota_storage = self.storage()
        self.quota = AsyncQuota(self.quota_storage, lambda: self.now)
        self.renderer = AsyncRenderer()
        self.browser = self.new_browser()

    def tearDown(self):
        for storage in self.storages:
            storage.connection.close()

    def storage(self):
        storage = Storage()
        self.storages.append(storage)
        return storage

    def new_browser(self, actor_id="actor-one"):
        return Browser(self.storage(), self.quota, self.renderer,
                       actor_id=actor_id, clock=lambda: self.now)

    async def assert_request_error(self, status, awaitable):
        with self.assertRaises(RequestError) as caught:
            await awaitable
        self.assertEqual(caught.exception.status, status)
        return str(caught.exception)

    def fingerprint(self, body):
        return content_hash({key: value for key, value in body.items() if key != "request_id"})

    async def test_reading_and_three_turns_survive_every_object_recreation(self):
        payload = selection()
        reading = await self.browser.host().analyze(payload)
        self.assertEqual(reading["status"], 200)
        body, session = reading["body"], reading["body"]["session_id"]
        self.assertEqual(body["max_dialogue_turns"], 3)
        original = self.browser.snapshot()["sessions"][0]
        self.assertEqual(original["generation_route"], DEEPSEEK_GENERATION_ROUTE)
        self.assertEqual(self.browser.snapshot()["schema_version"], LIVE_ACTOR_SCHEMA_VERSION)
        self.assertEqual(await self.browser.host().analyze(payload), reading)
        self.assertEqual(len(self.renderer.calls), 1)
        previous_history = deepcopy(original["history"])
        for turn in range(1, 4):
            request = {"session_id": session, "user_message": f"Question {turn}?", "turn_id": f"turn-{turn}"}
            response = await self.browser.host().dialogue(request)
            self.assertEqual(response["status"], 200)
            self.assertEqual(response["body"]["turn_count"], turn)
            self.assertEqual(response["body"]["ended"], turn == 3)
            self.assertEqual(await self.browser.host().dialogue(request), response)
            self.assertEqual(len(self.renderer.calls), 1 + turn)
            restored = self.browser.host().service.sessions.get(session)
            self.assertEqual(restored.turns, turn)
            self.assertEqual(restored.context, original["context"])
            self.assertEqual(len(restored.calls), 1 + turn)
            self.assertEqual(restored.history[-1]["content"], f"Fixture answer {1 + turn}.")
            self.assertEqual(self.renderer.calls[-1]["messages"][:len(previous_history)], previous_history)
            previous_history = deepcopy(restored.history)
        snapshot = self.browser.snapshot()
        self.assertEqual(snapshot["model_calls_reserved"], 4)
        self.assertEqual(self.browser.snapshot_modes, [True, False] * 4)
        self.assertEqual(self.browser.storage.rows("SELECT state FROM norn_paid_receipts"),
                         [("settled",)] * 4)
        # The provider receives the entire frozen prefix, including earlier
        # native assistant replies; persisted client data never supplies it.
        final_messages = self.renderer.calls[-1]["messages"]
        self.assertTrue(any("Question 1?" in message["content"] for message in final_messages))
        self.assertTrue(any(message["content"] == "Fixture answer 2." for message in final_messages))
        self.assertEqual(len(self.quota.reservations), 4)
        self.assertEqual(len(self.quota.releases), 4)

    async def test_conflicting_analysis_and_dialogue_ids_return_409_without_dispatch(self):
        payload = selection()
        reading = await self.browser.host().analyze(payload)
        await self.assert_request_error(409, self.browser.host().analyze({**payload, "selections": [
            {"id": "NORN_0002", "round": 1}]}))
        request = {"session_id": reading["body"]["session_id"], "user_message": "Question?", "turn_id": "turn"}
        await self.browser.host().dialogue(request)
        await self.assert_request_error(409, self.browser.host().dialogue({**request, "user_message": "Changed?"}))
        self.assertEqual(len(self.renderer.calls), 2)
        self.assertEqual(len(self.quota.reservations), 2)

    async def test_snapshot_failure_before_fetch_leaves_pending_and_zero_dispatch(self):
        self.browser.fail_snapshot_mode = True
        payload = selection()
        with self.assertRaisesRegex(RuntimeError, "snapshot failure"):
            await self.browser.host().analyze(payload)
        self.assertEqual(self.renderer.calls, [])
        self.assertIsNone(self.browser.snapshot())
        receipt = self.browser.receipts().get("analysis", payload["request_id"], self.fingerprint(payload))
        self.assertEqual(receipt["state"], "pending")
        self.browser.fail_snapshot_mode = None
        await self.assert_request_error(409, self.browser.host().analyze(payload))
        self.assertEqual(self.renderer.calls, [])
        self.assertEqual(len(self.quota.reservations), 1)
        self.assertEqual(len(self.quota.releases), 1)

    async def test_settle_failure_after_fetch_cannot_redispatch_or_persist_partial_session(self):
        for failure in ("snapshot", "receipt"):
            with self.subTest(failure=failure):
                browser = self.new_browser("actor-" + failure)
                payload = selection()
                before = len(self.renderer.calls)
                if failure == "snapshot":
                    browser.fail_snapshot_mode = False
                else:
                    browser.storage.sql.fail_prefix = "UPDATE norn_paid_receipts"

                def inspect_before_response():
                    persisted = browser.snapshot()
                    self.assertEqual(persisted["model_calls_reserved"], 1)
                    self.assertEqual(persisted["sessions"], [])
                    self.assertEqual(browser.receipts().get("analysis", "reading-1", self.fingerprint(payload))["state"],
                                     "pending")

                self.renderer.before_return = inspect_before_response
                with self.assertRaisesRegex(RuntimeError, "failure"):
                    await browser.host().analyze(payload)
                self.assertEqual(len(self.renderer.calls), before + 1)
                browser.fail_snapshot_mode = None
                browser.storage.sql.fail_prefix = None
                self.renderer.before_return = None
                self.assertEqual(browser.snapshot()["model_calls_reserved"], 1)
                self.assertEqual(browser.snapshot()["sessions"], [])
                await self.assert_request_error(409, browser.host().analyze(payload))
                self.assertEqual(len(self.renderer.calls), before + 1)

    async def test_explicit_quota_denial_discards_pending_and_same_id_can_retry(self):
        # All four leases are occupied by other visitors, using real quota SQL.
        for index in range(4):
            self.assertTrue(self.quota.store().reserve(f"other-{index}")["allowed"])
        payload = selection()
        await self.assert_request_error(429, self.browser.host().analyze(payload))
        self.assertIsNone(self.browser.receipts().get("analysis", "reading-1", self.fingerprint(payload)))
        self.assertIsNone(self.browser.snapshot())
        self.assertEqual(self.renderer.calls, [])
        self.quota.store().release("other-0")
        response = await self.browser.host().analyze(payload)
        self.assertEqual(response["status"], 200)
        self.assertEqual(len(self.renderer.calls), 1)
        self.assertEqual(self.browser.snapshot()["model_calls_reserved"], 1)
        self.assertEqual(self.quota_storage.rows("SELECT used FROM norn_budget_days"), [(5,)])

    async def test_ambiguous_quota_rpc_retains_pending_even_when_reservation_committed(self):
        self.quota.ambiguous_reservation = True
        payload = selection()
        with self.assertRaisesRegex(RuntimeError, "disconnected"):
            await self.browser.host().analyze(payload)
        self.assertEqual(self.renderer.calls, [])
        self.assertEqual(self.quota_storage.rows("SELECT used FROM norn_budget_days"), [(1,)])
        self.assertEqual(self.browser.receipts().get("analysis", "reading-1", self.fingerprint(payload))["state"],
                         "pending")
        self.quota.ambiguous_reservation = False
        await self.assert_request_error(409, self.browser.host().analyze(payload))
        self.assertEqual(self.renderer.calls, [])
        self.assertEqual(len(self.quota.reservations), 1)

    async def test_plain_dict_quota_adapters_keep_the_same_admission_contract(self):
        # Production Python RPC returns a JSON string; local adapters may return
        # a plain dict. Both must make exactly one reservation and dispatch.
        self.quota.string_results = False
        response = await self.browser.host().analyze(selection(1))
        self.assertEqual(response["status"], 200)
        self.assertEqual(len(self.renderer.calls), 1)
        self.assertEqual(len(self.quota.reservations), 1)
        self.assertEqual(len(self.quota.releases), 1)

    async def test_browser_model_limit_survives_new_sessions_and_evictions(self):
        for index in range(MAX_LIVE_MODEL_CALLS):
            response = await self.browser.host().analyze(selection(1, request_id=f"reading-{index}"))
            self.assertEqual(response["status"], 200)
        self.assertEqual(self.browser.snapshot()["model_calls_reserved"], 12)
        await self.assert_request_error(429, self.browser.host().analyze(selection(1, request_id="reading-overflow")))
        self.assertEqual(len(self.renderer.calls), 12)
        self.assertEqual(len(self.quota.reservations), 12)
        self.assertIsNone(self.browser.receipts().get("analysis", "reading-overflow",
                          self.fingerprint(selection(1, request_id="reading-overflow"))))

    async def test_global_hundred_call_limit_is_shared_by_independent_browsers(self):
        browser = None
        for index in range(100):
            if index % 12 == 0:
                browser = self.new_browser(f"visitor-{index // 12}")
            response = await browser.host().analyze(selection(1, request_id=f"reading-{index}"))
            self.assertEqual(response["status"], 200)
        fresh = self.new_browser("visitor-fresh")
        await self.assert_request_error(429, fresh.host().analyze(selection(1)))
        self.assertEqual(len(self.renderer.calls), 100)
        self.assertEqual(self.quota_storage.rows("SELECT used FROM norn_budget_days"), [(100,)])
        self.assertEqual(fresh.storage.rows("SELECT COUNT(*) FROM norn_paid_receipts"), [(0,)])
        self.assertIsNone(fresh.snapshot())

    async def test_empty_selection_and_completed_lockout_make_no_paid_calls(self):
        empty = {"selections": [], "total_rounds": 1, "request_id": "empty"}
        empty_result = await self.browser.host(api_key="").analyze(empty)
        self.assertEqual(empty_result["status"], 200)
        self.assertTrue(empty_result["body"]["empty_selection"])
        self.assertEqual(empty_result["body"]["provider_calls"], 0)
        self.assertEqual(await self.browser.host(api_key="").analyze(empty), empty_result)
        self.assertEqual(self.renderer.calls, [])
        self.assertEqual(self.quota.reservations, [])
        self.assertEqual(self.browser.snapshot()["model_calls_reserved"], 0)
        reading = await self.browser.host().analyze(selection(1, request_id="real"))
        request = {"session_id": reading["body"]["session_id"], "user_message": "Question?", "turn_id": "turn"}
        await self.browser.host().dialogue(request)
        before = len(self.renderer.calls)
        locked = await self.browser.host(api_key="").dialogue({**request, "turn_id": "locked"})
        self.assertEqual(locked["status"], 200)
        self.assertTrue(locked["body"]["blocked"])
        self.assertTrue(locked["body"]["ended"])
        self.assertEqual(len(self.renderer.calls), before)
        self.assertEqual(len(self.quota.reservations), before)

    async def test_missing_credential_is_fail_closed_and_never_reserves(self):
        await self.assert_request_error(503, self.browser.host(api_key="").analyze(selection()))
        self.assertEqual(self.renderer.calls, [])
        self.assertEqual(self.quota.reservations, [])
        self.assertEqual(self.browser.storage.rows("SELECT COUNT(*) FROM norn_paid_receipts"), [(0,)])
        self.assertIsNone(self.browser.snapshot())

    async def test_credentials_never_enter_response_snapshot_receipt_trace_or_attempt_id(self):
        host = self.browser.host()
        self.assertEqual(host.service.config["api_key"], "")
        result = await host.analyze(selection())
        session = result["body"]["session_id"]
        reply = await self.browser.host().dialogue({"session_id": session, "user_message": "Question?", "turn_id": "turn"})
        snapshot = self.browser.snapshot()
        persisted_results = self.browser.storage.rows("SELECT result_json FROM norn_paid_receipts")
        inspect = self.browser.host().service.inspect_session(session)
        serialized = json.dumps([result, reply, snapshot, persisted_results, inspect, self.quota.reservations],
                                ensure_ascii=False)
        self.assertNotIn(SECRET, serialized)
        self.assertNotIn("api_key", serialized)
        self.assertTrue(all(attempt.startswith("sha256:") for attempt in self.quota.reservations))


if __name__ == "__main__":
    unittest.main()

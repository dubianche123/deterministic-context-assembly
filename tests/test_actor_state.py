"""Eviction-safe, bounded mock conversation persistence for Cloudflare actors."""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from norn.actor_state import (ACTOR_SCHEMA_VERSION, MAX_OPERATIONS, MAX_STATE_BYTES,
                              dump_actor_state, new_actor_service, next_expiry)
from norn.events import canonical_json, content_hash
from norn.service import NornService, RequestError


class EpochClock:
    def __init__(self):
        self.now = 1_700_000_000.0

    def __call__(self):
        return self.now


def journal(round_count=21):
    events = []
    def add(kind, **fields):
        events.append({"sequence": len(events) + 1, "event_id": f"actor-{len(events) + 1}",
                       "type": kind, **fields})
    for number in range(1, round_count + 1):
        ids = [f"NORN_{(number - 1) * 3 + offset:04}" for offset in (1, 2, 3)]
        add("round_started", round=number, card_ids=ids)
        add("card_selected", round=number, card_id=ids[0])
        add("round_completed", round=number, duration_ms=1000)
    add("session_completed")
    return {"schema_version": "norn.events.v1", "events": events}


class ActorStateTests(unittest.TestCase):
    def setUp(self):
        self.clock = EpochClock()

    def restore(self, service):
        state = dump_actor_state(service)
        # Exercise the same strict UTF-8 JSON boundary used by SQLite chunks.
        state = json.loads(canonical_json(state))
        return new_actor_service(state, clock=self.clock)

    def test_reading_and_three_followups_survive_an_eviction_after_every_request(self):
        service = new_actor_service(clock=self.clock)
        first = service.analyze({"journal": journal(), "request_id": "initial"})
        session_id = first["session_id"]
        bundle = service.export_session(session_id)
        original_context = deepcopy(service.sessions.get(session_id).context)
        initial_expiry = service.sessions.get(session_id).expires_at
        for turn in range(1, 4):
            self.clock.now += 60
            service = self.restore(service)
            response = service.dialogue({"session_id": session_id, "turn_id": f"turn-{turn}",
                                         "user_message": f"为什么留下这些图片？{turn}"})
            self.assertEqual(response["turn_count"], turn)
            self.assertEqual(response["status"], "ok")
            self.assertEqual(response["ended"], turn == 3)
            session = service.sessions.get(session_id)
            self.assertEqual(session.context, original_context)
            self.assertEqual(session.expires_at, initial_expiry)
            self.assertEqual(service.export_session(session_id), bundle)
            self.assertEqual(len(session.calls), turn + 1)
            self.assertEqual(len(session.history), 2 + 2 * turn)
        service = self.restore(service)
        self.assertTrue(service.replay(service.export_session(session_id))["verified"])
        self.assertEqual(service.sessions.get(session_id).turns, 3)
        self.assertEqual(service.health()["limits"]["model_calls_reserved"], 0)
        state = dump_actor_state(service)
        self.assertLess(len(canonical_json(state).encode("utf-8")), MAX_STATE_BYTES)

    def test_v6_mock_snapshot_preserves_ledger_without_refitting_on_restore(self):
        from norn.core import engine_module
        from test_research_deck import session, research
        service = new_actor_service(clock=self.clock, deck="pamela-v1")
        reading = service.analyze({"journal": session(research.RANDOM, rounds=4), "request_id": "v6-mock"})
        exported = service.export_session(reading["session_id"])
        with patch.object(engine_module("research_model"), "fit", side_effect=AssertionError("actor refitted model")):
            state = dump_actor_state(service)
            restored = new_actor_service(json.loads(canonical_json(state)), clock=self.clock, deck="pamela-v1")
            self.assertEqual(dump_actor_state(restored), state)
            self.assertEqual(restored.export_session(reading["session_id"]), exported)
        self.assertTrue(restored.replay(exported)["verified"])

    def test_idempotent_analysis_and_turn_receipts_survive_restore_and_reject_mismatch(self):
        service = new_actor_service(clock=self.clock)
        body = {"journal": journal(2), "request_id": "initial"}
        first = service.analyze(body)
        service = self.restore(service)
        with patch.object(service, "generate", side_effect=AssertionError("retried generation")):
            self.assertEqual(service.analyze(body), first)
        changed = deepcopy(body)
        changed["journal"]["events"][2]["duration_ms"] += 1
        with self.assertRaises(RequestError) as mismatch:
            service.analyze(changed)
        self.assertEqual(mismatch.exception.status, 409)
        turn = {"session_id": first["session_id"], "turn_id": "question", "user_message": "为什么？"}
        response = service.dialogue(turn)
        service = self.restore(service)
        with patch.object(service, "generate", side_effect=AssertionError("retried generation")):
            self.assertEqual(service.dialogue(turn), response)
        with self.assertRaises(RequestError) as mismatch:
            service.dialogue({**turn, "user_message": "别的问题？"})
        self.assertEqual(mismatch.exception.status, 409)
        self.assertEqual(service.sessions.get(first["session_id"]).turns, 1)

    def test_failed_generation_and_terminal_request_error_are_both_cached(self):
        service = new_actor_service(clock=self.clock)
        failed = {"text": "暂时无法完成", "status": "provider_unavailable", "usage": {}}
        body = {"journal": journal(1), "request_id": "failure"}
        with patch.object(service, "generate", return_value=failed) as generate:
            result = service.analyze(body)
        self.assertEqual(generate.call_count, 1)
        self.assertNotIn("session_id", result)
        service = self.restore(service)
        with patch.object(service, "generate", side_effect=AssertionError("retried failure")):
            self.assertEqual(service.analyze(body), result)
        def rejected():
            raise RequestError("终止请求", 422)
        fingerprint = content_hash({"test": True})
        with self.assertRaises(RequestError):
            service.operations.execute("analysis", "terminal", fingerprint, rejected)
        service = self.restore(service)
        with self.assertRaises(RequestError) as rejected_again:
            service.operations.execute("analysis", "terminal", fingerprint,
                                       lambda: self.fail("terminal error repeated"))
        self.assertEqual(rejected_again.exception.status, 422)

    def test_epoch_expiry_is_never_refreshed_and_all_expired_data_is_deleted(self):
        service = new_actor_service(clock=self.clock)
        result = service.analyze({"journal": journal(1), "request_id": "old"})
        state = dump_actor_state(service)
        original_expiry = self.clock.now + 1800
        self.assertEqual(next_expiry(state), original_expiry)
        self.clock.now += 1799
        service = new_actor_service(state, clock=self.clock)
        self.assertEqual(service.sessions.get(result["session_id"]).expires_at, original_expiry)
        self.assertEqual(dump_actor_state(service)["operations"][0]["expires_at"], original_expiry)
        self.clock.now += 1
        # Expiry is inclusive, and storage cleanup does not retain reading,
        # frozen evidence or cached replies after all relevant receipts expire.
        service = new_actor_service(state, clock=self.clock)
        with self.assertRaises(RequestError) as expired:
            service.export_session(result["session_id"])
        self.assertEqual(expired.exception.status, 404)
        clean = dump_actor_state(service)
        self.assertEqual(clean["sessions"], [])
        self.assertEqual(clean["operations"], [])
        self.assertIsNone(next_expiry(clean))

    def test_evicted_session_receipt_does_not_generate_a_second_reading(self):
        service = new_actor_service(clock=self.clock)
        first_body = {"journal": journal(1), "request_id": "first"}
        first = service.analyze(first_body)
        service.analyze({"journal": journal(2), "request_id": "second"})
        service = self.restore(service)
        self.assertNotIn(first["session_id"], service.sessions._sessions)
        with patch.object(service, "generate", side_effect=AssertionError("regenerated evicted session")):
            with self.assertRaises(RequestError) as gone:
                service.analyze(first_body)
        self.assertEqual(gone.exception.status, 410)

    def test_retriable_429_receipt_remains_retriable_after_restore(self):
        service = new_actor_service(clock=self.clock)
        fingerprint = content_hash({"test": "limited"})
        def limited():
            raise RequestError("容量限制", 429)
        with self.assertRaises(RequestError):
            service.operations.execute("analysis", "limited", fingerprint, limited)
        state = dump_actor_state(service)
        self.assertFalse(state["operations"][0]["done"])
        service = new_actor_service(state, clock=self.clock)
        result = service.operations.execute("analysis", "limited", fingerprint, lambda: {"ok": True})
        self.assertEqual(result, {"ok": True})

    def test_detached_snapshots_and_restores_do_not_share_mutable_data(self):
        service = new_actor_service(clock=self.clock)
        result = service.analyze({"journal": journal(1), "request_id": "detached"})
        state = dump_actor_state(service)
        restored = new_actor_service(state, clock=self.clock)
        state["sessions"][0]["reading"] = "changed"
        state["operations"][0]["result"]["reading"] = "changed"
        for instance in (service, restored):
            self.assertEqual(instance.sessions.get(result["session_id"]).reading, result["reading"])
            self.assertEqual(instance.analyze({"journal": journal(1), "request_id": "detached"}), result)

    def test_strict_schema_types_and_structural_bounds_reject_hostile_state(self):
        service = new_actor_service(clock=self.clock)
        service.analyze({"journal": journal(1), "request_id": "strict"})
        clean = dump_actor_state(service)
        cases = []
        def altered(change):
            state = deepcopy(clean)
            change(state)
            cases.append(state)
        altered(lambda state: state.update(unexpected=True))
        altered(lambda state: state.update(schema_version="norn.actor.future"))
        altered(lambda state: state.update(model_calls_reserved=True))
        altered(lambda state: state.update(sessions=state["sessions"] * 2))
        altered(lambda state: state.update(operations=state["operations"] * (MAX_OPERATIONS + 1)))
        altered(lambda state: state["sessions"][0].update(lock="bad"))
        altered(lambda state: state["sessions"][0].update(turns=True))
        altered(lambda state: state["sessions"][0].update(expires_at=float("nan")))
        altered(lambda state: state["sessions"][0].update(history="not-messages"))
        altered(lambda state: state["sessions"][0]["history"][0].update(role="system"))
        altered(lambda state: state["sessions"][0]["prompt"].update(system="forged"))
        altered(lambda state: state["operations"][0].update(unexpected=True))
        altered(lambda state: state["operations"][0].update(done=1))
        altered(lambda state: state["operations"][0].update(fingerprint="not-a-hash"))
        altered(lambda state: state["operations"][0].update(scope="arbitrary-code"))
        altered(lambda state: state["operations"][0].update(result=None))
        altered(lambda state: state["operations"][0].update(error=["bad", 200]))
        altered(lambda state: state.update(operations=state["operations"] * 2))
        altered(lambda state: state["sessions"][0].update(analysis={"bad": object()}))
        nested = None
        for _ in range(66):
            nested = [nested]
        altered(lambda state: state["sessions"][0].update(analysis={"deep": nested}))
        for index, state in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(ValueError):
                new_actor_service(state, clock=self.clock)
        with patch("norn.actor_state.MAX_STATE_BYTES", 1024), self.assertRaises(ValueError):
            new_actor_service(clean, clock=self.clock)

    def test_snapshot_refuses_live_operations_or_non_actor_services(self):
        with self.assertRaises(ValueError):
            dump_actor_state(NornService())
        service = new_actor_service(clock=self.clock)
        service.limits.active = 1
        with self.assertRaises(ValueError):
            dump_actor_state(service)
        service.limits.active = 0
        service.analyze({"journal": journal(1), "request_id": "busy"})
        next(iter(service.operations._entries.values())).users = 1
        with self.assertRaises(ValueError):
            dump_actor_state(service)


if __name__ == "__main__":
    unittest.main()

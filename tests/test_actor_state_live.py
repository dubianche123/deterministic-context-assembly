"""Versioned actor routing persists real generations without storing secrets."""

from copy import deepcopy
import json
import time
import unittest
from unittest.mock import patch

from norn.actor_state import (ACTOR_SCHEMA_VERSION, DEEPSEEK_GENERATION_ROUTE,
                              LIVE_ACTOR_SCHEMA_VERSION, MAX_LIVE_MODEL_CALLS,
                              MOCK_GENERATION_ROUTE, dump_actor_state,
                              new_actor_service, next_expiry)
from norn.events import canonical_json
from norn.service import RequestError


class EpochClock:
    def __init__(self):
        self.now = 1_700_000_000.0

    def __call__(self):
        return self.now


def selection():
    return {"total_rounds": 25, "selections": [{"id": "NORN_0001", "round": 1}]}


def settled_generation(service, plan, task):
    """Model the async transport's already-reserved, settled response."""
    service.limits.model_calls += 1
    generation = {"text": "完整初次判词。" if task == "analysis" else "完整追问回复。",
                  "status": "ok", "blocked": False, "provider": "openai-compatible",
                  "simulated": False, "provider_calls": 1, "model": "deepseek-flash",
                  "usage": {"input_tokens": 100, "output_tokens": 20}}
    return service._finish_render(plan["prompt"], {"task": task}, generation, time.perf_counter())


class LiveActorStateTests(unittest.TestCase):
    def setUp(self):
        self.clock = EpochClock()

    def live_service(self, state=None, **kwargs):
        return new_actor_service(state, clock=self.clock, provider="openai-compatible", **kwargs)

    def analyzed(self):
        service = self.live_service()
        plan = service._prepare_analysis(selection())
        generation, trace = settled_generation(service, plan, "analysis")
        response = service._finish_analysis(plan, generation, trace)
        session = service.sessions.get(response["session_id"])
        session.generation_route = deepcopy(DEEPSEEK_GENERATION_ROUTE)
        return service, response

    def test_legacy_snapshot_keeps_v1_and_implicit_mock_route(self):
        legacy = new_actor_service(clock=self.clock)
        response = legacy.analyze(selection())
        state = dump_actor_state(legacy)
        self.assertEqual(state["schema_version"], ACTOR_SCHEMA_VERSION)
        self.assertNotIn("generation_route", state["sessions"][0])
        restored = new_actor_service(state, clock=self.clock)
        self.assertEqual(restored.sessions.get(response["session_id"]).generation_route, MOCK_GENERATION_ROUTE)
        self.assertEqual(dump_actor_state(restored), state)

        upgraded = self.live_service(state)
        upgraded_state = dump_actor_state(upgraded)
        self.assertEqual(upgraded_state["schema_version"], LIVE_ACTOR_SCHEMA_VERSION)
        self.assertEqual(upgraded_state["sessions"][0]["generation_route"], MOCK_GENERATION_ROUTE)
        self.assertEqual(upgraded_state["sessions"][0]["expires_at"], state["sessions"][0]["expires_at"])
        self.assertEqual(upgraded.limits.max_model_calls, MAX_LIVE_MODEL_CALLS)

    def test_live_reading_and_three_staged_followups_survive_eviction(self):
        service, response = self.analyzed()
        key = response["session_id"]
        original = service.sessions.get(key)
        initial_context, initial_expiry = deepcopy(original.context), original.expires_at
        previous_history = deepcopy(original.history)
        for turn in range(1, 4):
            self.clock.now += 60
            state = json.loads(canonical_json(dump_actor_state(service)))
            self.assertEqual(state["schema_version"], LIVE_ACTOR_SCHEMA_VERSION)
            self.assertEqual(state["model_calls_reserved"], turn)
            # GET/alarm reconstruction need not enable a paid provider.
            read_only = new_actor_service(state, clock=self.clock)
            self.assertEqual(read_only.provider, "mock")
            self.assertEqual(read_only.limits.max_model_calls, MAX_LIVE_MODEL_CALLS)
            self.assertEqual(dump_actor_state(read_only), state)
            service = self.live_service(dump_actor_state(read_only))
            session = service.sessions.get(key)
            self.assertEqual(session.generation_route, DEEPSEEK_GENERATION_ROUTE)
            plan = service._prepare_dialogue(key, f"为什么？{turn}")
            self.assertEqual(plan["prompt"]["messages"][:-1], previous_history)
            generation, trace = settled_generation(service, plan, "dialogue")
            result = service._finish_dialogue(plan, generation, trace)
            self.assertEqual(result["turn_count"], turn)
            self.assertEqual(result["ended"], turn == 3)
            session = service.sessions.get(key)
            self.assertEqual(session.context, initial_context)
            self.assertEqual(session.expires_at, initial_expiry)
            self.assertEqual(len(session.calls), turn + 1)
            previous_history = deepcopy(session.history)
        state = dump_actor_state(service)
        self.assertEqual(state["model_calls_reserved"], 4)
        restored = new_actor_service(state, clock=self.clock)
        self.assertTrue(restored._prepare_dialogue(key, "还可以吗？")["response"]["ended"])
        self.assertTrue(restored.replay(restored.export_session(key))["verified"])

    def test_expiry_is_not_extended_by_live_or_read_only_restore(self):
        service, result = self.analyzed()
        state = dump_actor_state(service)
        expires_at = self.clock.now + 1800
        self.assertEqual(next_expiry(state), expires_at)
        self.clock.now += 1799
        restored = self.live_service(state)
        self.assertEqual(restored.sessions.get(result["session_id"]).expires_at, expires_at)
        self.assertEqual(next_expiry(dump_actor_state(restored)), expires_at)
        self.clock.now += 1
        restored = new_actor_service(state, clock=self.clock)
        with self.assertRaises(RequestError):
            restored.sessions.get(result["session_id"])
        clean = dump_actor_state(restored)
        self.assertEqual(clean["schema_version"], LIVE_ACTOR_SCHEMA_VERSION)
        self.assertEqual(clean["sessions"], [])
        self.assertIsNone(next_expiry(clean))

    def test_key_is_not_retained_in_config_or_snapshot_and_failure_stays_v2(self):
        secret = "fixture-secret-must-never-be-durable"
        service = self.live_service(config={"api_key": secret, **{
            key: value for key, value in DEEPSEEK_GENERATION_ROUTE.items() if key != "provider"}})
        self.assertEqual(service.config["api_key"], "")
        plan = service._prepare_analysis(selection())
        service.limits.model_calls += 1
        generation, trace = service._finish_render(plan["prompt"], {"task": "analysis"}, {
            "text": "暂时无法生成。", "status": "provider_unavailable", "usage": {}}, time.perf_counter())
        self.assertNotIn("session_id", service._finish_analysis(plan, generation, trace))
        state = dump_actor_state(service)
        self.assertEqual(state["schema_version"], LIVE_ACTOR_SCHEMA_VERSION)
        self.assertEqual(state["model_calls_reserved"], 1)
        encoded = canonical_json(state)
        for forbidden in (secret, "api_key", "config", "Authorization"):
            self.assertNotIn(forbidden, encoded)
        self.assertEqual(dump_actor_state(new_actor_service(state, clock=self.clock)), state)

    def test_missing_or_modified_routes_and_invalid_counters_are_rejected(self):
        service, _ = self.analyzed()
        clean = dump_actor_state(service)
        bad_routes = [{}, {"provider": "openai-compatible"}, {"provider": "mock", "api_key": "secret"},
                      {**DEEPSEEK_GENERATION_ROUTE, "api_key": "secret"},
                      {**DEEPSEEK_GENERATION_ROUTE, "base_url": "https://elsewhere.test/v1"},
                      {**DEEPSEEK_GENERATION_ROUTE, "model": "unapproved"},
                      {**DEEPSEEK_GENERATION_ROUTE, "thinking": "enabled"},
                      {**DEEPSEEK_GENERATION_ROUTE, "provider": "anthropic"}, None, []]
        for route in bad_routes:
            state = deepcopy(clean)
            state["sessions"][0]["generation_route"] = route
            with self.subTest(route=route), self.assertRaises(ValueError):
                new_actor_service(state, clock=self.clock)
        missing = deepcopy(clean)
        del missing["sessions"][0]["generation_route"]
        with self.assertRaises(ValueError):
            new_actor_service(missing, clock=self.clock)
        for counter in (-1, True, 1.5, MAX_LIVE_MODEL_CALLS + 1):
            state = deepcopy(clean)
            state["model_calls_reserved"] = counter
            with self.subTest(counter=counter), self.assertRaises(ValueError):
                new_actor_service(state, clock=self.clock)
        state = deepcopy(clean)
        state["schema_version"] = ACTOR_SCHEMA_VERSION
        with self.assertRaises(ValueError):
            new_actor_service(state, clock=self.clock)
        legacy = dump_actor_state(new_actor_service(clock=self.clock))
        legacy["model_calls_reserved"] = 2
        with self.assertRaises(ValueError):
            new_actor_service(legacy, clock=self.clock)

    def test_live_snapshot_requires_explicit_session_route_and_fixed_provider(self):
        service = self.live_service()
        plan = service._prepare_analysis(selection())
        generation, trace = settled_generation(service, plan, "analysis")
        result = service._finish_analysis(plan, generation, trace)
        with self.assertRaises(ValueError):
            dump_actor_state(service)
        service.sessions.get(result["session_id"]).generation_route = deepcopy(DEEPSEEK_GENERATION_ROUTE)
        state = dump_actor_state(service)
        state["sessions"][0]["generation_route"]["model"] = "detached"
        self.assertEqual(service.sessions.get(result["session_id"]).generation_route, DEEPSEEK_GENERATION_ROUTE)
        for config in ({"model": "other"}, {"base_url": "http://localhost/v1"},
                       {"thinking": "enabled"}, {"extra": "unapproved"}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                self.live_service(config=config)
        with self.assertRaises(ValueError):
            new_actor_service(clock=self.clock, provider="anthropic")
        with patch.dict("os.environ", {"NORN_API_KEY": "environment-secret"}):
            self.assertEqual(self.live_service().config["api_key"], "")


if __name__ == "__main__":
    unittest.main()

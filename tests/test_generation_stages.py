"""Synchronous and async hosts share one authoritative generation lifecycle."""

from copy import deepcopy
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch

from norn.service import NornService, RequestError


def selection(rounds=25):
    return {"total_rounds": rounds, "selections": [{"id": "NORN_0001", "round": 1}],
            "round_durations": [], "deselection_events": []}


def service_state(service):
    def without_lock(value):
        return deepcopy({key: item for key, item in vars(value).items() if key != "lock"})
    return {"sessions": {key: without_lock(value) for key, value in service.sessions._sessions.items()},
            "operations": {key: without_lock(value) for key, value in service.operations._entries.items()},
            "limits": service.limits.snapshot()}


class Renderer:
    def __init__(self):
        self.calls = []
        self.status = "ok"

    def __call__(self, provider, prompt, **kwargs):
        self.calls.append(deepcopy({"prompt": prompt, **kwargs}))
        return {"text": "完整判词。" if kwargs["task"] == "analysis" else "完整追问回复。",
                "status": self.status, "blocked": False, "provider": provider,
                "simulated": provider == "mock", "request_id": "upstream-id",
                "elapsed_ms": 12.5, "usage": {"input_tokens": 100, "output_tokens": 20},
                "cache": {"observed": True}, "model": "fixture"}


def staged_render(service, plan, task):
    kwargs = {"task": task, "analysis": plan["analysis"]}
    if task == "dialogue":
        kwargs.update(user_message=plan["user_message"], turn_count=plan["turn"],
                      max_turns=plan["session"].max_turns)
    started = time.perf_counter()
    generation = service.generate(service.provider, deepcopy(plan["prompt"]),
                                  config=deepcopy(service.config), **deepcopy(kwargs))
    return service._finish_render(plan["prompt"], kwargs, generation, started)


class GenerationStageTests(unittest.TestCase):
    def make_service(self):
        renderer = Renderer()
        return NornService(generate=renderer), renderer

    @patch("norn.service.secrets.token_urlsafe", return_value="fixed-session")
    @patch("norn.service.uuid.uuid4", return_value=SimpleNamespace(hex="local-request"))
    def test_staged_reading_and_three_followups_equal_sync_results_and_history(self, *_):
        sync, sync_renderer = self.make_service()
        staged, staged_renderer = self.make_service()
        expected = sync.analyze(selection())
        plan = staged._prepare_analysis(selection())
        self.assertIsNone(plan["generation"])
        generation, trace = staged_render(staged, plan, "analysis")
        actual = staged._finish_analysis(plan, generation, trace)
        self.assertEqual(actual, expected)
        key = actual["session_id"]
        for turn in range(1, 4):
            message = f"第 {turn} 个问题"
            expected = sync.dialogue({"session_id": key, "user_message": message})
            plan = staged._prepare_dialogue(key, message)
            generation, trace = staged_render(staged, plan, "dialogue")
            actual = staged._finish_dialogue(plan, generation, trace)
            self.assertEqual(actual, expected)
            left, right = sync.sessions.get(key), staged.sessions.get(key)
            for name in ("analysis", "reading", "replay_bundle", "history", "context", "prompt", "calls", "turns"):
                self.assertEqual(getattr(left, name), getattr(right, name), name)
        self.assertEqual(sync_renderer.calls, staged_renderer.calls)
        self.assertTrue(actual["ended"])

    def test_preparation_does_not_dispatch_create_or_mutate_session_state(self):
        service, renderer = self.make_service()
        before = service_state(service)
        plan = service._prepare_analysis(selection())
        self.assertEqual(service_state(service), before)
        self.assertEqual(renderer.calls, [])
        self.assertEqual(set(plan), {"bundle", "analysis", "context", "prompt", "limit", "generation", "visitor",
                                     "experiments", "probe"})
        self.assertIsNone(plan["visitor"], "no consent marker, nothing to store")
        generation, trace = staged_render(service, plan, "analysis")
        key = service._finish_analysis(plan, generation, trace)["session_id"]
        before = service_state(service)
        prompt_plan = service._prepare_dialogue(key, "为什么？")
        self.assertEqual(service_state(service), before)
        self.assertEqual(len(renderer.calls), 1)
        self.assertEqual(set(prompt_plan), {"session", "prompt", "turn", "identity", "analysis", "user_message", "generation"})
        # The dispatch input is detached even when a host adapts it in place.
        prompt_plan["analysis"]["total_rounds"] = -1
        self.assertNotEqual(service.sessions.get(key).analysis["total_rounds"], -1)

    def test_failed_staged_reading_creates_no_session(self):
        service, renderer = self.make_service()
        plan = service._prepare_analysis(selection())
        renderer.status = "provider_unavailable"
        generation, trace = staged_render(service, plan, "analysis")
        result = service._finish_analysis(plan, generation, trace)
        self.assertEqual(result["status"], "provider_unavailable")
        self.assertNotIn("session_id", result)
        self.assertEqual(service.sessions._sessions, {})

    def test_failed_staged_dialogue_audits_attempt_without_advancing_history(self):
        service, renderer = self.make_service()
        key = service.analyze(selection(1))["session_id"]
        session = service.sessions.get(key)
        history = deepcopy(session.history)
        plan = service._prepare_dialogue(key, "可以重试吗？")
        renderer.status = "provider_unavailable"
        generation, trace = staged_render(service, plan, "dialogue")
        failed = service._finish_dialogue(plan, generation, trace)
        self.assertEqual(session.turns, 0)
        self.assertEqual(session.history, history)
        self.assertEqual(session.calls[-1], trace)
        self.assertEqual(session.prompt, plan["prompt"])
        self.assertEqual(failed["turn_count"], 0)
        self.assertFalse(failed["ended"])
        renderer.status = "ok"
        retry_plan = service._prepare_dialogue(key, "可以重试吗？")
        self.assertEqual(retry_plan["prompt"], plan["prompt"])
        generation, trace = staged_render(service, retry_plan, "dialogue")
        success = service._finish_dialogue(retry_plan, generation, trace)
        self.assertEqual(success["turn_count"], 1)
        self.assertTrue(success["ended"])

    def test_empty_and_locked_plans_need_no_dispatch(self):
        service, renderer = self.make_service()
        plan = service._prepare_analysis({"total_rounds": 1, "selections": []})
        self.assertIsNotNone(plan["generation"])
        result = service._finish_analysis(plan, None)
        self.assertEqual(renderer.calls, [])
        self.assertTrue(result["empty_selection"])
        self.assertEqual(result["provider_calls"], 0)
        key = result["session_id"]
        service.dialogue({"session_id": key, "user_message": "为什么？"})
        before = service_state(service)
        plan = service._prepare_dialogue(key, "还能追问吗？")
        self.assertEqual(set(plan), {"response"})
        locked = service._finish_dialogue(plan, None)
        self.assertTrue(locked["blocked"])
        self.assertTrue(locked["ended"])
        self.assertEqual(len(renderer.calls), 1)
        self.assertEqual(service_state(service), before)

    @patch("norn.service.uuid.uuid4", return_value=SimpleNamespace(hex="normalized-id"))
    def test_finish_render_preserves_safe_normalization_without_mutating_input(self, *_):
        service, _ = self.make_service()
        prompt = {"messages": [{"role": "user", "content": "问题"}]}
        generation = {"text": "可用文本。", "status": "sanitized", "request_id": "remote-id",
                      "usage": {"output_tokens": 10}}
        original = deepcopy(generation)
        rendered, trace = service._finish_render(prompt, {"task": "dialogue"}, generation, time.perf_counter())
        self.assertEqual(generation, original)
        self.assertEqual(rendered["status"], "ok")
        self.assertEqual(rendered["output_status"], "sanitized")
        self.assertEqual(rendered["provider_request_id"], "remote-id")
        self.assertEqual(rendered["request_id"], "normalized-id")
        self.assertEqual(rendered["provider_calls"], 0)
        prompt["messages"][0]["content"] = "changed"
        self.assertEqual(trace["prompt"]["messages"][0]["content"], "问题")
        self.assertEqual(service.limits.snapshot()["active"], 0)

    def test_invalid_prepare_and_unsettled_finish_cannot_create_session(self):
        service, renderer = self.make_service()
        with self.assertRaises(RequestError):
            service._prepare_analysis({"selections": [{"id": "unknown", "round": 1}]})
        with self.assertRaises(ValueError):
            service._finish_analysis(service._prepare_analysis(selection()), None)
        self.assertEqual(renderer.calls, [])
        self.assertEqual(service.sessions._sessions, {})


if __name__ == "__main__":
    unittest.main()

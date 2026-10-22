"""Production projections, native history and dispatch/retry boundaries."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import http.client
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from norn.service import NornService, RequestError, SessionStore
from norn.server import make_server


FIXTURE = Path(__file__).parent / "fixtures" / "replay-session.json"


def journal():
    document = json.loads(FIXTURE.read_text())
    return document.get("journal", document)


def snapshot():
    return {"total_rounds": 6, "selections": [{"id": "NORN_0001", "round": 1}]}


class Renderer:
    def __init__(self):
        self.calls = []
        self.status = "ok"

    def __call__(self, provider, prompt, **kwargs):
        self.calls.append(deepcopy(prompt))
        return {"text": "你可以慢慢回看这一次的选择，给自己留下一点辨认方向的空间。",
                "status": self.status, "usage": {"input_tokens": 10, "output_tokens": 5,
                "cached_tokens": 0, "cached_tokens_observed": True}, "reason_codes": []}


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.renderer = Renderer()
        self.service = NornService(generate=self.renderer)

    def test_every_prefix_previews_without_creating_session_or_model_call(self):
        original = journal()
        for index in range(len(original["events"]) + 1):
            source = {**original, "events": original["events"][:index]}
            untouched = deepcopy(source)
            result = self.service.preview({"journal": source})
            self.assertEqual(source, untouched)
            self.assertEqual(result["calls"], [])
            self.assertIsNone(result["metrics"]["usage"])
            self.assertIsNone(result["coords_avg"], "v4 has no four-axis heuristic to reconstruct")
            self.assertEqual(result["profile"]["analysis"]["attribution"]["schema_version"], "norn.attribution.v3")
            self.assertEqual(result["profile"]["source"], "event_journal" if result["complete"] else "event_preview")
        self.assertEqual(self.renderer.calls, [])
        self.assertEqual(len(self.service.sessions._sessions), 0)
        with self.assertRaises(RequestError):
            self.service.analyze({"journal": {**original, "events": original["events"][:-1]}})

    def test_frozen_system_and_full_native_history_survive_followups_and_failure(self):
        result = self.service.analyze({**snapshot(), "request_id": "reading-1"})
        key = result["session_id"]
        initial = self.service.inspect_session(key)
        exported = self.service.export_session(key)
        question = {"session_id": key, "user_message": "为什么？", "turn_id": "turn-1"}
        self.service.dialogue(question)
        first = deepcopy(self.renderer.calls[-1])
        self.renderer.status = "provider_unavailable"
        failed = self.service.dialogue({**question, "turn_id": "turn-2"})
        self.assertEqual(failed["turn_count"], 1)
        self.renderer.status = "ok"
        self.service.dialogue({**question, "turn_id": "turn-3"})
        second = self.renderer.calls[-1]
        self.assertEqual(second["system"], initial["prompt"]["system"])
        self.assertEqual(second["messages"][:len(first["messages"])], first["messages"])
        self.assertIn("程序当轮任务 1/2", second["messages"][-3]["content"])
        inspection = self.service.inspect_session(key)
        self.assertEqual(len(inspection["calls"]), 4)
        self.assertEqual(inspection["context"], initial["context"])
        self.assertEqual(inspection["profile"], initial["profile"])
        self.assertEqual(exported, self.service.export_session(key))
        self.assertNotIn("api_key", json.dumps(inspection))
        self.assertNotIn("base_url", json.dumps(inspection))

    def test_same_operation_id_collapses_parallel_and_terminal_failure_requests(self):
        body = {**snapshot(), "request_id": "same-reading"}
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(lambda _: self.service.analyze(body), range(4)))
        self.assertTrue(all(row == responses[0] for row in responses))
        self.assertEqual(len(self.renderer.calls), 1)
        with self.assertRaises(RequestError) as conflict:
            self.service.analyze({**body, "total_rounds": 1})
        self.assertEqual(conflict.exception.status, 409)
        self.renderer.status = "provider_unavailable"
        question = {"session_id": responses[0]["session_id"], "user_message": "再看一眼？", "turn_id": "failure-1"}
        first = self.service.dialogue(question)
        self.renderer.status = "ok"
        self.assertEqual(self.service.dialogue(question), first)
        self.assertEqual(len(self.renderer.calls), 2)
        self.assertEqual(self.service.dialogue({**question, "turn_id": "retry-2"})["turn_count"], 1)

    def test_real_attempt_limit_counts_failure_and_idempotent_retry_never_spends_twice(self):
        service = NornService(provider="openai-compatible", generate=self.renderer,
                              config={"base_url": "http://localhost/v1", "model": "fixture", "api_key": "secret"},
                              max_model_calls=1)
        self.renderer.status = "provider_unavailable"
        body = {**snapshot(), "request_id": "limited"}
        first = service.analyze(body)
        self.assertEqual(service.analyze(body), first)
        with self.assertRaises(RequestError) as limited:
            service.analyze({**body, "request_id": "new-attempt"})
        self.assertEqual(limited.exception.status, 429)
        self.assertEqual(len(self.renderer.calls), 1)
        self.assertEqual(service.limits.snapshot()["model_calls_reserved"], 1)
        service.preview({"journal": journal()})
        self.assertEqual(service.limits.snapshot()["model_calls_reserved"], 1)

    def test_concurrent_limit_rejects_before_dispatch_then_same_id_can_retry(self):
        entered, release = threading.Event(), threading.Event()
        def blocking(provider, prompt, **kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            return self.renderer(provider, prompt, **kwargs)
        service = NornService(generate=blocking, max_concurrency=1)
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(service.analyze, {**snapshot(), "request_id": "running"})
            self.assertTrue(entered.wait(5))
            with self.assertRaises(RequestError) as busy:
                service.analyze({**snapshot(), "request_id": "retry-busy"})
            self.assertEqual(busy.exception.status, 429)
            release.set()
            running.result()
        service.analyze({**snapshot(), "request_id": "retry-busy"})
        self.assertEqual(len(self.renderer.calls), 2)

    def test_sanitized_success_creates_usable_session(self):
        self.renderer.status = "sanitized"
        result = self.service.analyze(snapshot())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["output_status"], "sanitized")
        self.assertIn("session_id", result)

    def test_expired_receipt_does_not_return_dead_session_or_dispatch_again(self):
        service = NornService(generate=self.renderer, sessions=SessionStore(max_sessions=1))
        old = {**snapshot(), "request_id": "old-session"}
        service.analyze(old)
        service.analyze({**snapshot(), "request_id": "new-session"})
        with self.assertRaises(RequestError) as expired:
            service.analyze(old)
        self.assertEqual(expired.exception.status, 410)
        self.assertEqual(len(self.renderer.calls), 2)

    def test_unexpected_generation_failure_is_observed_and_retry_receipted(self):
        result = self.service.analyze(snapshot())
        def broken(*args, **kwargs):
            raise RuntimeError("credential-like data must not be exposed")
        self.service.generate = broken
        body = {"session_id": result["session_id"], "user_message": "为什么？", "turn_id": "broken"}
        first = self.service.dialogue(body)
        self.assertEqual(first["status"], "internal_error")
        self.assertEqual(self.service.dialogue(body), first)
        inspection = self.service.inspect_session(result["session_id"])
        self.assertEqual(len(inspection["calls"]), 2)
        self.assertEqual(inspection["turn_count"], 0)
        self.assertTrue(inspection["calls"][-1]["request_id"])
        self.assertNotIn("credential-like", json.dumps(inspection))


class WorkbenchHTTPTests(unittest.TestCase):
    def test_preview_inspect_and_exact_host_origin_boundaries(self):
        server = make_server(port=0, host="0.0.0.0", allowed_hosts=["demo.example"])
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(method, path, body=None, **headers):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                connection.request(method, path, json.dumps(body) if body is not None else None,
                                   {"Content-Type": "application/json", **headers})
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()
        try:
            status, preview = request("POST", "/api/preview", {"journal": journal()})
            self.assertEqual(status, 200)
            self.assertEqual(preview["calls"], [])
            status, reading = request("POST", "/api/analyze", snapshot())
            status, inspected = request("GET", f"/api/sessions/{reading['session_id']}/inspect")
            self.assertEqual(status, 200)
            self.assertEqual(len(inspected["calls"]), 1)
            self.assertEqual(request("GET", "/api/health", Host="demo.example", Origin="https://demo.example")[0], 200)
            self.assertEqual(request("GET", "/api/health", Host="demo.example", Origin="https://attacker.example")[0], 403)
            self.assertEqual(request("GET", "/api/health", Host="attacker.example")[0], 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_public_bind_and_wildcard_hosts_fail_before_opening_socket(self):
        with patch("norn.server.NornHTTPServer", side_effect=AssertionError("must validate first")):
            for options in ({"host": "0.0.0.0"}, {"allowed_hosts": ["*"]},
                            {"allowed_hosts": ["demo.example:bad"]}, {"allowed_hosts": ["user:pass@host"]}):
                with self.subTest(options=options), self.assertRaises(ValueError):
                    make_server(**options)


if __name__ == "__main__":
    unittest.main()

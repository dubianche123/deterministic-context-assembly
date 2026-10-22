"""Portable runtime contracts: real calculations, offline HTTP, bounded sessions."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from copy import deepcopy
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from norn.cli import doctor_report, main
from norn.core import engine_module
from norn.server import MAX_REQUEST_BYTES, make_server
from norn.service import NornService, RequestError, SessionStore


def selection(rounds=6, card="NORN_0001"):
    return {"total_rounds": rounds, "selections": [{"id": card, "round": 1}],
            "round_durations": [{"round": 1, "duration_ms": 1200}], "deselection_events": []}


class RecordingRenderer:
    def __init__(self):
        self.calls = []
        self.status = "ok"

    def __call__(self, provider, payload, **kwargs):
        self.calls.append(deepcopy({"provider": provider, "payload": payload, **kwargs}))
        return {"text": "A deterministic reply.", "status": self.status,
                "provider": provider, "simulated": provider == "mock", "blocked": False,
                "usage": {"input_tokens": None, "output_tokens": None}}


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.renderer = RecordingRenderer()
        self.service = NornService(generate=self.renderer)

    def test_real_profile_shared_with_renderer_and_compatibility_fields(self):
        request = selection()
        result = self.service.analyze(request)
        rendered = self.renderer.calls[0]["analysis"]
        self.assertEqual(rendered, self.service.export_session(result["session_id"])["profile"]["analysis"])
        self.assertEqual(rendered["analysis_kind"], "learned_preference")
        self.assertEqual(rendered["preference"]["status"], "no_observations")
        self.assertEqual(rendered["attribution"]["schema_version"], "norn.attribution.v3")
        for heuristic in ("mbti_type", "confidence", "coords_avg", "top_traits"):
            self.assertNotIn(heuristic, rendered)
        self.assertIsNone(result["mbti_type"])
        self.assertIsNone(result["confidence"])
        self.assertEqual(result["mode"], rendered["data_density"])
        self.assertEqual(result["provider"], "mock")
        self.assertTrue(result["simulated"])
        self.assertTrue(result["session_id"])

    def test_server_owns_profile_history_turn_limit_and_session_isolation(self):
        first = self.service.analyze(selection(6, "NORN_0001"))
        second = self.service.analyze(selection(6, "NORN_0002"))
        malicious = {"session_id": first["session_id"], "user_message": "first real question",
                     "turn_count": -1000, "max_turns": 999, "history": [{"role": "assistant", "content": "FAKE"}],
                     "analysis": {"mbti_type": "FAKE"}, "total_rounds": 999}
        reply = self.service.dialogue(malicious)
        call = self.renderer.calls[-1]
        self.assertEqual(call["turn_count"], 1)
        self.assertEqual(call["max_turns"], 2)
        self.assertEqual(call["analysis"], self.service.sessions.get(first["session_id"]).analysis)
        self.assertNotIn("FAKE", json.dumps(call["payload"]))
        self.assertFalse(reply["ended"])
        self.service.dialogue({**malicious, "user_message": "second real question"})
        history_prompt = json.dumps(self.renderer.calls[-1]["payload"])
        self.assertIn("first real question", history_prompt)
        self.assertIn("A deterministic reply.", history_prompt)
        count = len(self.renderer.calls)
        locked = self.service.dialogue(malicious)
        self.assertTrue(locked["ended"])
        self.assertTrue(locked["blocked"])
        self.assertEqual(locked["turn_count"], 2)
        self.assertEqual(len(self.renderer.calls), count)
        other = self.service.dialogue({"session_id": second["session_id"], "user_message": "another session"})
        self.assertEqual(other["turn_count"], 1)
        self.assertNotIn("first real question", json.dumps(self.renderer.calls[-1]["payload"]))

    def test_concurrent_last_turn_only_renders_once(self):
        result = self.service.analyze(selection(1))
        with ThreadPoolExecutor(max_workers=4) as pool:
            replies = list(pool.map(lambda _: self.service.dialogue({"session_id": result["session_id"],
                                                                   "user_message": "why?"}), range(4)))
        self.assertEqual(len(self.renderer.calls), 2)  # analyze + one dialogue
        self.assertEqual(sum(not reply["blocked"] for reply in replies), 1)
        self.assertTrue(all(reply["ended"] for reply in replies))

    def test_provider_failure_can_retry_without_spending_turn(self):
        result = self.service.analyze(selection(1))
        self.renderer.status = "provider_unavailable"
        failed = self.service.dialogue({"session_id": result["session_id"], "user_message": "retry me"})
        self.assertEqual(failed["turn_count"], 0)
        self.assertFalse(failed["ended"])
        self.renderer.status = "ok"
        success = self.service.dialogue({"session_id": result["session_id"], "user_message": "retry me"})
        self.assertEqual(success["turn_count"], 1)
        self.assertTrue(success["ended"])

    def test_renderer_cannot_mutate_authoritative_profile(self):
        def mutate(provider, payload, **kwargs):
            kwargs["analysis"]["total_rounds"] = -1
            kwargs["analysis"]["attribution"]["selections"].append("FAKE")
            return {"text": "Safe text.", "status": "ok"}
        service = NornService(generate=mutate)
        result = service.analyze(selection())
        stored = service.sessions.get(result["session_id"])
        self.assertNotEqual(stored.analysis["total_rounds"], -1)
        self.assertNotIn("FAKE", stored.analysis["attribution"]["selections"])
        service.dialogue({"session_id": result["session_id"], "user_message": "why?"})
        self.assertNotIn("FAKE", stored.analysis["attribution"]["selections"])

    def test_session_store_expiry_capacity_and_snapshot(self):
        now = [10.0]
        store = SessionStore(ttl=5, max_sessions=2, clock=lambda: now[0])
        profile = {"nested": [1]}
        first = store.create(profile, "reading", 1)
        profile["nested"].append(2)
        self.assertEqual(store.get(first).analysis, {"nested": [1]})
        second = store.create({}, "reading", 1)
        store.create({}, "reading", 1)
        with self.assertRaises(RequestError):
            store.get(first)
        now[0] = 15.0
        with self.assertRaises(RequestError):
            store.get(second)

    def test_bad_input_never_calls_renderer(self):
        valid = selection()
        bad_cases = [[], {**valid, "total_rounds": True}, {**valid, "total_rounds": 0},
                     {**valid, "total_rounds": 10**100},
                     {**valid, "selections": "bad"},
                     {**valid, "selections": [{"id": "unknown", "round": 1}]},
                     {**valid, "selections": [{"id": "NORN_0001", "round": 999}]},
                     {**valid, "selections": valid["selections"] * 2},
                     {**valid, "round_durations": [{"round": 1, "duration_ms": -1}]},
                     {**valid, "round_durations": valid["round_durations"] * 2},
                     {**valid, "deselection_events": [{"id": "fake", "round": 999}]}]
        for request in bad_cases:
            with self.subTest(request=request):
                with self.assertRaises(RequestError):
                    self.service.analyze(request)
        self.assertEqual(self.renderer.calls, [])

    def test_bad_dialogue_does_not_consume_turn(self):
        result = self.service.analyze(selection(1))
        for message in ("", " " * 5, "问" * 101, 25, None):
            with self.subTest(message=message):
                with self.assertRaises(RequestError):
                    self.service.dialogue({"session_id": result["session_id"], "user_message": message})
        with self.assertRaises(RequestError):
            self.service.dialogue({"session_id": "nonexistent", "user_message": "why?"})
        self.assertEqual(len(self.renderer.calls), 1)

    def test_empty_reading_is_a_rule_without_a_model_call_for_any_provider(self):
        service = NornService(provider="openai-compatible",
                              config={"base_url": "http://localhost/v1", "model": "fixture"},
                              generate=self.renderer)
        result = service.analyze({"selections": [], "total_rounds": 1})
        self.assertEqual(self.renderer.calls, [])
        self.assertTrue(result["empty_selection"])
        self.assertIsNone(result["mbti_type"], "Scoring v4 computes no four-axis type")
        self.assertEqual(result["provider_calls"], 0)
        self.assertIn("没有选择任何一张画面", result["reading"])
        self.assertTrue(result["session_id"])


class LocalHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Binding failures are failures: they must not silently skip the HTTP contract.
        cls.server = make_server(port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def post(self, path, body):
        status, raw, _ = self.request("POST", path, json.dumps(body), {"Content-Type": "application/json"})
        return status, json.loads(raw)

    def test_health_runtime_config_and_same_origin_static_bundle(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        status, raw, headers = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(raw)["offline"])
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        status, raw, _ = self.request("GET", "/runtime-config.js?v=offline")
        self.assertEqual(status, 200)
        self.assertIn(b'"apiEndpoint": "/api"', raw)
        self.assertIn(b'"offline": true', raw)
        status, raw, _ = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"<html", raw.lower())

    def test_offline_real_analysis_and_dialogue_loop(self):
        # Any attempted outgoing provider connection fails this test immediately.
        with patch("norn.providers.urlopen", side_effect=AssertionError("mock attempted network")):
            status, result = self.post("/api/analyze", selection(1))
            self.assertEqual(status, 200)
            self.assertEqual(result["profile_version"], "norn.profile.v5")
            self.assertTrue(result["reading"])
            self.assertTrue(result["simulated"])
            self.assertIsNone(result["usage"]["input_tokens"])
            self.assertEqual(result["provider_calls"], 0)
            status, reply = self.post("/api/dialogue", {"session_id": result["session_id"],
                                                       "user_message": "下一步应该怎么做？", "turn_count": 0,
                                                       "max_turns": 999})
            self.assertEqual(status, 200)
            self.assertTrue(reply["reply"])
            self.assertEqual(reply["turn_count"], 1)
            self.assertTrue(reply["ended"])
            status, locked = self.post("/api/dialogue", {"session_id": result["session_id"], "user_message": "继续？"})
            self.assertEqual(status, 200)
            self.assertTrue(locked["blocked"])
            self.assertTrue(locked["ended"])

    def test_empty_selection_still_creates_follow_up_session(self):
        status, result = self.post("/api/analyze", {"total_rounds": 1, "selections": []})
        self.assertEqual(status, 200)
        self.assertTrue(result["empty_selection"])
        self.assertIsNone(result["mbti_type"])
        status, reply = self.post("/api/dialogue", {"session_id": result["session_id"], "user_message": "为什么没有选择？"})
        self.assertEqual(status, 200)
        self.assertIn("没有", reply["reply"])

    def test_all_card_images_are_served_without_remote_urls(self):
        status, raw, _ = self.request("GET", "/data/cards.json")
        self.assertEqual(status, 200)
        cards = json.loads(raw)
        self.assertEqual(len(cards), 256)
        for card in cards:
            self.assertNotIn("://", card["url"])
            status, image, headers = self.request("GET", "/" + card["url"].removeprefix("./").lstrip("/"))
            self.assertEqual(status, 200, card["id"])
            self.assertGreater(len(image), 100)
            self.assertIn("image/", headers["Content-Type"])

    def test_reject_malformed_oversized_and_non_object_json(self):
        for body, content_type, expected in (("[]", "application/json", 400),
                                             ("{bad", "application/json", 400),
                                             ('{"total_rounds":NaN}', "application/json", 400),
                                             ('{"selections":[],"selections":[]}', "application/json", 400),
                                             ("{}", "text/plain", 415)):
            with self.subTest(expected=expected, body=body[:30]):
                status, raw, _ = self.request("POST", "/api/analyze", body, {"Content-Type": content_type})
                self.assertEqual(status, expected)
                self.assertIn("error", json.loads(raw))
        # Rejection happens from headers, before accepting an oversized body.
        status, raw, _ = self.request("POST", "/api/analyze", b"", {
            "Content-Type": "application/json", "Content-Length": str(MAX_REQUEST_BYTES + 1)})
        self.assertEqual(status, 413)
        self.assertIn("error", json.loads(raw))

    def test_static_traversal_and_other_origins_are_rejected(self):
        for path in ("/../README.md", "/%2e%2e/README.md", "/%2Egit/config", "/assets/%5c..%5cREADME.md", "/api/unknown"):
            with self.subTest(path=path):
                status, _, _ = self.request("GET", path)
                self.assertEqual(status, 404)
        status, _, _ = self.request("POST", "/api/analyze", "{}", {"Content-Type": "application/json", "Origin": "https://example.com"})
        self.assertEqual(status, 403)
        status, _, _ = self.request("GET", "/", headers={"Host": "example.com"})
        self.assertEqual(status, 403)
        status, _, _ = self.request("GET", "/data/")
        self.assertEqual(status, 404)

    def test_head_does_not_send_body(self):
        status, raw, headers = self.request("HEAD", "/")
        self.assertEqual(status, 200)
        self.assertEqual(raw, b"")
        self.assertGreater(int(headers["Content-Length"]), 0)

    def test_provider_errors_are_http_errors_without_creating_session(self):
        renderer = RecordingRenderer()
        service = NornService(generate=renderer)
        with patch.object(self.server, "service", service):
            for provider_status, expected in (("provider_unavailable", 503), ("invalid_output", 502)):
                renderer.status = provider_status
                status, result = self.post("/api/analyze", selection())
                self.assertEqual(status, expected)
                self.assertEqual(result["status"], provider_status)
                self.assertIn("error", result)
                self.assertNotIn("session_id", result)

    def test_static_symlinks_cannot_escape_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "web"
            root.mkdir()
            (parent / "private.txt").write_text("private marker")
            (root / "linked.txt").symlink_to(parent / "private.txt")
            with patch.object(self.server, "static_root", root):
                status, raw, _ = self.request("GET", "/linked.txt")
            self.assertEqual(status, 404)
            self.assertNotIn(b"private marker", raw)


class DoctorTests(unittest.TestCase):
    def test_doctor_is_read_only_offline_and_ignores_credentials(self):
        with patch("socket.create_connection", side_effect=AssertionError("doctor attempted network")), \
                patch.dict("os.environ", {"NORN_API_KEY": "never-use-this", "AWS_ACCESS_KEY_ID": "expired"}):
            report = doctor_report()
            self.assertTrue(report["ok"], report)
            self.assertFalse(report["network_used"])
            stream = io.StringIO()
            with redirect_stdout(stream):
                self.assertEqual(main(["doctor", "--json"]), 0)
            self.assertNotIn("never-use-this", stream.getvalue())

    def test_doctor_detects_missing_local_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            (root / "data" / "cards.json").write_text(json.dumps([{"id": "NORN_0001", "url": "assets/missing.webp"}]))
            with patch("norn.cli.web_root", return_value=root), \
                    patch("norn.cli.engine_module") as engine:
                engine.return_value._load_cards.return_value = {"NORN_0001": {}}
                report = doctor_report()
            check = next(item for item in report["checks"] if item["name"] == "offline_images")
            self.assertFalse(check["ok"])
            self.assertIn("Missing local card image", check["detail"])


if __name__ == "__main__":
    unittest.main()

"""Offline provider-boundary contracts, including real loopback HTTP transport."""
import builtins
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import unittest
from unittest.mock import patch

from norn.core import engine_module
from norn.providers import generate, provider_config, _usage


def profile(card="NORN_0001"):
    return engine_module("fast_thinker").fast_think([{"id": card, "round": 1}], 1)


@contextmanager
def compatible_server(response, status=200):
    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append({"path": self.path, "authorization": self.headers.get("Authorization"),
                             "body": json.loads(self.rfile.read(int(self.headers["Content-Length"])))})
            payload = json.dumps(response).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"base_url": f"http://127.0.0.1:{server.server_port}/v1", "model": "fixture-model",
               "api_key": "test-secret", "timeout": 2}, received
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class ProviderTests(unittest.TestCase):
    def test_mock_uses_real_profile_without_network_or_aws_import(self):
        original_import = builtins.__import__
        def no_aws(name, *args, **kwargs):
            if name.split(".")[0] in ("boto3", "botocore"):
                raise AssertionError("Offline path imported AWS")
            return original_import(name, *args, **kwargs)
        with patch.object(builtins, "__import__", side_effect=no_aws), \
                patch("norn.providers.urlopen", side_effect=AssertionError("Offline network access")):
            analysis = profile()
            prompt = engine_module("template_router").route_template(analysis)
            first = generate("mock", prompt, analysis=analysis)
            again = generate("mock", prompt, analysis=analysis)
            followup = generate("mock", prompt, task="dialogue", analysis=analysis,
                                user_message="为什么说我反复撤回？")
        self.assertEqual(first["text"], again["text"])
        self.assertIn("寻找框架", first["text"])
        self.assertIn("没有撤回", followup["text"])
        self.assertTrue(first["simulated"])
        self.assertEqual(first["provider_calls"], 0)
        self.assertIsNone(first["usage"]["input_tokens"])
        self.assertEqual(first["usage"]["source"], "not_applicable")

    def test_profile_changes_mock_reading(self):
        cards = engine_module("fast_thinker")._load_cards()
        opposite = next(card_id for card_id, card in cards.items() if card["coords"]["J_P"] < 0)
        a, b = profile(), profile(opposite)
        self.assertNotEqual(a["coords_avg"], b["coords_avg"])
        self.assertNotEqual(generate("mock", {}, analysis=a)["text"], generate("mock", {}, analysis=b)["text"])

    def test_explicit_provider_and_configuration(self):
        with self.assertRaises(ValueError):
            generate("unknown", {})
        with patch.dict("os.environ", {"NORN_API_KEY": "key-to-ignore", "NORN_MODEL": "ignore"}, clear=True):
            self.assertEqual(provider_config("mock"), {})
            with self.assertRaises(ValueError):
                provider_config("openai-compatible")
        for base in ("file:///tmp/api", "https://user:secret@example.test/v1", "https://example.test/v1?key=secret"):
            with self.assertRaises(ValueError):
                provider_config("openai-compatible", {"base_url": base, "model": "test"})

    def test_usage_distinguishes_missing_zero_and_invalid(self):
        self.assertIsNone(_usage()["cached_tokens"])
        self.assertFalse(_usage()["cached_tokens_observed"])
        measured = _usage({"prompt_tokens": 0, "completion_tokens": 0,
                           "prompt_tokens_details": {"cached_tokens": 0}})
        self.assertEqual(measured["input_tokens"], 0)
        self.assertEqual(measured["cached_tokens"], 0)
        self.assertTrue(measured["cached_tokens_observed"])
        self.assertIsNone(_usage({"prompt_tokens": True})["input_tokens"])

    def test_online_transport_preserves_native_messages_and_usage(self):
        prompt = {"system": "fixed instructions", "messages": [{"role": "user", "content": "first"},
                  {"role": "assistant", "content": "reply"}, {"role": "user", "content": "followup"}],
                  "max_tokens": 100, "temperature": 0.5}
        response = {"choices": [{"message": {"content": "你给自己保留了重新选择的余地。"}}],
                    "usage": {"prompt_tokens": 42, "completion_tokens": 12,
                              "prompt_tokens_details": {"cached_tokens": 0}}}
        with compatible_server(response) as (config, received):
            result = generate("openai-compatible", prompt, config=config)
        self.assertEqual(received[0]["path"], "/v1/chat/completions")
        self.assertEqual(received[0]["authorization"], "Bearer test-secret")
        self.assertEqual(received[0]["body"]["messages"][1:], prompt["messages"])
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["simulated"])
        self.assertEqual(result["usage"]["input_tokens"], 42)

    def test_provider_failure_does_not_switch_to_mock_or_leak_secrets(self):
        prompt = {"system": "fixed", "messages": [], "max_tokens": 20}
        with compatible_server({"error": "secret-provider-message"}, status=429) as (config, received):
            result = generate("openai-compatible", prompt, config=config)
        self.assertEqual(len(received), 1)
        self.assertEqual(result["status"], "provider_unavailable")
        self.assertFalse(result["simulated"])
        self.assertEqual(result["provider"], "openai-compatible")
        self.assertNotIn("secret", json.dumps(result))

    def test_generate_detaches_request_before_transport(self):
        request = {"system": "fixed", "messages": [{"role": "user", "content": "original"}]}
        def inspect(snapshot, config):
            request["messages"][0]["content"] = "later mutation"
            self.assertEqual(snapshot["messages"][0]["content"], "original")
            return {"text": "valid", "status": "ok", "blocked": False, "usage": _usage()}
        with patch("norn.providers._compatible", side_effect=inspect):
            generate("openai-compatible", request,
                     config={"base_url": "http://localhost/v1", "model": "fixture"})

    def test_bedrock_empty_output_is_not_a_successful_reading(self):
        engine = engine_module("slow_thinker")
        with patch.object(engine, "_invoke_bedrock", return_value={
            "text": " ", "blocked": False, "usage": {"input_tokens": 10, "output_tokens": 0}
        }):
            result = generate("bedrock", {})
        self.assertEqual(result["status"], "invalid_output")
        self.assertTrue(result["blocked"])
        self.assertTrue(result["text"].strip())
        self.assertEqual(result["usage"]["output_tokens"], 0)


if __name__ == "__main__":
    unittest.main()

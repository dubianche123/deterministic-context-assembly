"""Native protocol fixtures inspect real urllib requests without network calls."""

import io
import json
import unittest
from unittest.mock import patch

from norn.core import engine_module
from norn.providers import generate, provider_config, _usage


PROMPT = {"system": "stable rules and frozen profile", "messages": [
    {"role": "user", "content": "original reading task"},
    {"role": "assistant", "content": "original reading"},
    {"role": "user", "content": "why this choice?"}],
    "max_tokens": 120, "temperature": 0.6,
    "cache": {"strategy": "stable_prefix", "anchors": [{"type": "system", "name": "frozen_profile"}]}}
TEXT = "你在做决定之前会先确认方向，也可以给下一步保留一点呼吸的余地。"
ANTH_USAGE = {"input_tokens": 12, "cache_read_input_tokens": 80,
              "cache_creation_input_tokens": 20, "output_tokens": 15}


class Response:
    def __init__(self, result):
        self.data = json.dumps(result).encode()
        self.headers = {"request-id": "request-fixture-1"}
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self, size):
        return self.data[:size]


def render(provider, response, *, cache=True, prompt=None, extra_config=None):
    requests = []
    def transport(request, timeout):
        requests.append({"url": request.full_url,
                         "headers": {key.lower(): value for key, value in request.header_items()},
                         "body": json.loads(request.data), "timeout": timeout})
        return Response(response)
    with patch("norn.providers.urlopen", side_effect=transport):
        result = generate(provider, PROMPT if prompt is None else prompt,
                          config={"base_url": "http://127.0.0.1:9999/v1", "model": "fixture-model",
                                  "api_key": "fixture-secret", "cache_enabled": cache, "timeout": 2,
                                  **(extra_config or {})})
    return result, requests


class NativeProviderTests(unittest.TestCase):
    def test_compatible_reasoning_effort_is_explicit_and_validated(self):
        response = {"choices": [{"message": {"content": TEXT}}]}
        with patch.dict("os.environ", {}, clear=True):
            _, requests = render("openai-compatible", response)
            self.assertNotIn("reasoning_effort", requests[0]["body"])
            for effort in ("off", "minimal", "low", "medium", "high", "xhigh", "max"):
                _, requests = render("openai-compatible", response, extra_config={"reasoning_effort": effort})
                self.assertEqual(requests[0]["body"]["reasoning_effort"], effort)
            for invalid in ("", "auto", 0, True, []):
                with self.assertRaises(ValueError):
                    provider_config("openai-compatible", {"base_url": "http://localhost/v1", "model": "fixture",
                                                          "reasoning_effort": invalid})
        with patch.dict("os.environ", {"NORN_REASONING_EFFORT": "off"}, clear=True):
            _, requests = render("openai-compatible", response)
            self.assertEqual(requests[0]["body"]["reasoning_effort"], "off")
            _, requests = render("anthropic", {"content": [{"type": "text", "text": TEXT}]})
            self.assertNotIn("reasoning_effort", requests[0]["body"])

    def test_protocol_token_cutoffs_are_invalid_and_preserve_usage_without_retry(self):
        fixtures = (("openai-compatible", {"choices": [{"finish_reason": "length", "message": {"content": TEXT}}],
                                            "usage": {"prompt_tokens": 12, "completion_tokens": 15}}),
                    ("anthropic", {"stop_reason": "max_tokens", "content": [{"type": "text", "text": TEXT}],
                                   "usage": ANTH_USAGE}),
                    ("gemini", {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": TEXT}]}}],
                                "usageMetadata": {"promptTokenCount": 12, "totalTokenCount": 27}}))
        for provider, response in fixtures:
            with self.subTest(provider=provider):
                result, requests = render(provider, response)
                self.assertEqual(len(requests), 1)
                self.assertEqual(result["status"], "invalid_output")
                self.assertTrue(result["blocked"])
                self.assertEqual(result["reason_codes"], ["output_truncated"])
                self.assertNotEqual(result["text"], TEXT)
                self.assertEqual(result["usage"]["output_tokens"], 15)
                self.assertEqual(result["provider_calls"], 1)

    def test_bedrock_token_cutoff_preserves_usage_without_retry(self):
        engine = engine_module("slow_thinker")
        class Client:
            calls = 0
            def invoke_model(self, **kwargs):
                self.calls += 1
                return {"body": io.BytesIO(json.dumps({"stop_reason": "max_tokens",
                    "content": [{"text": TEXT}], "usage": ANTH_USAGE}).encode())}
        client = Client()
        with patch.object(engine, "_get_client", return_value=client):
            result = generate("bedrock", PROMPT)
        self.assertEqual(client.calls, 1)
        self.assertEqual(result["status"], "invalid_output")
        self.assertEqual(result["reason_codes"], ["output_truncated"])
        self.assertEqual(result["usage"]["output_tokens"], 15)

    def test_native_configuration_requires_explicit_model_and_key(self):
        with patch.dict("os.environ", {}, clear=True):
            for provider in ("anthropic", "gemini"):
                with self.subTest(provider=provider):
                    with self.assertRaises(ValueError):
                        provider_config(provider, {"model": "chosen"})
                    with self.assertRaises(ValueError):
                        provider_config(provider, {"api_key": "chosen"})
                    config = provider_config(provider, {"model": "chosen", "api_key": "chosen"})
                    self.assertTrue(config["base_url"].startswith("https://"))
                    self.assertFalse(config["cache_enabled"])
            self.assertEqual(provider_config("mock", {"cache_enabled": "not-a-switch"}), {})
            self.assertEqual(provider_config("openai-compatible", {
                "model": "local", "base_url": "http://localhost/v1"})["api_key"], "")
        with patch.dict("os.environ", {"NORN_MODEL": "chosen", "NORN_API_KEY": "chosen", "NORN_PROMPT_CACHE": "on"}, clear=True):
            self.assertTrue(provider_config("anthropic")["cache_enabled"])
            with self.assertRaises(ValueError):
                provider_config("gemini", {"cache_enabled": 1})

    def test_anthropic_native_roles_and_explicit_stable_system_cache_block(self):
        response = {"id": "msg-fixture", "model": "returned-model", "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": TEXT}], "usage": ANTH_USAGE}
        result, requests = render("anthropic", response)
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertTrue(request["url"].endswith("/v1/messages"))
        self.assertEqual(request["headers"]["x-api-key"], "fixture-secret")
        self.assertEqual(request["headers"]["anthropic-version"], "2023-06-01")
        self.assertEqual(request["body"]["system"], [{"type": "text", "text": PROMPT["system"],
                                                    "cache_control": {"type": "ephemeral"}}])
        self.assertEqual(request["body"]["messages"], PROMPT["messages"])
        self.assertEqual(result["usage"]["input_tokens"], 112)
        self.assertEqual(result["usage"]["uncached_input_tokens"], 12)
        self.assertEqual(result["usage"]["raw"], ANTH_USAGE)
        self.assertEqual(result["model"], "returned-model")
        self.assertEqual(result["request_id"], "request-fixture-1")
        self.assertEqual(result["provider_calls"], 1)
        self.assertTrue(result["cache"]["applied"])
        self.assertIsNone(result["cache"]["hit"])

    def test_anthropic_cache_is_opt_in_and_requires_logical_anchor(self):
        response = {"content": [{"type": "text", "text": TEXT}], "usage": ANTH_USAGE}
        for cache, prompt in ((False, PROMPT), (True, {**PROMPT, "cache": {}})):
            result, requests = render("anthropic", response, cache=cache, prompt=prompt)
            self.assertEqual(requests[0]["body"]["system"], PROMPT["system"])
            self.assertNotIn("cache_control", requests[0]["body"])
            self.assertFalse(result["cache"]["applied"])

    def test_compatible_never_receives_nonstandard_cache_fields(self):
        result, requests = render("openai-compatible", {
            "choices": [{"message": {"content": TEXT}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10,
                      "prompt_tokens_details": {"cached_tokens": 60}}})
        body = requests[0]["body"]
        self.assertEqual(body["messages"], [{"role": "system", "content": PROMPT["system"]}] + PROMPT["messages"])
        self.assertNotIn("cache", body)
        self.assertNotIn("cache_control", json.dumps(body))
        self.assertEqual(result["usage"]["uncached_input_tokens"], 40)
        self.assertEqual(result["cache"]["capability"], "provider_managed")

    def test_gemini_roles_parts_system_and_implicit_cache_without_creation(self):
        response = {"modelVersion": "gemini-fixture-version", "responseId": "gemini-id",
                    "candidates": [{"content": {"parts": [{"thought": True, "text": "private thought"}, {"text": TEXT}]},
                                    "finishReason": "STOP"}],
                    "usageMetadata": {"promptTokenCount": 100, "cachedContentTokenCount": 80,
                                      "candidatesTokenCount": 10, "thoughtsTokenCount": 5, "totalTokenCount": 115}}
        result, requests = render("gemini", response)
        self.assertEqual(len(requests), 1)
        self.assertTrue(requests[0]["url"].endswith("/models/fixture-model:generateContent"))
        self.assertNotIn("fixture-secret", requests[0]["url"])
        self.assertEqual(requests[0]["headers"]["x-goog-api-key"], "fixture-secret")
        body = requests[0]["body"]
        self.assertEqual(body["systemInstruction"], {"parts": [{"text": PROMPT["system"]}]})
        self.assertEqual([item["role"] for item in body["contents"]], ["user", "model", "user"])
        self.assertEqual(body["contents"][1]["parts"][0]["text"], "original reading")
        self.assertEqual(body["generationConfig"]["maxOutputTokens"], 120)
        self.assertNotIn("cachedContent", body)
        self.assertNotIn("cache_control", body)
        self.assertNotIn("private thought", result["text"])
        self.assertEqual(result["usage"]["input_tokens"], 100)
        self.assertEqual(result["usage"]["output_tokens"], 15)
        self.assertEqual(result["usage"]["uncached_input_tokens"], 20)
        self.assertEqual(result["usage"]["raw"], response["usageMetadata"])
        self.assertEqual(result["cache"]["capability"], "implicit")
        self.assertFalse(result["cache"]["applied"])

    def test_usage_zero_is_observed_while_missing_billing_counters_remain_unknown(self):
        missing = _usage({"input_tokens": 4, "output_tokens": 2}, provider="anthropic")
        self.assertIsNone(missing["input_tokens"])
        self.assertFalse(missing["input_tokens_observed"])
        self.assertEqual(missing["uncached_input_tokens"], 4)
        self.assertIsNone(missing["cache_read_input_tokens"])
        measured = _usage({"input_tokens": 0, "cache_read_input_tokens": 0,
                           "cache_creation_input_tokens": 0, "output_tokens": 0}, provider="anthropic")
        self.assertEqual(measured["input_tokens"], 0)
        self.assertTrue(measured["input_tokens_observed"])
        self.assertTrue(measured["cached_tokens_observed"])
        gemini = _usage({"promptTokenCount": 10, "candidatesTokenCount": 2}, provider="gemini")
        self.assertIsNone(gemini["output_tokens"])
        self.assertFalse(gemini["output_tokens_observed"])
        self.assertIsNone(gemini["uncached_input_tokens"])
        self.assertIsNone(_usage({"prompt_tokens": True})["input_tokens"])

    def test_native_guardrail_sanitizes_with_one_call_and_keeps_usage(self):
        for provider, response in (("anthropic", {"content": [{"type": "text", "text": "你是INTJ。" + TEXT}],
                                                   "usage": ANTH_USAGE}),
                                   ("gemini", {"candidates": [{"content": {"parts": [{"text": "你的E_I为8。" + TEXT}]}}],
                                               "usageMetadata": {"promptTokenCount": 10, "totalTokenCount": 20}})):
            with self.subTest(provider=provider):
                result, requests = render(provider, response)
                self.assertEqual(len(requests), 1)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["output_status"], "sanitized")
                self.assertFalse(result["blocked"])
                self.assertEqual(result["text"], TEXT)
                self.assertTrue(result["reason_codes"])
                self.assertEqual(result["usage"]["source"], "provider")

    def test_policy_blocks_are_not_transport_errors_or_empty_outputs(self):
        for provider, response in (("anthropic", {"stop_reason": "refusal", "usage": ANTH_USAGE}),
                                   ("gemini", {"promptFeedback": {"blockReason": "SAFETY"},
                                               "usageMetadata": {"promptTokenCount": 10, "totalTokenCount": 10}}),
                                   ("openai-compatible", {"choices": [{"finish_reason": "content_filter", "message": {}}]})):
            result, requests = render(provider, response)
            self.assertEqual(result["status"], "policy_blocked")
            self.assertTrue(result["blocked"])
            self.assertEqual(len(requests), 1)
        result, _ = render("anthropic", {"content": [], "usage": ANTH_USAGE})
        self.assertEqual(result["status"], "invalid_output")
        self.assertEqual(result["usage"]["output_tokens"], 15)

    def test_bedrock_uses_explicit_model_region_and_shared_cache_mapping(self):
        engine = engine_module("slow_thinker")
        requests = []
        class Client:
            def invoke_model(self, **kwargs):
                requests.append(kwargs)
                return {"body": io.BytesIO(json.dumps({"content": [{"text": TEXT}], "usage": ANTH_USAGE}).encode()),
                        "ResponseMetadata": {"RequestId": "bedrock-id"}}
        with patch.object(engine, "_get_client", return_value=Client()) as get_client:
            result = generate("bedrock", PROMPT, config={"model": "explicit-bedrock-model", "region": "fixture-region",
                                                        "cache_enabled": True})
        self.assertEqual(get_client.call_args.args, ("fixture-region",))
        self.assertEqual(requests[0]["modelId"], "explicit-bedrock-model")
        body = json.loads(requests[0]["body"])
        self.assertEqual(body["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertNotIn("_bedrock_config", body)
        self.assertEqual(result["request_id"], "bedrock-id")
        self.assertEqual(result["model"], "explicit-bedrock-model")
        self.assertEqual(result["usage"]["input_tokens"], 112)
        self.assertEqual(result["provider_calls"], 1)


if __name__ == "__main__":
    unittest.main()

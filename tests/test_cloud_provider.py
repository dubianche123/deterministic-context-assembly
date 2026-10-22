"""Cloud transport contracts without a credential or external model call."""
import asyncio
from copy import deepcopy
import json
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from norn import cloud_provider as cloud
from norn.providers import compatible_payload, compatible_result, generate


SECRET = "fixture-secret-do-not-persist"
PROMPT = {"system": "根据这次选择作有限解释。", "messages": [
    {"role": "user", "content": "选择记录"},
    {"role": "assistant", "content": "第一次回答"},
    {"role": "user", "content": "依据是什么？"}], "max_tokens": 650, "temperature": 0.8}


def response(text="你为自己留下一些重新辨认画面的余地。", **extra):
    return {"model": "deepseek-flash", "id": "chatcmpl-fixture",
            "choices": [{"message": {"content": text}, "finish_reason": "stop"}], **extra}


class Chunks:
    def __init__(self, chunks, delay=0):
        self.chunks = iter(chunks)
        self.delay = delay
        self.closed = False
        self.read = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.delay:
            await asyncio.sleep(self.delay)
        value = next(self.chunks, None)
        if value is None:
            raise StopAsyncIteration
        self.read += 1
        return value

    async def aclose(self):
        self.closed = True


class CloudProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_admission_helper_matches_the_exact_transmitted_bytes(self):
        encoded = cloud.prepare_cloud_request(PROMPT)
        received = []

        async def transport(_url, payload, _key, _timeout):
            received.append(payload)
            return 200, {}, json.dumps(response()).encode()

        await cloud.generate_cloud(PROMPT, api_key=SECRET, transport=transport)
        self.assertEqual(received, [encoded])
        for limit in (1200, cloud.MAX_OUTPUT_TOKENS):
            admitted = cloud.prepare_cloud_request({**PROMPT, "max_tokens": limit})
            self.assertEqual(json.loads(admitted)["max_tokens"], limit)

    async def render(self, raw=None, *, status=200, headers=None, body=None):
        calls = []

        async def transport(url, payload, key, timeout):
            calls.append((url, json.loads(payload), key, timeout))
            return status, headers or {}, (json.dumps(raw or response()).encode() if body is None else body)

        result = await cloud.generate_cloud(PROMPT, api_key=SECRET, transport=transport)
        return result, calls

    async def test_success_preserves_native_history_and_reports_real_cache_counters(self):
        prompt = deepcopy(PROMPT)
        raw = response(usage={"prompt_tokens": 120, "completion_tokens": 25,
                              "prompt_cache_hit_tokens": 100, "prompt_cache_miss_tokens": 20,
                              "provider_secret": SECRET,
                              "completion_tokens_details": {"reasoning_tokens": 0, "secret": SECRET}})
        result, calls = await self.render(raw, headers={"x-request-id": "request-42"})
        self.assertEqual(PROMPT, prompt)
        self.assertEqual(len(calls), 1)
        url, payload, key, timeout = calls[0]
        self.assertEqual(url, "https://api.deepseek.com/v1/chat/completions")
        self.assertEqual(payload["model"], "deepseek-flash")
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertNotIn("reasoning_effort", payload)
        self.assertEqual(payload["messages"][1:], PROMPT["messages"])
        self.assertEqual((key, timeout), (SECRET, 45))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["request_id"], "request-42")
        self.assertEqual(result["provider_calls"], 1)
        self.assertFalse(result["simulated"])
        self.assertEqual(result["usage"]["uncached_input_tokens"], 20)
        self.assertEqual(result["usage"]["cached_tokens"], 100)
        self.assertEqual(result["usage"]["raw"]["completion_tokens_details"], {"reasoning_tokens": 0})
        self.assertTrue(result["cache"]["hit"])
        self.assertFalse(result["cache"]["requested"])
        self.assertFalse(result["cache"]["applied"])
        self.assertNotIn(SECRET, json.dumps(result))

    async def test_unknown_cache_is_not_a_hit_and_observed_zero_is_a_miss(self):
        unknown, _ = await self.render()
        zero, _ = await self.render(response(usage={"prompt_cache_hit_tokens": 0}))
        self.assertIsNone(unknown["cache"]["hit"])
        self.assertIsNone(unknown["usage"]["cached_tokens"])
        self.assertFalse(zero["cache"]["hit"])
        self.assertTrue(zero["usage"]["cached_tokens_observed"])

    async def test_http_errors_and_redirects_never_echo_remote_bodies_or_retry(self):
        for status in (301, 401, 429, 500):
            with self.subTest(status=status):
                result, calls = await self.render({"error": {"message": SECRET}}, status=status)
                self.assertEqual(len(calls), 1)
                self.assertEqual(result["status"], "provider_unavailable")
                self.assertNotIn(SECRET, json.dumps(result))

    async def test_explicit_policy_and_token_cutoff_are_not_successful_prose(self):
        policy, _ = await self.render({"error": {"code": "policy_violation", "message": SECRET}}, status=403)
        filtered = response()
        filtered["choices"][0]["finish_reason"] = "content_filter"
        cutoff = deepcopy(filtered)
        cutoff["choices"][0]["finish_reason"] = "length"
        filtered_result, _ = await self.render(filtered)
        cutoff_result, _ = await self.render(cutoff)
        self.assertEqual(policy["status"], "policy_blocked")
        self.assertEqual(filtered_result["status"], "policy_blocked")
        self.assertEqual(cutoff_result["status"], "invalid_output")
        self.assertEqual(cutoff_result["reason_codes"], ["output_truncated"])

    async def test_same_shared_guardrails_sanitize_exposed_internal_fields(self):
        raw = response("你的 coords_avg 是内部数据。你可以慢慢辨认这一次留下的画面，给自己保留一些探索余地。")
        result, _ = await self.render(raw)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["output_status"], "sanitized")
        self.assertNotIn("coords_avg", result["text"])
        self.assertIn("internal_field", result["reason_codes"])

    async def test_invalid_json_empty_output_and_inconsistent_response_are_rejected(self):
        for body in (b"[]", b'{"choices":[],"choices":[]}', b'{"usage":NaN}',
                     b'{"usage":1e999}', b'{"choices":', b"\xff", b"{}"):
            with self.subTest(body=body):
                result, _ = await self.render(body=body)
                self.assertEqual(result["status"], "invalid_output")

    async def test_stream_is_bounded_and_closed_at_first_overflow(self):
        body = Chunks([b"a" * cloud.MAX_RESPONSE_BYTES, b"x", b"must not read"])
        result, calls = await self.render(body=body)
        self.assertEqual(result["status"], "invalid_output")
        self.assertEqual(body.read, 2)
        self.assertTrue(body.closed)
        self.assertEqual(len(calls), 1)
        declared = Chunks([b"not read"])
        result, _ = await self.render(body=declared,
                                      headers={"content-length": str(cloud.MAX_RESPONSE_BYTES + 1)})
        self.assertEqual(result["status"], "invalid_output")
        self.assertEqual(declared.read, 0)
        self.assertTrue(declared.closed)

    async def test_deadline_includes_body_stream_and_cleans_up(self):
        body = Chunks([b"{}"], delay=0.1)
        with patch.object(cloud, "REQUEST_TIMEOUT", 0.01):
            result, calls = await self.render(body=body)
        self.assertEqual(result["status"], "provider_unavailable")
        self.assertTrue(body.closed)
        self.assertEqual(len(calls), 1)

    async def test_network_exception_and_secret_echo_never_enter_generation(self):
        async def fail(*_args):
            raise OSError("Authorization: Bearer " + SECRET)

        failed = await cloud.generate_cloud(PROMPT, api_key=SECRET, transport=fail)
        echoed, _ = await self.render(response("这是你的 " + SECRET))
        metadata, _ = await self.render({**response(), "id": SECRET, "model": SECRET},
                                         headers={"x-request-id": SECRET})
        self.assertEqual(failed["status"], "provider_unavailable")
        self.assertEqual(echoed["status"], "invalid_output")
        self.assertIsNone(metadata["request_id"])
        self.assertEqual(metadata["model"], "deepseek-flash")
        for item in (failed, echoed, metadata):
            self.assertNotIn(SECRET, json.dumps(item))

    async def test_missing_key_and_invalid_admission_never_call_transport(self):
        async def fail(*_args):
            raise AssertionError("Unexpected transport call")

        missing = await cloud.generate_cloud(PROMPT, api_key="", transport=fail)
        self.assertEqual(missing["provider_calls"], 0)
        for prompt in ({**PROMPT, "max_tokens": cloud.MAX_OUTPUT_TOKENS + 1}, {**PROMPT, "max_tokens": True},
                       {**PROMPT, "system": "图" * 12000}):
            with self.assertRaises(ValueError):
                await cloud.generate_cloud(prompt, api_key=SECRET, transport=fail)
        with self.assertRaises(ValueError):
            await cloud.generate_cloud(PROMPT, api_key=SECRET, model="other", transport=fail)

    async def test_ffi_fetch_uses_abort_signal_and_refuses_redirects(self):
        options, deadlines = [], []

        class Reader:
            cancelled = released = False

            async def read(self):
                return SimpleNamespace(done=True)

            async def cancel(self):
                self.cancelled = True

            def releaseLock(self):
                self.released = True

        reader = Reader()

        async def fetch(url, value):
            options.append((url, value))
            return SimpleNamespace(status=200, headers={}, body=SimpleNamespace(getReader=lambda: reader))

        js = ModuleType("js")
        js.fetch = fetch
        js.Object = SimpleNamespace(fromEntries=object())
        js.AbortSignal = SimpleNamespace(timeout=lambda ms: deadlines.append(ms) or "timeout-signal")
        ffi = ModuleType("pyodide.ffi")
        ffi.to_js = lambda value, **_kwargs: value
        with patch.dict(sys.modules, {"js": js, "pyodide.ffi": ffi}):
            _, _, body = await cloud._worker_transport(cloud.DEEPSEEK_URL, b"{}", SECRET, 45)
            await body.aclose()
        self.assertEqual(deadlines, [45000])
        self.assertEqual(options[0][1]["redirect"], "manual")
        self.assertEqual(options[0][1]["signal"], "timeout-signal")
        self.assertTrue(reader.cancelled)
        self.assertTrue(reader.released)


class CompatibleHelperTests(unittest.TestCase):
    def test_sync_adapter_reuses_identical_pure_wire_and_result_helpers(self):
        config = {"base_url": "https://example.test/v1", "model": "fixture", "api_key": SECRET}
        payload = compatible_payload(PROMPT, config)
        self.assertNotIn(SECRET, json.dumps(payload))
        raw = response(usage={"prompt_tokens": 20, "prompt_cache_hit_tokens": 10,
                              "prompt_cache_miss_tokens": 10})
        expected = compatible_result(raw, "request-1", config)
        with patch("norn.providers._request_json", return_value=(raw, "request-1")) as request:
            actual = generate("openai-compatible", PROMPT, config=config)
        self.assertEqual(request.call_args.args[1], payload)
        for name in expected:
            self.assertEqual(actual[name], expected[name])
        self.assertEqual(actual["usage"]["cache_read_input_tokens"], 10)

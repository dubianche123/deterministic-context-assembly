"""Bounded, asynchronous DeepSeek transport for the public Python Worker.

The API key belongs only to this request's transport. It is never configuration,
an actor field, a prompt, or generation metadata. No retries or fallback occur.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import math
import re
import time

from .providers import (INVALID_TEXT, UNAVAILABLE_TEXT, _cache_metadata,
                        _policy_result, _usage, compatible_payload, compatible_result, localize_result)
from .ffi_compat import optional_js

DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-flash"
REQUEST_TIMEOUT = 45
MAX_PROMPT_BYTES = 32 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_TOKENS = 1600
_COUNTERS = ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens",
             "output_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")
_DETAILS = {"prompt_tokens_details": ("cached_tokens",),
            "completion_tokens_details": ("reasoning_tokens",)}


class _InvalidResponse(ValueError):
    pass


def _safe_identifier(value, secret):
    return (value if isinstance(value, str) and secret not in value
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", value) else None)


def _safe_usage(value):
    """Persist only documented numeric accounting, never arbitrary provider JSON."""
    if not isinstance(value, dict):
        return {}
    result = {key: value[key] for key in _COUNTERS
              if type(value.get(key)) is int and 0 <= value[key] <= 2**53 - 1}
    for name, counters in _DETAILS.items():
        details = value.get(name)
        if isinstance(details, dict):
            selected = {key: details[key] for key in counters
                        if type(details.get(key)) is int and 0 <= details[key] <= 2**53 - 1}
            if selected:
                result[name] = selected
    return result


def _pairs(items):
    result = {}
    for name, value in items:
        if name in result:
            raise _InvalidResponse("Duplicate response property")
        result[name] = value
    return result


def _nonfinite(_value):
    raise _InvalidResponse("Nonfinite response number")


def _float(value):
    parsed = float(value)
    if not math.isfinite(parsed):
        raise _InvalidResponse("Nonfinite response number")
    return parsed


def _json(raw):
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=_nonfinite, parse_float=_float)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise _InvalidResponse("Invalid provider JSON") from exc
    if type(value) is not dict:
        raise _InvalidResponse("Provider JSON must be an object")
    return value


async def _worker_transport(url, payload, api_key, timeout):
    """Use Workers fetch; its abort signal remains active during body reads."""
    from js import AbortSignal, Object, fetch
    from pyodide.ffi import to_js

    response = await fetch(url, to_js({
        # Workers supports manual/follow, but rejects redirect='error'. Return
        # redirects to our HTTP status check without forwarding credentials.
        "method": "POST", "body": payload.decode("utf-8"), "redirect": "manual",
        "headers": {"Authorization": "Bearer " + api_key, "Content-Type": "application/json",
                    "Accept": "application/json"},
        "signal": AbortSignal.timeout(int(timeout * 1000)),
    }, dict_converter=Object.fromEntries))
    headers = {name: optional_js(response.headers.get(name))
               for name in ("content-length", "request-id", "x-request-id")}

    class Body:
        def __init__(self):
            body = optional_js(response.body)
            self.reader = body.getReader() if body is not None else None
            self.complete = self.reader is None

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.complete:
                raise StopAsyncIteration
            part = await self.reader.read()
            if part.done:
                self.complete = True
                raise StopAsyncIteration
            value = part.value
            return bytes(value.to_py()) if hasattr(value, "to_py") else bytes(value)

        async def aclose(self):
            if self.reader is not None:
                try:
                    if not self.complete:
                        await self.reader.cancel()
                finally:
                    self.reader.releaseLock()
                    self.reader = None

    return int(response.status), headers, Body()


async def _receive(transport, payload, secret):
    status, headers, body = await transport(DEEPSEEK_URL, payload, secret, REQUEST_TIMEOUT)
    try:
        if type(status) is not int or not 100 <= status <= 599:
            raise _InvalidResponse("Invalid provider HTTP status")
        length = headers.get("content-length")
        if length is not None:
            if not isinstance(length, str) or not length.isascii() or not length.isdecimal():
                raise _InvalidResponse("Invalid provider content length")
            if len(length) > 10 or int(length) > MAX_RESPONSE_BYTES:
                raise _InvalidResponse("Oversized provider response")
        if isinstance(body, bytes):
            raw = body
            if len(raw) > MAX_RESPONSE_BYTES:
                raise _InvalidResponse("Oversized provider response")
        else:
            parts, total = [], 0
            async for chunk in body:
                if not isinstance(chunk, bytes):
                    raise _InvalidResponse("Invalid provider response chunk")
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    raise _InvalidResponse("Oversized provider response")
                parts.append(chunk)
            raw = b"".join(parts)
        return status, headers, raw
    finally:
        close = getattr(body, "aclose", None)
        if close is not None:
            await close()


def _failure(*, invalid=False):
    status = "invalid_output" if invalid else "provider_unavailable"
    return {"text": INVALID_TEXT if invalid else UNAVAILABLE_TEXT, "usage": _usage(),
            "blocked": invalid, "status": status, "output_status": status,
            "reason_codes": ["invalid_response" if invalid else "provider_error"]}


def _log_failure(error=None, *, http_status=None):
    record = {"event": "norn_provider_failure"}
    if http_status is not None:
        record["http_status"] = http_status
    if error is not None:
        record["error_type"] = type(error).__name__
        # Classify known runtime failures without copying exception text, which
        # may include request headers or other private transport values.
        message = str(error).lower()
        patterns = {"io_context": ("different request", "request context", "global scope", "i/o"),
                    "illegal_invocation": ("illegal invocation", "incorrect this"),
                    "redirect": ("redirect",), "abort": ("abort", "timeout"),
                    "network": ("network", "fetch failed", "connection", "tls"),
                    "invalid_request": ("invalid header", "invalid url", "requestinit", "abortsignal")}
        record["runtime_categories"] = [name for name, words in patterns.items()
                                        if any(word in message for word in words)]
        frames, trace = [], error.__traceback__
        while trace is not None:
            frames.append({"file": trace.tb_frame.f_code.co_filename.rsplit("/", 1)[-1],
                           "line": trace.tb_lineno})
            trace = trace.tb_next
        record["frames"] = frames[-5:]
    # No exception text, response body, URL, request headers, or local values.
    print(json.dumps(record, separators=(",", ":")))


def prepare_cloud_request(prompt: dict, model: str = DEEPSEEK_MODEL) -> bytes:
    """Encode an exact cloud request, bounded to 1600 output tokens and 32 KiB."""
    if model != DEEPSEEK_MODEL:
        raise ValueError("The cloud renderer requires deepseek-flash")
    payload = compatible_payload(deepcopy(prompt), {"model": model, "cache_enabled": False})
    max_tokens = payload.get("max_tokens")
    if type(max_tokens) is not int or not 1 <= max_tokens <= MAX_OUTPUT_TOKENS:
        raise ValueError("Cloud output tokens must be between 1 and 1600")
    # Official DeepSeek defaults to thinking mode. The prose renderer explicitly
    # disables it; reasoning_effort='off' is not its wire protocol.
    payload["thinking"] = {"type": "disabled"}
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_PROMPT_BYTES:
        raise ValueError("Cloud prompt exceeds 32 KiB")
    return encoded


async def generate_cloud(prompt: dict, *, api_key: str, model: str = DEEPSEEK_MODEL,
                         transport=None) -> dict:
    """Render once, with the same result contract as the synchronous adapter.

    Injectable transport: async (url, payload_bytes, api_key, timeout_seconds)
    returns (HTTP status, lower-case header mapping, bytes or async byte stream).
    Its complete fetch and body consumption share one 45-second deadline.
    """
    prompt = deepcopy(prompt)
    settings = {"model": model, "cache_enabled": False}
    encoded = prepare_cloud_request(prompt, model)
    started = time.perf_counter()
    attempted = bool(isinstance(api_key, str) and api_key.strip())
    result = _failure()
    if attempted:
        try:
            status, headers, raw = await asyncio.wait_for(
                _receive(transport or _worker_transport, encoded, api_key), timeout=REQUEST_TIMEOUT)
            if not 200 <= status < 300:
                _log_failure(http_status=status)
                # Never expose remote error bodies, URLs, credentials, or traces.
                try:
                    error = _json(raw).get("error", {})
                    policy = (isinstance(error, dict)
                              and error.get("code", error.get("type")) in
                              ("content_filter", "safety", "policy_violation"))
                except _InvalidResponse:
                    policy = False
                result = _policy_result(_usage()) if policy else _failure()
            else:
                decoded = _json(raw)
                decoded["usage"] = _safe_usage(decoded.get("usage"))
                decoded["model"] = _safe_identifier(decoded.get("model"), api_key) or model
                decoded["id"] = _safe_identifier(decoded.get("id"), api_key)
                request_id = (_safe_identifier(headers.get("request-id"), api_key)
                              or _safe_identifier(headers.get("x-request-id"), api_key))
                result = compatible_result(decoded, request_id, settings)
                if api_key in json.dumps(result, ensure_ascii=False):
                    result = _failure(invalid=True)
        except (_InvalidResponse, ValueError, KeyError, IndexError, TypeError, RecursionError) as exc:
            _log_failure(exc)
            result = _failure(invalid=True)
        except Exception as exc:
            _log_failure(exc)
            # Workers fetch raises a JS exception on abort/network/redirect.
            # Its string may contain credentials; only the category crosses out.
            result = _failure()
    cache = _cache_metadata("openai-compatible", prompt, settings)
    observed = result["usage"]["cache_read_input_tokens"]
    cache.update(hit=(observed > 0 if observed is not None else None),
                 note="DeepSeek manages prefix caching automatically; reuse is measured only by its token counters.")
    result = localize_result(result, prompt.get("language", "zh"))
    return {"model": model, "request_id": None, **result, "cache": cache,
            "provider": "openai-compatible", "simulated": False,
            "provider_calls": int(attempted),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3)}

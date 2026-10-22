"""Bounded, shared HTTP transport for offline evaluations only.

Share one instance between generation and judge workers. Its concurrency limit,
attempt budget, and proxy cooldown apply to every request, including retries.
Neither reports nor exception records contain credentials, URLs, or prose.
"""

from datetime import timezone
from email.utils import parsedate_to_datetime
import json
import math
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_FINISH_REASONS = {"stop", "length", "content_filter", "tool_calls", "function_call"}
_USAGE_FIELDS = {
    "prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens",
    "prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "cache_read_input_tokens",
    "cache_creation_input_tokens", "reasoning_tokens",
}
_DETAIL_FIELDS = {
    "prompt_tokens_details": {"cached_tokens", "audio_tokens"},
    "completion_tokens_details": {"reasoning_tokens", "audio_tokens", "accepted_prediction_tokens",
                                  "rejected_prediction_tokens"},
    "input_tokens_details": {"cached_tokens"},
    "output_tokens_details": {"reasoning_tokens"},
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A proxy redirect must never forward the bearer credential elsewhere.
        return None


class _ResponseTooLarge(ValueError):
    pass


def _number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def safe_usage(raw):
    """Only numeric token counts; upstream metadata and arbitrary nested fields stay private."""
    if not isinstance(raw, dict):
        return {}
    result = {name: raw[name] for name in _USAGE_FIELDS if _number(raw.get(name))}
    for name, allowed in _DETAIL_FIELDS.items():
        details = raw.get(name)
        if isinstance(details, dict):
            selected = {key: details[key] for key in allowed if _number(details.get(key))}
            if selected:
                result[name] = selected
    return result


class EvalTransport:
    def __init__(self, base_url, api_key, concurrency=6, attempts=2, timeout=180,
                 max_requests=256, *, opener=None, clock=time.monotonic, sleep=time.sleep,
                 rng=None, wall_clock=time.time, backoff_base=2, backoff_cap=30,
                 retry_after_cap=120, max_response_bytes=MAX_RESPONSE_BYTES,
                 cancel_event=None):
        if not isinstance(api_key, str) or not api_key.strip() or "\r" in api_key or "\n" in api_key:
            raise ValueError("api_key must be a nonempty credential string without line breaks")
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url must be an HTTP endpoint without credentials or query parameters")
        self._validate_int(concurrency, "concurrency", 1, 32)
        self._validate_int(attempts, "attempts", 1, 10)
        self._validate_int(max_requests, "max_requests", 1, 1000000)
        self._validate_int(max_response_bytes, "max_response_bytes", 1, MAX_RESPONSE_BYTES)
        self._validate_seconds(timeout, "timeout")
        for name, value in (("backoff_base", backoff_base), ("backoff_cap", backoff_cap),
                            ("retry_after_cap", retry_after_cap)):
            self._validate_seconds(value, name)
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._key = api_key
        self._concurrency = concurrency
        self._attempt_limit = attempts
        self._timeout = timeout
        self._max_requests = max_requests
        self._max_response_bytes = max_response_bytes
        self._opener = opener or urllib.request.build_opener(_NoRedirect()).open
        self._clock, self._sleep, self._wall_clock = clock, sleep, wall_clock
        self._cancel_event = cancel_event
        self._rng = rng or random.Random()
        self._backoff_base, self._backoff_cap = backoff_base, backoff_cap
        self._retry_after_cap = retry_after_cap
        self._slots = threading.BoundedSemaphore(concurrency)
        self._lock = threading.Lock()
        self._cooldown_until = 0.0
        self._stats = {"calls": 0, "requests": 0, "retries": 0, "succeeded": 0, "failed": 0,
                       "active": 0, "max_active": 0, "budget_rejections": 0,
                       "cancelled_calls": 0, "retry_delay_seconds": 0.0,
                       "http_status_counts": {}, "usage": {}}

    @staticmethod
    def _validate_int(value, name, lower, upper):
        if not isinstance(value, int) or isinstance(value, bool) or not lower <= value <= upper:
            raise ValueError(f"{name} is outside its supported range")

    @staticmethod
    def _validate_seconds(value, name):
        if not _number(value) or value <= 0:
            raise ValueError(f"{name} must be a positive finite number")

    def _label(self, value):
        if value is None:
            return None
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}", value) and "://" not in value and not (self._key and self._key in value):
            return value
        return "redacted"

    def _summary(self, payload):
        thinking = payload.get("thinking")
        return {"model": self._label(payload.get("model")),
                "max_tokens": payload.get("max_tokens") if _number(payload.get("max_tokens")) else None,
                "reasoning_effort": self._label(payload.get("reasoning_effort")),
                "thinking": self._label(thinking.get("type")) if isinstance(thinking, dict) else None}

    def _cancelled(self):
        return self._cancel_event is not None and self._cancel_event.is_set()

    def _acquire(self, local_deadline):
        # Recheck after acquisition: another request may publish a cooldown while
        # this thread waits for a permit. No permit is held during backoff sleep.
        while True:
            if self._cancelled():
                return False
            with self._lock:
                remaining = max(local_deadline, self._cooldown_until) - self._clock()
            if remaining > 0:
                if self._cancel_event is None:
                    self._sleep(remaining)
                elif self._sleep is time.sleep:
                    self._cancel_event.wait(min(remaining, 0.1))
                else:
                    # Injected clocks/sleepers can advance virtual time while
                    # retaining the same cancellation boundary as real waits.
                    self._sleep(min(remaining, 0.1))
                continue
            if self._cancel_event is None:
                self._slots.acquire()
            elif not self._slots.acquire(timeout=0.1):
                continue
            if self._cancelled():
                self._slots.release()
                return False
            with self._lock:
                remaining = max(local_deadline, self._cooldown_until) - self._clock()
            if remaining <= 0:
                return True
            self._slots.release()

    def _backoff(self, attempt):
        base = min(self._backoff_cap, self._backoff_base * 2 ** min(attempt - 1, 20))
        with self._lock:
            draw = self._rng.random() if hasattr(self._rng, "random") else self._rng()
        return min(self._backoff_cap, base * (1 + 0.25 * max(0, min(1, draw))))

    def _retry_after(self, headers):
        value = headers.get("Retry-After") if headers is not None else None
        if not isinstance(value, str):
            return None
        try:
            seconds = float(value)
        except ValueError:
            try:
                date = parsedate_to_datetime(value)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                seconds = date.timestamp() - self._wall_clock()
            except (ValueError, TypeError, OverflowError):
                return None
        return min(self._retry_after_cap, max(0, seconds)) if math.isfinite(seconds) else None

    def _read(self, response):
        content_length = response.headers.get("Content-Length") if getattr(response, "headers", None) is not None else None
        if isinstance(content_length, str) and content_length.isdecimal() and int(content_length) > self._max_response_bytes:
            raise _ResponseTooLarge()
        data = bytearray()
        while len(data) <= self._max_response_bytes:
            chunk = response.read(min(65536, self._max_response_bytes + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > self._max_response_bytes:
            raise _ResponseTooLarge()
        raw = json.loads(data.decode("utf-8"))
        if not isinstance(raw, dict):
            raise ValueError()
        return raw

    def _record_result(self, entry):
        with self._lock:
            status = entry.get("http_status")
            if status is not None:
                counts = self._stats["http_status_counts"]
                counts[str(status)] = counts.get(str(status), 0) + 1
            usage = entry.get("usage", {})
            totals = self._stats["usage"]
            for name, value in usage.items():
                if isinstance(value, dict):
                    details = totals.setdefault(name, {})
                    for key, number in value.items():
                        details[key] = details.get(key, 0) + number
                else:
                    totals[name] = totals.get(name, 0) + value

    @staticmethod
    def _completion_choice(raw):
        choices = raw.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ValueError()
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict):
            raise ValueError()
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise ValueError()
        finish = choice.get("finish_reason")
        if finish is not None and not isinstance(finish, str):
            raise ValueError()
        return choice

    def complete(self, payload, timeout=None, attempts=None):
        """Return a private decoded response and safe per-attempt records.

        Budget exhaustion or cancellation returns ``(None, logs)`` without a
        new dispatch. An active successful response is returned even after
        cancellation, so its caller can persist the completed work. HTTP 401,
        403, malformed responses, and oversized responses are never retried.
        """
        timeout = self._timeout if timeout is None else timeout
        attempts = self._attempt_limit if attempts is None else attempts
        self._validate_seconds(timeout, "timeout")
        self._validate_int(attempts, "attempts", 1, 10)
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        summary = self._summary(payload)
        log = []
        local_deadline = 0.0
        with self._lock:
            self._stats["calls"] += 1
        for attempt in range(1, attempts + 1):
            entry = {**summary, "attempt": attempt, "dispatched": False, "ok": False,
                     "http_status": None, "elapsed_ms": 0, "retry_delay_seconds": 0.0}
            with self._lock:
                cancelled = self._cancelled()
                already_exhausted = not cancelled and self._stats["requests"] >= self._max_requests
                if cancelled:
                    self._stats["cancelled_calls"] += 1
                elif already_exhausted:
                    self._stats["budget_rejections"] += 1
            if cancelled or already_exhausted:
                entry["error"] = "cancelled" if cancelled else "budget_exhausted"
                log.append(entry)
                break
            if not self._acquire(local_deadline):
                with self._lock:
                    self._stats["cancelled_calls"] += 1
                entry["error"] = "cancelled"
                log.append(entry)
                break
            with self._lock:
                cancelled = self._cancelled()
                exhausted = not cancelled and self._stats["requests"] >= self._max_requests
                if cancelled:
                    self._stats["cancelled_calls"] += 1
                elif exhausted:
                    self._stats["budget_rejections"] += 1
                else:
                    self._stats["requests"] += 1
                    self._stats["retries"] += int(attempt > 1)
                    self._stats["active"] += 1
                    self._stats["max_active"] = max(self._stats["max_active"], self._stats["active"])
            entry["dispatched"] = not (cancelled or exhausted)
            if cancelled or exhausted:
                self._slots.release()
                entry["error"] = "cancelled" if cancelled else "budget_exhausted"
                log.append(entry)
                break
            started = self._clock()
            retryable = False
            headers = None
            raw = None
            response = None
            try:
                request = urllib.request.Request(self._url, body,
                    {"Content-Type": "application/json", "Authorization": "Bearer " + self._key})
                response = self._opener(request, timeout=timeout)
                status = getattr(response, "status", None)
                status = int(response.getcode() if status is None else status)
                entry["http_status"] = status
                headers = getattr(response, "headers", None)
                if not 200 <= status < 300:
                    entry["error"] = "http_error"
                    retryable = status == 429 or 500 <= status <= 599
                else:
                    raw = self._read(response)
                    choice = self._completion_choice(raw)
                    entry["usage"] = safe_usage(raw.get("usage"))
                    entry["response_model"] = self._label(raw.get("model"))
                    finish = choice.get("finish_reason")
                    entry["finish_reason"] = finish if finish in _FINISH_REASONS else "unknown"
                    entry["ok"] = True
            except urllib.error.HTTPError as exc:
                response = exc
                entry["error"] = "http_error"
                entry["http_status"] = int(exc.code)
                headers = exc.headers
                retryable = exc.code == 429 or 500 <= exc.code <= 599
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
                entry["error"] = "network_error"
                retryable = True
            except _ResponseTooLarge:
                entry["error"] = "response_too_large"
            except (ValueError, UnicodeError, TypeError):
                entry["error"] = "invalid_response"
            finally:
                if response is not None:
                    try:
                        response.close()
                    except (OSError, ValueError):
                        pass
                entry["elapsed_ms"] = round(max(0, self._clock() - started) * 1000)
                # Publish shared throttling before releasing the permit, even
                # when this caller has exhausted its own retry allowance.
                if retryable:
                    delay = self._backoff(attempt)
                    if entry["http_status"] in (429, 503):
                        retry_after = self._retry_after(headers)
                        if retry_after is not None:
                            delay = max(delay, retry_after)
                            entry["retry_after_seconds"] = retry_after
                        with self._lock:
                            self._cooldown_until = max(self._cooldown_until, self._clock() + delay)
                    if attempt < attempts:
                        entry["retry_delay_seconds"] = round(delay, 6)
                        local_deadline = self._clock() + delay
                        with self._lock:
                            self._stats["retry_delay_seconds"] += delay
                with self._lock:
                    self._stats["active"] -= 1
                self._slots.release()
            self._record_result(entry)
            log.append(entry)
            if entry["ok"]:
                with self._lock:
                    self._stats["succeeded"] += 1
                return raw, log
            if not retryable:
                break
        with self._lock:
            self._stats["failed"] += 1
        return None, log

    def chat(self, model, messages, reasoning=None, max_tokens=6000, temperature=0,
             timeout=None, attempts=None):
        payload = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        if reasoning is not None:
            payload["reasoning_effort"] = reasoning
        raw, log = self.complete(payload, timeout=timeout, attempts=attempts)
        if raw is None:
            return None, None, log
        try:
            choice = self._completion_choice(raw)
            content = choice["message"].get("content") or ""
            if not isinstance(content, str):
                raise ValueError()
            finish = choice.get("finish_reason")
            return content, finish if finish in _FINISH_REASONS else None, log
        except (KeyError, IndexError, TypeError, ValueError):
            log[-1]["ok"] = False
            log[-1]["error"] = "invalid_response"
            with self._lock:
                self._stats["succeeded"] -= 1
                self._stats["failed"] += 1
            return None, None, log

    def snapshot(self):
        with self._lock:
            result = json.loads(json.dumps(self._stats))
            result.update({"concurrency": self._concurrency, "attempts": self._attempt_limit,
                           "max_requests": self._max_requests,
                           "remaining_requests": self._max_requests - self._stats["requests"],
                           "budget_exhausted": self._stats["requests"] >= self._max_requests,
                           "cooldown_remaining_seconds": round(max(0, self._cooldown_until - self._clock()), 6)})
            result["retry_delay_seconds"] = round(result["retry_delay_seconds"], 6)
            return result

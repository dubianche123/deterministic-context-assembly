import concurrent.futures
from datetime import datetime, timezone
from email.utils import format_datetime
import io
import json
import threading
import unittest
import urllib.error

from scripts.eval_transport import EvalTransport, safe_usage


SECRET = "sk-test-private-credential"
PRIVATE_PROMPT = "Synthetic private prompt which must not enter logs"
BASE = "https://private-proxy.example/v1"


class Response(io.BytesIO):
    def __init__(self, raw=None, *, body=None, status=200, headers=None):
        if body is None:
            body = json.dumps(raw if raw is not None else {
                "model": "cn:deepseek-v4.1-flash", "choices": [{
                    "message": {"content": "a complete answer"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            }).encode()
        super().__init__(body)
        self.status = status
        self.headers = headers or {}


class FakeClock:
    def __init__(self):
        self.now = 0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, delay):
        self.sleeps.append(delay)
        self.now += delay


class EvalTransportTests(unittest.TestCase):
    def transport(self, opener, **kwargs):
        return EvalTransport(BASE, SECRET, opener=opener, rng=lambda: 0, **kwargs)

    def test_concurrency_is_shared_across_all_attempts_and_chats(self):
        self.assert_shared_concurrency(2, concurrency=2)

    def test_default_allows_six_concurrent_requests(self):
        self.assert_shared_concurrency(6)

    def assert_shared_concurrency(self, limit, **options):
        entered = threading.Barrier(7)
        slots_open = threading.Event()
        excess_open = threading.Event()
        release = threading.Event()
        lock = threading.Lock()
        counts = {"active": 0, "peak": 0, "total": 0}

        def opener(request, timeout):
            with lock:
                counts["active"] += 1
                counts["total"] += 1
                counts["peak"] = max(counts["peak"], counts["active"])
                if counts["active"] == limit:
                    slots_open.set()
                if counts["active"] > limit:
                    excess_open.set()
            if not release.wait(3):
                raise AssertionError("test did not release concurrent HTTP requests")
            with lock:
                counts["active"] -= 1
            return Response()

        transport = self.transport(opener, **options)

        def call(index):
            entered.wait(3)
            return transport.chat("judge" if index % 2 else "generation", [])

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(call, index) for index in range(6)]
            try:
                entered.wait(3)
                self.assertTrue(slots_open.wait(2))
                self.assertFalse(excess_open.wait(0.1))
                with lock:
                    self.assertEqual(counts["total"], limit)
            finally:
                release.set()
            self.assertTrue(all(f.result(3)[1] == "stop" for f in futures))
        self.assertEqual(counts["peak"], limit)
        self.assertEqual(transport.snapshot()["max_active"], limit)
        self.assertEqual(transport.snapshot()["requests"], 6)

    def test_429_publishes_shared_cooldown_and_releases_permit(self):
        now = [0.0]
        lock = threading.Lock()
        condition = threading.Condition(lock)
        sleepers = [0]
        two_sleeping = threading.Event()
        release_sleep = threading.Event()
        dispatches = []

        def clock():
            with lock:
                return now[0]

        def sleep(delay):
            with condition:
                sleepers[0] += 1
                condition.notify_all()
                if sleepers[0] >= 2:
                    two_sleeping.set()
            if not release_sleep.wait(3):
                raise AssertionError("shared cooldown was not released")

        def opener(request, timeout):
            with lock:
                dispatches.append(now[0])
                count = len(dispatches)
            if count == 1:
                raise urllib.error.HTTPError(BASE, 429, "secret upstream body " + SECRET,
                                             {"Retry-After": "7"}, None)
            return Response()

        transport = self.transport(opener, concurrency=1, clock=clock, sleep=sleep)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(transport.chat, "judge", [])
            with condition:
                self.assertTrue(condition.wait_for(lambda: sleepers[0] >= 1, 2))
            # While the first call backs off, another call sees the same
            # deadline. It can acquire the sole semaphore: backoff holds none.
            self.assertTrue(transport._slots.acquire(blocking=False))
            transport._slots.release()
            second = pool.submit(transport.chat, "generation", [])
            try:
                self.assertTrue(two_sleeping.wait(2))
                with lock:
                    self.assertEqual(dispatches, [0])
                    now[0] = 7
            finally:
                release_sleep.set()
            first_result, second_result = first.result(3), second.result(3)
        self.assertEqual(first_result[2][0]["retry_after_seconds"], 7)
        self.assertEqual(first_result[2][0]["retry_delay_seconds"], 7)
        self.assertEqual(second_result[1], "stop")
        self.assertEqual(dispatches, [0, 7, 7])

    def test_retry_after_date_and_final_503_throttle_the_next_call(self):
        clock = FakeClock()
        count = [0]
        date = format_datetime(datetime.fromtimestamp(1020, tz=timezone.utc), usegmt=True)

        def opener(request, timeout):
            count[0] += 1
            return Response(status=503, headers={"Retry-After": date}) if count[0] == 1 else Response()

        transport = self.transport(opener, attempts=1, clock=clock, sleep=clock.sleep,
                                   wall_clock=lambda: 1000)
        first = transport.chat("judge", [])
        self.assertIsNone(first[0])
        self.assertEqual(first[2][0]["retry_after_seconds"], 20)
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(transport.chat("judge", [])[1], "stop")
        self.assertEqual(clock.sleeps, [20])

    def test_retry_after_and_exponential_backoff_are_bounded(self):
        clock = FakeClock()
        count = [0]

        def opener(request, timeout):
            count[0] += 1
            return Response(status=503, headers={"Retry-After": "999999"})

        transport = self.transport(opener, attempts=3, clock=clock, sleep=clock.sleep,
                                   retry_after_cap=9, backoff_base=3, backoff_cap=5)
        result = transport.chat("judge", [])
        self.assertEqual(count[0], 3)
        self.assertEqual(clock.sleeps, [9, 9])
        self.assertEqual([entry["retry_after_seconds"] for entry in result[2]], [9, 9, 9])

    def test_network_and_5xx_retry_but_auth_errors_do_not(self):
        for failure, expected_count in ((urllib.error.URLError(SECRET), 2),
                                        (TimeoutError(SECRET), 2), (503, 2), (500, 2),
                                        (401, 1), (403, 1), (400, 1)):
            with self.subTest(failure=type(failure).__name__ if isinstance(failure, Exception) else failure):
                clock = FakeClock()
                count = [0]

                def opener(request, timeout):
                    count[0] += 1
                    if count[0] > 1:
                        return Response()
                    if isinstance(failure, Exception):
                        raise failure
                    raise urllib.error.HTTPError(BASE, failure, SECRET, {}, None)

                transport = self.transport(opener, clock=clock, sleep=clock.sleep)
                result = transport.chat("judge", [])
                self.assertEqual(count[0], expected_count)
                self.assertEqual(result[1] == "stop", expected_count == 2)

    def test_logs_keep_request_configuration_and_numeric_usage_only(self):
        requests = []
        raw = {"model": "cn:deepseek-v4.1-flash", "choices": [{
            "message": {"content": SECRET + PRIVATE_PROMPT}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30,
                      "prompt_cache_hit_tokens": 7, "prompt_cache_miss_tokens": 13,
                      "completion_tokens_details": {"reasoning_tokens": 9, "secret": SECRET},
                      "prompt_tokens_details": {"cached_tokens": 7}, "secret": SECRET,
                      "reasoning_tokens": True, "output_tokens": -1, "input_tokens": float("inf")}}

        def opener(request, timeout):
            requests.append((json.loads(request.data), timeout))
            return Response(raw)

        transport = self.transport(opener)
        result = transport.chat("cn:kimi-k2.7", [{"role": "user", "content": PRIVATE_PROMPT}],
                                reasoning="low", max_tokens=6000, timeout=42)
        self.assertEqual(result[0], SECRET + PRIVATE_PROMPT)  # private return value remains usable
        entry = result[2][0]
        self.assertEqual(entry["model"], "cn:kimi-k2.7")
        self.assertEqual(entry["response_model"], "cn:deepseek-v4.1-flash")
        self.assertEqual(entry["max_tokens"], 6000)
        self.assertEqual(entry["reasoning_effort"], "low")
        self.assertEqual(entry["usage"]["completion_tokens_details"], {"reasoning_tokens": 9})
        self.assertNotIn("reasoning_tokens", entry["usage"])
        self.assertNotIn("output_tokens", entry["usage"])
        self.assertNotIn("input_tokens", entry["usage"])
        self.assertEqual(requests[0][1], 42)
        encoded = json.dumps({"attempts": result[2], "stats": transport.snapshot()})
        for private in (SECRET, PRIVATE_PROMPT, BASE, "Authorization", "Bearer", "headers"):
            self.assertNotIn(private, encoded)

    def test_exception_message_response_metadata_and_finish_cannot_leak(self):
        clock = FakeClock()
        count = [0]

        def opener(request, timeout):
            count[0] += 1
            if count[0] == 1:
                raise urllib.error.URLError(SECRET + BASE + PRIVATE_PROMPT)
            return Response({"model": BASE + SECRET, "choices": [{
                "message": {"content": "ok"}, "finish_reason": SECRET}], "usage": {"raw": SECRET}})

        transport = self.transport(opener, clock=clock, sleep=clock.sleep)
        text, finish, logs = transport.chat("judge", [])
        self.assertEqual(text, "ok")
        self.assertIsNone(finish)
        encoded = json.dumps({"logs": logs, "snapshot": transport.snapshot()})
        for private in (SECRET, BASE, PRIVATE_PROMPT):
            self.assertNotIn(private, encoded)
        self.assertEqual(logs[-1]["finish_reason"], "unknown")
        self.assertEqual(logs[-1]["response_model"], "redacted")

    def test_response_read_is_bounded_and_invalid_json_does_not_retry(self):
        for response, error in ((Response(body=b"x" * 129), "response_too_large"),
                                (Response(body=b"x", headers={"Content-Length": "129"}), "response_too_large"),
                                (Response(body=b"not-json"), "invalid_response")):
            with self.subTest(error=error):
                count = [0]

                def opener(request, timeout):
                    count[0] += 1
                    return response

                transport = self.transport(opener, max_response_bytes=128)
                result = transport.chat("judge", [])
                self.assertIsNone(result[0])
                self.assertEqual(result[2][0]["error"], error)
                self.assertEqual(count[0], 1)

    def test_global_attempt_budget_includes_retries_and_fails_without_sleep(self):
        clock = FakeClock()
        count = [0]

        def opener(request, timeout):
            count[0] += 1
            return Response(status=503)

        transport = self.transport(opener, max_requests=1, clock=clock, sleep=clock.sleep)
        first = transport.chat("judge", [])
        second = transport.chat("generation", [])
        self.assertEqual(count[0], 1)
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(first[2][-1]["error"], "budget_exhausted")
        self.assertFalse(first[2][-1]["dispatched"])
        self.assertEqual(second[2][0]["error"], "budget_exhausted")
        self.assertEqual(transport.snapshot()["requests"], 1)
        self.assertEqual(transport.snapshot()["remaining_requests"], 0)
        self.assertTrue(transport.snapshot()["budget_exhausted"])

    def test_complete_preserves_payload_and_private_raw_response(self):
        seen = []

        def opener(request, timeout):
            seen.append(json.loads(request.data))
            return Response()

        transport = self.transport(opener)
        payload = {"model": "deepseek-flash", "messages": [], "max_tokens": 1600,
                   "thinking": {"type": "disabled"}, "temperature": 0.5}
        raw, logs = transport.complete(payload)
        self.assertEqual(seen, [payload])
        self.assertEqual(raw["choices"][0]["message"]["content"], "a complete answer")
        self.assertEqual(logs[0]["thinking"], "disabled")
        self.assertEqual(transport.snapshot()["usage"]["total_tokens"], 14)

    def test_unsafe_endpoints_and_configuration_rejected_without_values(self):
        for base in ("file:///private/secret", "https://user:pass@proxy.example", "https://proxy.example?key=secret"):
            with self.subTest(base=base):
                with self.assertRaises(ValueError) as failure:
                    EvalTransport(base, SECRET)
                self.assertNotIn(base, str(failure.exception))
        with self.assertRaises(ValueError):
            self.transport(lambda *a, **k: Response(), concurrency=0)

    def test_adversarial_numeric_usage_stays_bounded_and_json_serializable(self):
        self.assertEqual(safe_usage({"prompt_tokens": 10 ** 400, "completion_tokens": float("nan"),
                                     "total_tokens": True, "reasoning_tokens": -2}), {})

    def test_complete_rejects_malformed_choice_before_marking_success(self):
        malformed = [
            {}, {"choices": []}, {"choices": {}}, {"choices": [None]},
            {"choices": [{"message": []}]},
            {"choices": [{"message": {"content": []}, "finish_reason": "stop"}]},
            {"choices": [{"message": {"content": False}, "finish_reason": "stop"}]},
            *({"choices": [{"message": {"content": "text"}, "finish_reason": finish}]}
              for finish in ({"private": SECRET}, [SECRET], False, 0)),
        ]
        for raw in malformed:
            with self.subTest(raw=raw):
                count = [0]

                def opener(request, timeout):
                    count[0] += 1
                    return Response(raw)

                transport = self.transport(opener)
                result, logs = transport.complete({"model": "judge", "messages": []})
                self.assertIsNone(result)
                self.assertFalse(logs[0]["ok"])
                self.assertEqual(logs[0]["error"], "invalid_response")
                self.assertEqual(count[0], 1)
                self.assertEqual(transport.snapshot()["succeeded"], 0)
                self.assertEqual(transport.snapshot()["failed"], 1)
                self.assertNotIn(SECRET, json.dumps(logs))

    def test_key_type_and_header_injection_rejected_before_any_dispatch(self):
        dispatches = []

        def opener(request, timeout):
            dispatches.append(True)
            return Response()

        for key in (None, False, 42, SECRET.encode(), {"key": SECRET}, [SECRET], "", "  ",
                    SECRET + "\nInjected: true", SECRET + "\rInjected: true"):
            with self.subTest(key_type=type(key).__name__):
                with self.assertRaises(ValueError) as failure:
                    EvalTransport(BASE, key, opener=opener)
                self.assertNotIn(SECRET, str(failure.exception))
        self.assertEqual(dispatches, [])

    def test_cancellation_interrupts_permit_wait_but_keeps_active_success(self):
        cancel = threading.Event()
        active_started = threading.Event()
        release_active = threading.Event()
        waiting_for_permit = threading.Event()
        calls = []

        def opener(request, timeout):
            calls.append(True)
            active_started.set()
            if not release_active.wait(3):
                raise AssertionError("active request was not released")
            return Response()

        transport = self.transport(opener, concurrency=1, cancel_event=cancel)
        real_slots = transport._slots

        class ObservedSemaphore:
            def acquire(self, *args, **kwargs):
                if active_started.is_set():
                    waiting_for_permit.set()
                return real_slots.acquire(*args, **kwargs)

            def release(self):
                return real_slots.release()

        transport._slots = ObservedSemaphore()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            active = pool.submit(transport.chat, "active", [])
            self.assertTrue(active_started.wait(2))
            pending = pool.submit(transport.chat, "pending", [])
            try:
                self.assertTrue(waiting_for_permit.wait(2))
                self.assertFalse(pending.done())
                cancel.set()
                text, finish, logs = pending.result(1)
                self.assertIsNone(text)
                self.assertIsNone(finish)
                self.assertEqual(logs[-1]["error"], "cancelled")
                self.assertFalse(logs[-1]["dispatched"])
                self.assertEqual(calls, [True])
            finally:
                release_active.set()
            # Cancelling the waiting caller must not discard the already paid,
            # completed active response: RunState needs it for persistence.
            self.assertEqual(active.result(2)[:2], ("a complete answer", "stop"))
        self.assertEqual(transport.snapshot()["requests"], 1)
        self.assertEqual(transport.snapshot()["succeeded"], 1)
        self.assertEqual(transport.snapshot()["cancelled_calls"], 1)
        self.assertEqual(transport.snapshot()["active"], 0)

    def test_cancellation_interrupts_real_retry_after_wait_without_new_http(self):
        sleep_started = threading.Event()

        class ObservedCancelEvent(threading.Event):
            def wait(self, timeout=None):
                sleep_started.set()
                return super().wait(timeout)

        cancel = ObservedCancelEvent()
        calls = []

        def opener(request, timeout):
            calls.append(True)
            return Response(status=429, headers={"Retry-After": "60"})

        transport = self.transport(opener, cancel_event=cancel)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(transport.chat, "judge", [])
            self.assertTrue(sleep_started.wait(2))
            cancel.set()
            text, finish, logs = future.result(1)
        self.assertIsNone(text)
        self.assertIsNone(finish)
        self.assertEqual(calls, [True])
        self.assertEqual([row["http_status"] for row in logs], [429, None])
        self.assertEqual(logs[-1]["error"], "cancelled")
        self.assertFalse(logs[-1]["dispatched"])
        self.assertEqual(transport.snapshot()["requests"], 1)
        self.assertEqual(transport.snapshot()["retries"], 0)
        self.assertEqual(transport.snapshot()["active"], 0)

    def test_cancellation_before_call_is_non_dispatched_and_custom_clock_still_works(self):
        cancel = threading.Event()
        cancel.set()
        calls = []

        def opener(request, timeout):
            calls.append(True)
            return Response()

        transport = self.transport(opener, cancel_event=cancel)
        text, finish, logs = transport.chat("judge", [])
        self.assertEqual((text, finish), (None, None))
        self.assertEqual(logs[0]["error"], "cancelled")
        self.assertFalse(logs[0]["dispatched"])
        self.assertEqual(transport.snapshot()["requests"], 0)
        self.assertEqual(calls, [])

        cancel.clear()
        clock = FakeClock()
        attempt = [0]

        def throttled(request, timeout):
            attempt[0] += 1
            return Response(status=503, headers={"Retry-After": "1"}) if attempt[0] == 1 else Response()

        transport = self.transport(throttled, cancel_event=cancel, clock=clock, sleep=clock.sleep)
        self.assertEqual(transport.chat("judge", [])[1], "stop")
        self.assertEqual(attempt[0], 2)
        self.assertTrue(all(delay <= 0.1 for delay in clock.sleeps))


if __name__ == "__main__":
    unittest.main()

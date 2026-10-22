import concurrent.futures
import copy
import http.client
import io
import json
import math
import threading
import traceback
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from norn.jev import (ALLOWED_MODELS, DEFAULT_MODEL, ENDPOINT, MAX_REQUEST_BYTES,
                      MAX_RESPONSE_BYTES, JevClient, JevError)


SECRET = "typesafe-test-private-credential"
PRIVATE = "Synthetic prefix which must never enter an error or report"
QUESTIONS = {
    "next_choice": {
        "type": "choice", "instructions": "Predict the next choice from this prefix.",
        "criteria": {"card_a": "An illustrated red tree", "card_b": "A blue photograph",
                     "__STOP__": "Stop selecting cards"},
    },
}


def valid_payload(model=DEFAULT_MODEL):
    return {
        "model": model,
        "answers": {"next_choice": {
            "type": "choice", "choice": "card_a",
            "probabilities": {"card_a": .6, "card_b": .3, "__STOP__": .1},
            "confidence": .8,
        }},
        "usage": {"input_tokens": 50, "output_tokens": 12},
    }


class Response(io.BytesIO):
    def __init__(self, payload=None, *, body=None, status=200, url=ENDPOINT, chunk_size=None, headers=None):
        if body is None:
            body = json.dumps(valid_payload() if payload is None else payload).encode("utf-8")
        super().__init__(body)
        self.status, self.url, self.chunk_size = status, url, chunk_size
        self.headers = headers or {}
        self.read_calls = []

    def geturl(self):
        return self.url

    def read(self, size=-1):
        self.read_calls.append(size)
        return super().read(min(size, self.chunk_size) if self.chunk_size is not None else size)


class JevTests(unittest.TestCase):
    def assert_safe_error(self, code, operation):
        with self.assertRaises(JevError) as raised:
            operation()
        error = raised.exception
        self.assertEqual(error.code, code)
        text = str(error) + repr(error) + "".join(traceback.format_exception(type(error), error, error.__traceback__))
        self.assertNotIn(SECRET, text)
        self.assertNotIn(PRIVATE, text)
        return error

    def client(self, response=None, **options):
        sent = []

        def opener(request, timeout):
            sent.append((request, timeout))
            return Response() if response is None else response

        return JevClient(SECRET, opener=opener, **options), sent

    def test_fixed_endpoint_and_key_only_in_authorization(self):
        client, sent = self.client()
        result = client.decide({"prefix": PRIVATE}, QUESTIONS)
        self.assertEqual(client.calls, 1)
        request, timeout = sent[0]
        self.assertEqual(request.full_url, ENDPOINT)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + SECRET)
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(timeout, 15)
        self.assertNotIn(SECRET, request.data.decode("utf-8"))
        self.assertNotIn(SECRET, request.full_url)
        self.assertEqual(json.loads(request.data), {"model": DEFAULT_MODEL,
                                                  "state": {"prefix": PRIVATE}, "questions": QUESTIONS})
        self.assertEqual(result["model"], DEFAULT_MODEL)
        self.assertEqual(result["answers"], valid_payload()["answers"])
        self.assertEqual(result["usage"], {"input_tokens": 50, "output_tokens": 12})
        self.assertEqual(result["provider_calls"], 1)
        self.assertGreaterEqual(result["elapsed_ms"], 0)
        self.assertTrue(math.isfinite(result["elapsed_ms"]))
        self.assertNotIn(SECRET, repr(client))
        self.assertNotIn(PRIVATE, json.dumps(result))

    def test_explicit_preview_model_and_maximum_timeout(self):
        self.assertEqual(ALLOWED_MODELS, {"jev-1.13.0", "jev-1.14.0"})
        client, sent = self.client(Response(valid_payload("jev-1.14.0")), model="jev-1.14.0", timeout=30)
        self.assertEqual(client.decide({}, QUESTIONS)["model"], "jev-1.14.0")
        self.assertEqual(sent[0][1], 30)

    def test_invalid_config_never_dispatches(self):
        for options in ({"model": "jev-latest"}, {"model": "jev-1.13"}, {"model": "jev-1.15.0"},
                        {"timeout": 30.001}, {"timeout": 0}, {"timeout": True}, {"timeout": float("nan")},
                        {"timeout": float("inf")}, {"max_calls": -1}, {"max_calls": 1.5},
                        {"max_calls": True}, {"opener": object()}):
            with self.subTest(options=options):
                self.assert_safe_error("invalid_config", lambda: JevClient(SECRET, **options))
        for secret in ("", None, "bad\r\nkey", "white space", "\tkey", "not-ascii-密钥"):
            with self.subTest(secret_type=type(secret).__name__):
                self.assert_safe_error("invalid_config", lambda: JevClient(secret))

    def test_default_transport_disables_redirects(self):
        fake = type("FakeOpener", (), {"open": lambda self, request, timeout: Response()})()
        with patch("norn.jev.urllib.request.build_opener", return_value=fake) as build:
            JevClient(SECRET).decide({}, QUESTIONS)
        handler = build.call_args.args[0]
        self.assertIsInstance(handler, urllib.request.HTTPRedirectHandler)
        self.assertIsNone(handler.redirect_request(None, None, 302, SECRET, {}, "https://attacker.example/"))

    def test_default_call_bound_and_zero_budget(self):
        client, sent = self.client()
        client.decide({}, QUESTIONS)
        self.assert_safe_error("call_limit", lambda: client.decide({}, QUESTIONS))
        self.assertEqual(client.calls, 1)
        self.assertEqual(len(sent), 1)
        client, sent = self.client(max_calls=0)
        self.assert_safe_error("call_limit", lambda: client.decide({}, QUESTIONS))
        self.assertEqual(client.calls, 0)
        self.assertEqual(sent, [])

    def test_dispatch_budget_is_atomic_across_threads(self):
        client, sent = self.client()
        barrier = threading.Barrier(8)

        def choose():
            barrier.wait(3)
            try:
                return client.decide({}, QUESTIONS)["provider_calls"]
            except JevError as error:
                return error.code

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: choose(), range(8)))
        self.assertEqual(results.count(1), 1)
        self.assertEqual(results.count("call_limit"), 7)
        self.assertEqual(len(sent), 1)
        self.assertEqual(client.calls, 1)

    def test_failures_count_before_dispatch_and_never_retry(self):
        for failure in (TimeoutError(SECRET + PRIVATE), urllib.error.URLError(SECRET + PRIVATE),
                        urllib.error.HTTPError(ENDPOINT, 429, SECRET + PRIVATE, {}, io.BytesIO(PRIVATE.encode())),
                        OSError(SECRET + PRIVATE)):
            with self.subTest(error_type=type(failure).__name__):
                sent = []
                client = None

                def opener(request, timeout):
                    sent.append(request)
                    self.assertEqual(client.calls, 1)
                    raise failure

                client = JevClient(SECRET, opener=opener)
                code = "http_error" if isinstance(failure, urllib.error.HTTPError) else "transport_error"
                self.assert_safe_error(code, lambda: client.decide({"prefix": PRIVATE}, QUESTIONS))
                self.assert_safe_error("call_limit", lambda: client.decide({}, QUESTIONS))
                self.assertEqual(len(sent), 1)
                self.assertEqual(client.calls, 1)

    def test_non_200_and_redirect_bodies_are_not_read(self):
        for status in (201, 301, 302, 307, 308, 400, 401, 403, 429, 500, 503):
            with self.subTest(status=status):
                response = Response(body=(PRIVATE + SECRET).encode(), status=status)
                client, sent = self.client(response)
                self.assert_safe_error("http_error", lambda: client.decide({}, QUESTIONS))
                self.assertEqual(response.read_calls, [])
                self.assertTrue(response.closed)
                self.assertEqual(client.calls, 1)
                self.assertEqual(len(sent), 1)

    def test_response_with_unexpected_final_url_is_rejected(self):
        response = Response(url="https://attacker.example/")
        client, _ = self.client(response)
        self.assert_safe_error("invalid_response", lambda: client.decide({}, QUESTIONS))
        self.assertEqual(response.read_calls, [])

    def test_request_requires_explicit_stop_and_valid_choices(self):
        bad = [None, {}, [], {"q": {"type": "choice", "instructions": "choose", "criteria": {"a": "A"}}}]
        for field, value in (("type", "boolean"), ("instructions", ""), ("criteria", [])):
            questions = copy.deepcopy(QUESTIONS)
            questions["next_choice"][field] = value
            bad.append(questions)
        for criteria in ({"__STOP__": "stop"}, {"a": "A", "__STOP__": ""},
                         {"a": 1, "__STOP__": "stop"}, {1: "A", "__STOP__": "stop"}):
            questions = copy.deepcopy(QUESTIONS)
            questions["next_choice"]["criteria"] = criteria
            bad.append(questions)
        questions = copy.deepcopy(QUESTIONS)
        questions["next_choice"]["extra"] = SECRET
        bad.append(questions)
        for questions in bad:
            with self.subTest(questions_type=type(questions).__name__):
                client, sent = self.client()
                self.assert_safe_error("invalid_request", lambda: client.decide({}, questions))
                self.assertEqual(client.calls, 0)
                self.assertEqual(sent, [])

    def test_request_json_and_size_validation_precede_dispatch(self):
        cycle = []
        cycle.append(cycle)
        for state in ({1: "numeric key"}, {"v": object()}, {"v": float("nan")},
                      {"v": float("inf")}, {"v": ("tuple",)}, {"v": "\ud800"}, cycle):
            with self.subTest(state_type=type(state).__name__):
                client, sent = self.client()
                self.assert_safe_error("invalid_request", lambda: client.decide(state, QUESTIONS))
                self.assertEqual(client.calls, 0)
                self.assertEqual(sent, [])
        client, sent = self.client()
        self.assert_safe_error("request_too_large", lambda: client.decide("x" * MAX_REQUEST_BYTES, QUESTIONS))
        self.assertEqual(client.calls, 0)
        self.assertEqual(sent, [])

    def test_response_size_bound_even_with_incremental_reads(self):
        response = Response(body=b"x" * (MAX_RESPONSE_BYTES + 1), chunk_size=17_000)
        client, _ = self.client(response)
        self.assert_safe_error("response_too_large", lambda: client.decide({}, QUESTIONS))
        self.assertEqual(sum(min(size, 17_000) for size in response.read_calls), MAX_RESPONSE_BYTES + 1)
        self.assertTrue(response.closed)

    def test_chunked_reads_include_all_json_and_reject_trailing_content(self):
        client, _ = self.client(Response(chunk_size=7))
        self.assertEqual(client.decide({}, QUESTIONS)["answers"], valid_payload()["answers"])
        body = json.dumps(valid_payload()).encode() + (" " + PRIVATE).encode()
        client, _ = self.client(Response(body=body, chunk_size=7))
        self.assert_safe_error("invalid_json", lambda: client.decide({}, QUESTIONS))

    def test_malformed_truncated_duplicate_and_nonfinite_json_are_safe(self):
        valid = json.dumps(valid_payload())
        bodies = [b"", valid[:-5].encode(), (PRIVATE + SECRET).encode(), b"\xff", b"{} {}",
                  valid.replace('"model":', '"model":"jev-1.13.0","model":', 1).encode(),
                  valid.replace('"card_a": 0.6', '"card_a": 0.6,"card_a": 0.6').encode(),
                  valid.replace('"confidence": 0.8', '"confidence": NaN').encode(),
                  valid.replace('"confidence": 0.8', '"confidence": Infinity').encode(),
                  valid.replace('"confidence": 0.8', '"confidence": -Infinity').encode(),
                  valid[:-1].encode() + b',"ignored":1e999}',
                  valid[:-1].encode() + b',"ignored":NaN}']
        for body in bodies:
            with self.subTest(body_length=len(body)):
                response = Response(body=body)
                client, sent = self.client(response)
                self.assert_safe_error("invalid_json", lambda: client.decide({}, QUESTIONS))
                self.assertEqual(len(sent), 1)
                self.assertTrue(response.closed)

    def test_truncated_http_read_is_safe_and_consumes_one_call(self):
        class Truncated(Response):
            def read(self, size=-1):
                raise http.client.IncompleteRead((PRIVATE + SECRET).encode(), 10)

        response = Truncated()
        client, sent = self.client(response)
        self.assert_safe_error("transport_error", lambda: client.decide({}, QUESTIONS))
        self.assertEqual(len(sent), 1)
        self.assertTrue(response.closed)

    def test_declared_response_length_is_bounded_and_truncation_rejected(self):
        body = json.dumps(valid_payload()).encode()
        response = Response(body=body, headers={"Content-Length": str(len(body))})
        client, _ = self.client(response)
        self.assertEqual(client.decide({}, QUESTIONS)["model"], DEFAULT_MODEL)
        for declared_size in (str(len(body) + 1), str(len(body) - 1), "-1", "1.0", SECRET):
            with self.subTest(declared_size=declared_size):
                response = Response(body=body, headers={"Content-Length": declared_size})
                client, _ = self.client(response)
                self.assert_safe_error("invalid_response", lambda: client.decide({}, QUESTIONS))
                self.assertTrue(response.closed)
        response = Response(headers={"Content-Length": str(MAX_RESPONSE_BYTES + 1)})
        client, _ = self.client(response)
        self.assert_safe_error("response_too_large", lambda: client.decide({}, QUESTIONS))
        self.assertEqual(response.read_calls, [])

    def test_malformed_distributions_and_answer_shapes_are_rejected(self):
        payloads = [[], {}, {"model": DEFAULT_MODEL, "answers": []}]
        for field, value in (("model", "jev-latest"), ("model", "jev-1.14.0"),
                             ("answers", {}), ("answers", {"other": {}})):
            payload = valid_payload()
            payload[field] = value
            payloads.append(payload)
        for field, value in (("type", "boolean"), ("choice", "other"), ("choice", None),
                             ("confidence", -0.1), ("confidence", 1.1), ("confidence", True),
                             ("confidence", "0.8"), ("probabilities", [])):
            payload = valid_payload()
            payload["answers"]["next_choice"][field] = value
            payloads.append(payload)
        for probabilities in ({"card_a": .6, "card_b": .4},
                              {"card_a": .6, "card_b": .3, "__STOP__": .1, "extra": 0},
                              {"card_a": .2, "card_b": .7, "__STOP__": .1},
                              {"card_a": .6, "card_b": -.1, "__STOP__": .5},
                              {"card_a": 1.0000001, "card_b": 0, "__STOP__": 0},
                              {"card_a": .6, "card_b": .3, "__STOP__": .2},
                              {"card_a": .6, "card_b": True, "__STOP__": .1},
                              {"card_a": .6, "card_b": "0.3", "__STOP__": .1}):
            payload = valid_payload()
            payload["answers"]["next_choice"]["probabilities"] = probabilities
            payloads.append(payload)
        for payload in payloads:
            with self.subTest(payload_type=type(payload).__name__):
                client, _ = self.client(Response(payload))
                self.assert_safe_error("invalid_response", lambda: client.decide({}, QUESTIONS))

    def test_probability_ties_stop_choice_and_sum_tolerance_are_valid(self):
        payload = valid_payload()
        answer = payload["answers"]["next_choice"]
        answer["choice"] = "__STOP__"
        answer["probabilities"] = {"card_a": .5, "card_b": 0, "__STOP__": .5000005}
        answer["confidence"] = 0
        client, _ = self.client(Response(payload))
        self.assertEqual(client.decide({}, QUESTIONS)["answers"]["next_choice"]["choice"], "__STOP__")
        answer["choice"] = "card_a"
        answer["probabilities"] = {"card_a": .5, "card_b": 0, "__STOP__": .5}
        client, _ = self.client(Response(payload))
        self.assertEqual(client.decide({}, QUESTIONS)["answers"]["next_choice"]["choice"], "card_a")

    def test_multiple_questions_must_have_exact_response_ids(self):
        questions = copy.deepcopy(QUESTIONS)
        questions["later_choice"] = copy.deepcopy(QUESTIONS["next_choice"])
        payload = valid_payload()
        payload["answers"]["later_choice"] = copy.deepcopy(payload["answers"]["next_choice"])
        client, sent = self.client(Response(payload))
        self.assertEqual(set(client.decide({}, questions)["answers"]), set(questions))
        self.assertEqual(len(sent), 1)
        del payload["answers"]["later_choice"]
        client, _ = self.client(Response(payload))
        self.assert_safe_error("invalid_response", lambda: client.decide({}, questions))

    def test_only_safe_usage_counters_and_answer_fields_survive(self):
        payload = valid_payload()
        payload["usage"] = {"input_tokens": 50, "output_tokens": 12.0, "total_tokens": 62,
                            "metadata": SECRET + PRIVATE, "details": {"input_tokens": SECRET}}
        payload["answers"]["next_choice"]["explanation"] = SECRET + PRIVATE
        payload["upstream_body"] = SECRET + PRIVATE
        client, _ = self.client(Response(payload))
        result = client.decide({}, QUESTIONS)
        self.assertEqual(result["usage"], {"input_tokens": 50, "output_tokens": 12.0})
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertNotIn(PRIVATE, json.dumps(result))
        for usage in (None, [], {"input_tokens": True, "output_tokens": "secret"},
                      {"input_tokens": -1, "output_tokens": {}}, {"input_tokens": 10 ** 500}):
            with self.subTest(usage_type=type(usage).__name__):
                payload = valid_payload()
                payload["usage"] = usage
                client, _ = self.client(Response(payload))
                self.assertEqual(client.decide({}, QUESTIONS)["usage"], {})


if __name__ == "__main__":
    unittest.main()

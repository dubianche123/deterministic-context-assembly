"""Offline tests of the native Decisions schema and bounded HTTP adapter."""

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

from norn.openai_decisions import OpenAIDecisionsClient, OpenAIDecisionsError


ENDPOINT = "https://api.openai.com/v1/decisions"
MODEL = "gpt-6-luna"
SECRET = "test-openai-private-credential"
PRIVATE = "Synthetic private prefix never included in errors or reports"
MAX_REQUEST_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
QUESTIONS = {
    "next_choice": {
        "type": "choice", "instructions": "Predict the next choice from the supplied prefix.",
        "criteria": {"card_a": "A red illustrated tree", "card_b": "A blue photograph",
                     "__STOP__": "Stop selecting cards"},
    },
}


def native_answer(name="next_choice"):
    return {
        "type": "choice", "name": name, "choice": "card_a",
        "probabilities": [{"value": "card_a", "probability": .6},
                          {"value": "card_b", "probability": .3},
                          {"value": "__STOP__", "probability": .1}],
        "confidence": .8,
    }


def valid_payload():
    return {"model": MODEL, "answers": [native_answer()],
            "usage": {"input_tokens": 50, "output_tokens": 12}}


class Response(io.BytesIO):
    def __init__(self, payload=None, *, body=None, status=200, url=ENDPOINT,
                 headers=None, chunk_size=None):
        if body is None:
            body = json.dumps(valid_payload() if payload is None else payload,
                              ensure_ascii=False).encode("utf-8")
        super().__init__(body)
        self.status, self.url, self.chunk_size = status, url, chunk_size
        self.headers = {} if headers is None else headers
        self.read_calls = []

    def geturl(self):
        return self.url

    def read(self, size=-1):
        self.read_calls.append(size)
        return super().read(min(size, self.chunk_size) if self.chunk_size is not None else size)


class OpenAIDecisionsTests(unittest.TestCase):
    def assert_safe_error(self, code, operation):
        with self.assertRaises(OpenAIDecisionsError) as raised:
            operation()
        error = raised.exception
        self.assertEqual(error.code, code)
        rendered = str(error) + repr(error)
        rendered += "".join(traceback.format_exception(type(error), error, error.__traceback__))
        self.assertNotIn(SECRET, rendered)
        self.assertNotIn(PRIVATE, rendered)
        return error

    def client(self, response=None, **options):
        sent = []

        def opener(request, timeout):
            sent.append((request, timeout))
            return Response() if response is None else response

        return OpenAIDecisionsClient(SECRET, opener=opener, **options), sent

    def test_native_request_and_response_round_trip_with_canonical_utf8_state(self):
        state = {"z": PRIVATE, "a": {"b": "霞の木", "a": [1, False, None]}}
        client, sent = self.client()
        result = client.decide(state, QUESTIONS)
        self.assertEqual(client.model, MODEL)
        self.assertEqual(client.provider, "openai")
        self.assertEqual(client.label, "OpenAI Decisions")
        self.assertEqual(client.calls, 1)
        request, timeout = sent[0]
        self.assertEqual(request.full_url, ENDPOINT)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + SECRET)
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(timeout, 15)
        self.assertNotIn(SECRET, request.data.decode("utf-8"))
        self.assertNotIn(SECRET, request.full_url)
        self.assertNotIn(SECRET, repr(client))
        body = json.loads(request.data)
        self.assertEqual(set(body), {"model", "input", "questions"})
        self.assertEqual(body["model"], MODEL)
        self.assertEqual(body["input"], json.dumps(state, sort_keys=True, ensure_ascii=False,
                                                  separators=(",", ":"), allow_nan=False))
        self.assertIsInstance(body["input"], str)
        self.assertEqual(body["questions"], [{
            "type": "choice", "name": "next_choice",
            "instructions": QUESTIONS["next_choice"]["instructions"],
            "choices": [{"value": name, "description": description}
                        for name, description in QUESTIONS["next_choice"]["criteria"].items()],
        }])
        self.assertEqual(result["answers"], {"next_choice": {
            "type": "choice", "choice": "card_a",
            "probabilities": {"card_a": .6, "card_b": .3, "__STOP__": .1}, "confidence": .8,
        }})
        self.assertEqual(result["usage"], {"input_tokens": 50, "output_tokens": 12})
        self.assertEqual(result["model"], MODEL)
        self.assertEqual(result["requested_model"], MODEL)
        self.assertEqual(result["observed_model"], MODEL)
        self.assertEqual(result["provider_calls"], 1)
        self.assertGreaterEqual(result["elapsed_ms"], 0)
        self.assertTrue(math.isfinite(result["elapsed_ms"]))
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertNotIn(PRIVATE, json.dumps(result))

    def test_missing_and_calendar_snapshot_model_metadata(self):
        payload = valid_payload()
        del payload["model"]
        client, _ = self.client(Response(payload))
        result = client.decide({}, QUESTIONS)
        self.assertEqual(result["model"], MODEL)
        self.assertEqual(result["requested_model"], MODEL)
        self.assertIsNone(result["observed_model"])
        for observed in (MODEL, MODEL + "-2026-10-21", MODEL + "-2028-02-29"):
            with self.subTest(observed=observed):
                payload["model"] = observed
                client, _ = self.client(Response(payload))
                result = client.decide({}, QUESTIONS)
                self.assertEqual(result["model"], observed)
                self.assertEqual(result["observed_model"], observed)
                self.assertEqual(result["requested_model"], MODEL)
                self.assertEqual(client.model, MODEL)
        for observed in (None, "", "gpt-6-luna-latest", "gpt-6-luna-2026-02-29",
                         "gpt-6-luna-2026-13-01", "gpt-6-luna-2026-10-21-extra", "gpt-6-sol"):
            with self.subTest(observed=observed):
                payload["model"] = observed
                client, _ = self.client(Response(payload))
                self.assert_safe_error("invalid_response", lambda: client.decide({}, QUESTIONS))

    def test_only_exact_requested_model_and_bounded_configuration(self):
        def unexpected(request, timeout):
            self.fail("invalid configuration must not dispatch")

        for options in ({"model": "gpt-6-luna-latest"}, {"model": "gpt-6-luna-2026-10-21"},
                        {"model": "gpt-6-sol"}, {"timeout": 30.001}, {"timeout": 0},
                        {"timeout": True}, {"timeout": float("nan")}, {"timeout": float("inf")},
                        {"max_calls": -1}, {"max_calls": True}, {"max_calls": 1.5}):
            with self.subTest(options=options):
                self.assert_safe_error("invalid_config", lambda: OpenAIDecisionsClient(
                    SECRET, opener=unexpected, **options))
        for secret in ("", None, "bad\r\nkey", "white space", "key\t", "密钥"):
            with self.subTest(secret_type=type(secret).__name__):
                self.assert_safe_error("invalid_config", lambda: OpenAIDecisionsClient(secret, opener=unexpected))
        self.assert_safe_error("invalid_config", lambda: OpenAIDecisionsClient(SECRET, opener=object()))
        client, sent = self.client(timeout=30)
        client.decide({}, QUESTIONS)
        self.assertEqual(sent[0][1], 30)

    def test_default_transport_disables_redirects(self):
        fake = type("FakeOpener", (), {"open": lambda self, request, timeout: Response()})()
        with patch("urllib.request.build_opener", return_value=fake) as build:
            OpenAIDecisionsClient(SECRET).decide({}, QUESTIONS)
        handler = build.call_args.args[0]
        self.assertIsInstance(handler, urllib.request.HTTPRedirectHandler)
        self.assertIsNone(handler.redirect_request(None, None, 302, SECRET, {}, "https://attacker.example/"))

    def test_default_and_zero_call_budgets(self):
        client, sent = self.client()
        client.decide({}, QUESTIONS)
        self.assert_safe_error("call_limit", lambda: client.decide({}, QUESTIONS))
        self.assertEqual(client.calls, 1)
        self.assertEqual(len(sent), 1)
        client, sent = self.client(max_calls=0)
        self.assert_safe_error("call_limit", lambda: client.decide({}, QUESTIONS))
        self.assertEqual(client.calls, 0)
        self.assertEqual(sent, [])

    def test_shared_budget_is_atomic_under_concurrency(self):
        client, sent = self.client()
        barrier = threading.Barrier(8)

        def choose(_):
            barrier.wait(3)
            try:
                return client.decide({}, QUESTIONS)["provider_calls"]
            except OpenAIDecisionsError as error:
                return error.code

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(choose, range(8)))
        self.assertEqual(results.count(1), 1)
        self.assertEqual(results.count("call_limit"), 7)
        self.assertEqual(client.calls, 1)
        self.assertEqual(len(sent), 1)

    def test_failed_dispatch_consumes_budget_without_retries_or_secret_leaks(self):
        failures = [TimeoutError(SECRET + PRIVATE), urllib.error.URLError(SECRET + PRIVATE),
                    urllib.error.HTTPError(ENDPOINT, 429, SECRET + PRIVATE, {}, io.BytesIO(PRIVATE.encode())),
                    OSError(SECRET + PRIVATE)]
        for failure in failures:
            with self.subTest(error_type=type(failure).__name__):
                sent = []
                client = None

                def opener(request, timeout):
                    sent.append(request)
                    self.assertEqual(client.calls, 1)
                    raise failure

                client = OpenAIDecisionsClient(SECRET, opener=opener)
                code = "http_error" if isinstance(failure, urllib.error.HTTPError) else "transport_error"
                self.assert_safe_error(code, lambda: client.decide({"prefix": PRIVATE}, QUESTIONS))
                self.assert_safe_error("call_limit", lambda: client.decide({}, QUESTIONS))
                self.assertEqual(client.calls, 1)
                self.assertEqual(len(sent), 1)

    def test_non_200_and_redirect_responses_are_closed_without_reading(self):
        for status in (201, 301, 302, 307, 308, 400, 401, 403, 429, 500, 503):
            with self.subTest(status=status):
                response = Response(body=(SECRET + PRIVATE).encode(), status=status)
                client, sent = self.client(response)
                self.assert_safe_error("http_error", lambda: client.decide({}, QUESTIONS))
                self.assertEqual(response.read_calls, [])
                self.assertTrue(response.closed)
                self.assertEqual(client.calls, 1)
                self.assertEqual(len(sent), 1)
        response = Response(url="https://attacker.example/")
        client, _ = self.client(response)
        self.assert_safe_error("invalid_response", lambda: client.decide({}, QUESTIONS))
        self.assertEqual(response.read_calls, [])

    def test_questions_require_stop_and_native_count_bounds(self):
        invalid = [None, {}, [], {"q": {"type": "choice", "instructions": "choose",
                                        "criteria": {"a": "A", "b": "B"}}}]
        for field, value in (("type", "boolean"), ("instructions", ""), ("criteria", [])):
            questions = copy.deepcopy(QUESTIONS)
            questions["next_choice"][field] = value
            invalid.append(questions)
        for criteria in ({"__STOP__": "stop"}, {"a": "A", "__STOP__": ""},
                         {"a": 1, "__STOP__": "stop"}, {1: "A", "__STOP__": "stop"},
                         {**{f"c{i}": "A card" for i in range(255)}, "__STOP__": "Stop"}):
            questions = copy.deepcopy(QUESTIONS)
            questions["next_choice"]["criteria"] = criteria
            invalid.append(questions)
        invalid.append({f"q{i}": copy.deepcopy(QUESTIONS["next_choice"]) for i in range(401)})
        for questions in invalid:
            with self.subTest(questions_type=type(questions).__name__):
                client, sent = self.client()
                self.assert_safe_error("invalid_request", lambda: client.decide({}, questions))
                self.assertEqual(client.calls, 0)
                self.assertEqual(sent, [])

    def test_maximum_question_and_choice_counts_are_accepted(self):
        questions = {f"q{i}": copy.deepcopy(QUESTIONS["next_choice"]) for i in range(400)}
        payload = valid_payload()
        payload["answers"] = [native_answer(name) for name in reversed(questions)]
        client, sent = self.client(Response(payload))
        self.assertEqual(set(client.decide({}, questions)["answers"]), set(questions))
        self.assertEqual(len(sent), 1)
        criteria = {**{f"c{i}": "A card" for i in range(254)}, "__STOP__": "Stop"}
        questions = {"q": {"type": "choice", "instructions": "choose", "criteria": criteria}}
        payload["answers"] = [{"type": "choice", "name": "q", "choice": "c0", "confidence": 1,
                                "probabilities": [{"value": key, "probability": int(key == "c0")}
                                                  for key in criteria]}]
        client, _ = self.client(Response(payload))
        self.assertEqual(set(client.decide({}, questions)["answers"]["q"]["probabilities"]), set(criteria))

    def test_request_json_and_size_checks_happen_before_dispatch(self):
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

    def test_missing_extra_duplicate_and_wrong_answer_ids_are_rejected(self):
        payloads = [[], {}, {"model": MODEL, "answers": {}}, {"model": MODEL, "answers": []}]
        for answers in ([native_answer("other")], [native_answer(), native_answer()],
                        [native_answer(), native_answer("extra")], [None]):
            payload = valid_payload()
            payload["answers"] = answers
            payloads.append(payload)
        payload = valid_payload()
        del payload["answers"][0]["name"]
        payloads.append(payload)
        for payload in payloads:
            with self.subTest(payload_type=type(payload).__name__):
                client, _ = self.client(Response(payload))
                self.assert_safe_error("invalid_response", lambda: client.decide({}, QUESTIONS))

    def test_any_refusal_rejects_all_partial_answers_without_disclosing_reason(self):
        questions = copy.deepcopy(QUESTIONS)
        questions["later_choice"] = copy.deepcopy(QUESTIONS["next_choice"])
        payloads = [
            {"model": MODEL, "answers": [native_answer(), {"type": "refusal", "name": "later_choice",
                                                           "refusal": SECRET + PRIVATE}]},
            {"model": MODEL, "answers": [native_answer(), {"type": "choice", "name": "later_choice",
                                                           "refusal": SECRET + PRIVATE}]},
            {"model": MODEL, "answers": [native_answer()], "refusal": SECRET + PRIVATE},
        ]
        for payload in payloads:
            with self.subTest(refusal_location="top" if "refusal" in payload else "answer"):
                client, _ = self.client(Response(payload))
                self.assert_safe_error("refusal", lambda: client.decide({}, questions))

    def test_distribution_ids_numbers_selected_choice_and_confidence_are_validated(self):
        payloads = []
        for field, value in (("type", "boolean"), ("choice", "other"), ("choice", None),
                             ("confidence", -0.1), ("confidence", 1.1), ("confidence", True),
                             ("confidence", "0.8"), ("probabilities", {})):
            payload = valid_payload()
            payload["answers"][0][field] = value
            payloads.append(payload)
        for probabilities in (
            [{"value": "card_a", "probability": .6}, {"value": "card_b", "probability": .4}],
            native_answer()["probabilities"] + [{"value": "extra", "probability": 0}],
            native_answer()["probabilities"] + [{"value": "card_a", "probability": 0}],
            [{"value": "card_a", "probability": .2}, {"value": "card_b", "probability": .7},
             {"value": "__STOP__", "probability": .1}],
            [{"value": "card_a", "probability": .6}, {"value": "card_b", "probability": -.1},
             {"value": "__STOP__", "probability": .5}],
            [{"value": "card_a", "probability": 1.0000001}, {"value": "card_b", "probability": 0},
             {"value": "__STOP__", "probability": 0}],
            [{"value": "card_a", "probability": .6}, {"value": "card_b", "probability": .3},
             {"value": "__STOP__", "probability": .2}],
            [{"value": "card_a", "probability": .6}, {"value": "card_b", "probability": True},
             {"value": "__STOP__", "probability": .1}],
            [{"value": "card_a", "probability": .6}, {"value": "card_b", "probability": "0.3"},
             {"value": "__STOP__", "probability": .1}],
            [{"value": "card_a", "probability": .6}, {"value": "card_b", "probability": .3},
             {"probability": .1}], [None],
        ):
            payload = valid_payload()
            payload["answers"][0]["probabilities"] = probabilities
            payloads.append(payload)
        for payload in payloads:
            with self.subTest(payload_type=type(payload).__name__):
                client, _ = self.client(Response(payload))
                self.assert_safe_error("invalid_response", lambda: client.decide({}, QUESTIONS))

    def test_tied_argmax_stop_choice_and_sum_tolerance(self):
        payload = valid_payload()
        answer = payload["answers"][0]
        answer["choice"] = "__STOP__"
        answer["probabilities"] = [{"value": "card_a", "probability": .5},
                                   {"value": "card_b", "probability": 0},
                                   {"value": "__STOP__", "probability": .5000005}]
        answer["confidence"] = 0
        client, _ = self.client(Response(payload))
        self.assertEqual(client.decide({}, QUESTIONS)["answers"]["next_choice"]["choice"], "__STOP__")
        answer["choice"] = "card_a"
        answer["probabilities"][-1]["probability"] = .5
        client, _ = self.client(Response(payload))
        self.assertEqual(client.decide({}, QUESTIONS)["answers"]["next_choice"]["choice"], "card_a")

    def test_strict_json_rejects_malformed_truncated_duplicates_and_nonfinite_anywhere(self):
        valid = json.dumps(valid_payload())
        bodies = [b"", valid[:-5].encode(), (SECRET + PRIVATE).encode(), b"\xff", b"{} {}",
                  valid.replace('"model":', '"model":"gpt-6-luna","model":', 1).encode(),
                  valid.replace('"name":', '"name":"next_choice","name":', 1).encode(),
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

    def test_response_bytes_are_bounded_and_incremental_json_is_complete(self):
        response = Response(body=b"x" * (MAX_RESPONSE_BYTES + 1), chunk_size=17000)
        client, _ = self.client(response)
        self.assert_safe_error("response_too_large", lambda: client.decide({}, QUESTIONS))
        self.assertTrue(response.closed)
        response = Response(chunk_size=7)
        client, _ = self.client(response)
        self.assertEqual(client.decide({}, QUESTIONS)["answers"]["next_choice"]["choice"], "card_a")
        body = json.dumps(valid_payload()).encode() + (" " + PRIVATE).encode()
        client, _ = self.client(Response(body=body, chunk_size=7))
        self.assert_safe_error("invalid_json", lambda: client.decide({}, QUESTIONS))

    def test_content_length_truncation_and_incomplete_http_reads_are_safe(self):
        response = Response(headers={"Content-Length": "99999"})
        client, _ = self.client(response)
        self.assert_safe_error("invalid_response", lambda: client.decide({}, QUESTIONS))
        self.assertTrue(response.closed)

        class Truncated(Response):
            def read(self, size=-1):
                raise http.client.IncompleteRead((SECRET + PRIVATE).encode(), 10)

        response = Truncated()
        client, sent = self.client(response)
        self.assert_safe_error("transport_error", lambda: client.decide({}, QUESTIONS))
        self.assertEqual(len(sent), 1)
        self.assertTrue(response.closed)

    def test_safe_usage_and_answer_fields_exclude_arbitrary_provider_text(self):
        payload = valid_payload()
        payload["usage"] = {"input_tokens": 50, "output_tokens": 12.0, "total_tokens": 62,
                            "metadata": SECRET + PRIVATE, "details": {"input_tokens": SECRET}}
        payload["answers"][0]["explanation"] = SECRET + PRIVATE
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

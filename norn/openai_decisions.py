"""Bounded native OpenAI Decisions choice requests for optional experiments.

Only the caller's supplied state and candidate descriptions are submitted. No
credential discovery, journal reads, retries, redirects, or substitute models
are performed. Returned confidence is provider metadata, not a calibrated
probability about an individual person's future choices.

Official request and response schema, checked on 2026-10-21:
https://developers.openai.com/api/docs/guides/decisions
https://developers.openai.com/api/reference/python/resources/decisions/methods/create
"""

from datetime import date
import json
import math
import re
import threading
import time
import urllib.request

from .jev import (MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, JevError, _ERROR_CODES,
                  _NoRedirect, _bounded_post, _json, _number, _questions, _usage)


ENDPOINT = "https://api.openai.com/v1/decisions"
DEFAULT_MODEL = "gpt-6-luna"
_MODEL_VERSION = re.compile(r"gpt-6-luna-([0-9]{4}-[0-9]{2}-[0-9]{2})\Z")


class OpenAIDecisionsError(RuntimeError):
    """A safe code; upstream bodies, request text, and credentials stay private."""

    def __init__(self, code):
        self.code = code if isinstance(code, str) and code in _ERROR_CODES | {"refusal"} else "transport_error"
        super().__init__("OpenAI Decisions request failed: " + self.code)


def _json_bytes(value, *, canonical=False):
    def check(item):
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError("non-string JSON key")
            for nested in item.values():
                check(nested)
        elif isinstance(item, list):
            for nested in item:
                check(nested)
        elif item is not None and not isinstance(item, (str, bool, int, float)):
            raise ValueError("unsupported JSON value")

    try:
        check(value)
        raw = json.dumps(value, sort_keys=canonical, ensure_ascii=False, allow_nan=False,
                         separators=(",", ":")).encode("utf-8")
    except (UnicodeError, ValueError, TypeError, OverflowError, RecursionError):
        raise OpenAIDecisionsError("invalid_request") from None
    if len(raw) > MAX_REQUEST_BYTES:
        raise OpenAIDecisionsError("request_too_large")
    return raw


def _native_questions(questions):
    try:
        questions = _questions(questions)
    except JevError as error:
        raise OpenAIDecisionsError(error.code) from None
    if len(questions) > 400 or any(len(question["criteria"]) > 255 for question in questions.values()):
        raise OpenAIDecisionsError("invalid_request")
    native = [{"type": "choice", "name": name, "instructions": question["instructions"],
               "choices": [{"value": value, "description": description}
                           for value, description in question["criteria"].items()]}
              for name, question in questions.items()]
    return questions, native


def _observed_model(payload):
    if "model" not in payload:
        return None
    model = payload["model"]
    if model == DEFAULT_MODEL:
        return model
    if not isinstance(model, str):
        raise OpenAIDecisionsError("invalid_response")
    match = _MODEL_VERSION.fullmatch(model)
    if match is None:
        raise OpenAIDecisionsError("invalid_response")
    try:
        date.fromisoformat(match.group(1))
    except ValueError:
        raise OpenAIDecisionsError("invalid_response") from None
    return model


def _validated_response(raw, questions):
    try:
        payload = _json(raw)
    except JevError as error:
        raise OpenAIDecisionsError(error.code) from None
    if not isinstance(payload, dict):
        raise OpenAIDecisionsError("invalid_response")
    if payload.get("refusal"):
        raise OpenAIDecisionsError("refusal")
    observed_model = _observed_model(payload)
    native_answers = payload.get("answers")
    if not isinstance(native_answers, list):
        raise OpenAIDecisionsError("invalid_response")
    answers = {}
    for answer in native_answers:
        if not isinstance(answer, dict):
            raise OpenAIDecisionsError("invalid_response")
        if answer.get("type") == "refusal" or answer.get("refusal"):
            raise OpenAIDecisionsError("refusal")
        name = answer.get("name")
        if not isinstance(name, str) or name not in questions or name in answers or answer.get("type") != "choice":
            raise OpenAIDecisionsError("invalid_response")
        native_probabilities = answer.get("probabilities")
        if not isinstance(native_probabilities, list):
            raise OpenAIDecisionsError("invalid_response")
        probabilities = {}
        for entry in native_probabilities:
            if not isinstance(entry, dict):
                raise OpenAIDecisionsError("invalid_response")
            value, probability = entry.get("value"), entry.get("probability")
            if not isinstance(value, str) or value in probabilities or not _number(probability) or not 0 <= probability <= 1:
                raise OpenAIDecisionsError("invalid_response")
            probabilities[value] = float(probability)
        choice, confidence = answer.get("choice"), answer.get("confidence")
        if set(probabilities) != set(questions[name]["criteria"]):
            raise OpenAIDecisionsError("invalid_response")
        if abs(math.fsum(probabilities.values()) - 1.0) > 1e-6:
            raise OpenAIDecisionsError("invalid_response")
        if not isinstance(choice, str) or choice not in probabilities or probabilities[choice] != max(probabilities.values()):
            raise OpenAIDecisionsError("invalid_response")
        if not _number(confidence) or not 0 <= confidence <= 1:
            raise OpenAIDecisionsError("invalid_response")
        answers[name] = {"type": "choice", "choice": choice, "probabilities": probabilities,
                         "confidence": float(confidence)}
    if set(answers) != set(questions):
        raise OpenAIDecisionsError("invalid_response")
    return {"model": observed_model or DEFAULT_MODEL, "requested_model": DEFAULT_MODEL,
            "observed_model": observed_model, "answers": answers, "usage": _usage(payload.get("usage"))}


class OpenAIDecisionsClient:
    """One explicit native Decisions POST per call, with a shared call budget."""

    provider = "openai"
    label = "OpenAI Decisions"

    def __init__(self, api_key, *, model=DEFAULT_MODEL, timeout=15, max_calls=1, opener=None):
        if not isinstance(api_key, str) or not api_key or len(api_key) > 4096:
            raise OpenAIDecisionsError("invalid_config")
        if any(not 33 <= ord(char) <= 126 for char in api_key):
            raise OpenAIDecisionsError("invalid_config")
        if model != DEFAULT_MODEL or not isinstance(model, str):
            raise OpenAIDecisionsError("invalid_config")
        if not _number(timeout) or not 0 < timeout <= 30:
            raise OpenAIDecisionsError("invalid_config")
        if isinstance(max_calls, bool) or not isinstance(max_calls, int) or max_calls < 0:
            raise OpenAIDecisionsError("invalid_config")
        if opener is not None and not callable(opener):
            raise OpenAIDecisionsError("invalid_config")
        self._key = api_key
        self._timeout, self._max_calls = timeout, max_calls
        self._opener = opener if opener is not None else urllib.request.build_opener(_NoRedirect()).open
        self._calls = 0
        self._lock = threading.Lock()

    @property
    def model(self):
        return DEFAULT_MODEL

    @property
    def calls(self):
        with self._lock:
            return self._calls

    def decide(self, state, questions):
        questions, native = _native_questions(questions)
        state_text = _json_bytes(state, canonical=True).decode("utf-8")
        raw = _json_bytes({"model": DEFAULT_MODEL, "input": state_text, "questions": native})
        with self._lock:
            if self._calls >= self._max_calls:
                raise OpenAIDecisionsError("call_limit")
            self._calls += 1
        started = time.monotonic()
        body = _bounded_post(raw, self._key, ENDPOINT, self._timeout, self._opener, OpenAIDecisionsError)
        result = _validated_response(body, questions)
        result["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
        result["provider_calls"] = 1
        return result

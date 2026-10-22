"""Bounded Typesafe Jev choice requests for explicitly selected experiments.

This stdlib-only adapter does not discover credentials, read journals, retry,
or follow redirects. Callers supply the exact state and candidate descriptions
they intend to disclose. Provider confidence is returned as metadata; it is not
treated as a calibrated probability about a particular person's preferences.

Official protocol and pinned models:
https://docs.typesafe.ai/api/system-one
https://docs.typesafe.ai/models
https://docs.typesafe.ai/primitives/choice
https://docs.typesafe.ai/confidence
"""

import json
import math
import threading
import time
import urllib.error
import urllib.request


ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-1.13.0"
ALLOWED_MODELS = frozenset((DEFAULT_MODEL, "jev-1.14.0"))
MAX_REQUEST_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
STOP = "__STOP__"

_ERROR_CODES = frozenset((
    "invalid_config", "invalid_request", "request_too_large", "call_limit",
    "transport_error", "http_error", "response_too_large", "invalid_json",
    "invalid_response",
))


class JevError(RuntimeError):
    """A safe error code, without request text, credentials, or upstream bodies."""

    def __init__(self, code):
        self.code = code if code in _ERROR_CODES else "transport_error"
        super().__init__("Jev request failed: " + self.code)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _usage(raw):
    """Keep only documented numeric counters, never arbitrary provider metadata."""
    if not isinstance(raw, dict):
        return {}
    return {name: raw[name] for name in ("input_tokens", "output_tokens")
            if _number(raw.get(name)) and raw[name] >= 0}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("nonfinite JSON number")


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite JSON number")
    return number


def _json(raw):
    try:
        # Explicit UTF-8 avoids accepting an unexpected UTF-16/32 response.
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=_reject_constant, parse_float=_finite_float)
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise JevError("invalid_json") from None


def _questions(raw):
    if not isinstance(raw, dict) or not raw:
        raise JevError("invalid_request")
    result = {}
    for question_id, question in raw.items():
        if not isinstance(question_id, str) or not question_id or not isinstance(question, dict):
            raise JevError("invalid_request")
        if set(question) != {"type", "instructions", "criteria"} or question["type"] != "choice":
            raise JevError("invalid_request")
        if not isinstance(question["instructions"], str) or not question["instructions"].strip():
            raise JevError("invalid_request")
        criteria = question["criteria"]
        if not isinstance(criteria, dict) or len(criteria) < 2 or STOP not in criteria:
            raise JevError("invalid_request")
        if any(not isinstance(key, str) or not key or not isinstance(description, str)
               or not description.strip() for key, description in criteria.items()):
            raise JevError("invalid_request")
        result[question_id] = {"type": "choice", "instructions": question["instructions"],
                               "criteria": dict(criteria)}
    return result


def _request_body(model, state, questions):
    # Validate before serialization to prevent JSON's implicit conversion of
    # non-string object keys. Serialization snapshots all mutable state.
    def check(value):
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                raise ValueError("non-string JSON key")
            for item in value.values():
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)
        elif value is not None and not isinstance(value, (str, bool, int, float)):
            raise ValueError("unsupported JSON value")

    try:
        check(state)
        raw = json.dumps({"model": model, "state": state, "questions": questions},
                         ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (UnicodeError, ValueError, TypeError, OverflowError, RecursionError):
        raise JevError("invalid_request") from None
    if len(raw) > MAX_REQUEST_BYTES:
        raise JevError("request_too_large")
    return raw


def _bounded_post(raw, api_key, endpoint, timeout, opener, error_class=JevError):
    """One HTTP POST for the two fixed-endpoint experimental adapters.

    The caller validates and reserves its call budget before entering this
    helper. Error conversion, redirect policy, and read limits stay shared.
    """
    response = None
    try:
        request = urllib.request.Request(endpoint, data=raw, method="POST", headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        response = opener(request, timeout=timeout)
        status = getattr(response, "status", None)
        if status is None:
            status = response.getcode()
        if type(status) is not int or status != 200:
            raise error_class("http_error")
        geturl = getattr(response, "geturl", None)
        if callable(geturl) and geturl() != endpoint:
            raise error_class("invalid_response")
        declared_size = getattr(response, "headers", {}).get("Content-Length")
        if declared_size is not None:
            if (not isinstance(declared_size, str) or not declared_size.isascii()
                    or not declared_size.isdecimal() or len(declared_size) > 10):
                raise error_class("invalid_response")
            declared_size = int(declared_size)
            if declared_size > MAX_RESPONSE_BYTES:
                raise error_class("response_too_large")
        chunks, size = [], 0
        while True:
            chunk = response.read(min(65536, MAX_RESPONSE_BYTES + 1 - size))
            if not isinstance(chunk, bytes):
                raise error_class("invalid_response")
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise error_class("response_too_large")
            if not chunk:
                break
            chunks.append(chunk)
        if declared_size is not None and size != declared_size:
            raise error_class("invalid_response")
        return b"".join(chunks)
    except error_class:
        raise
    except urllib.error.HTTPError as exc:
        try:
            exc.close()
        except Exception:
            pass
        raise error_class("http_error") from None
    except Exception:
        raise error_class("transport_error") from None
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass


def _validated_response(raw, model, questions):
    payload = _json(raw)
    if not isinstance(payload, dict) or payload.get("model") != model:
        raise JevError("invalid_response")
    answers = payload.get("answers")
    if not isinstance(answers, dict) or set(answers) != set(questions):
        raise JevError("invalid_response")
    result = {}
    for question_id, question in questions.items():
        answer = answers[question_id]
        if not isinstance(answer, dict) or answer.get("type") != "choice":
            raise JevError("invalid_response")
        choice, probabilities, confidence = (answer.get("choice"), answer.get("probabilities"),
                                             answer.get("confidence"))
        if not isinstance(choice, str) or choice not in question["criteria"]:
            raise JevError("invalid_response")
        if not isinstance(probabilities, dict) or set(probabilities) != set(question["criteria"]):
            raise JevError("invalid_response")
        if any(not _number(value) or not 0 <= value <= 1 for value in probabilities.values()):
            raise JevError("invalid_response")
        if abs(math.fsum(probabilities.values()) - 1.0) > 1e-6:
            raise JevError("invalid_response")
        if probabilities[choice] != max(probabilities.values()):
            raise JevError("invalid_response")
        if not _number(confidence) or not 0 <= confidence <= 1:
            raise JevError("invalid_response")
        result[question_id] = {"type": "choice", "choice": choice,
                               "probabilities": {key: float(value) for key, value in probabilities.items()},
                               "confidence": float(confidence)}
    return {"model": model, "answers": result, "usage": _usage(payload.get("usage"))}


class JevClient:
    """One bounded HTTP dispatch per decide, with a shared explicit call budget.

    Failed dispatches consume the budget. Local validation failures do not.
    Both stable and preview models require their exact pinned identifiers.
    ``opener`` is an injectable ``(request, timeout=...)`` transport for tests.
    """

    provider = "typesafe"
    label = "Jev"

    def __init__(self, api_key, *, model=DEFAULT_MODEL, timeout=15, max_calls=1, opener=None):
        if not isinstance(api_key, str) or not api_key or len(api_key) > 4096:
            raise JevError("invalid_config")
        if any(not 33 <= ord(char) <= 126 for char in api_key):
            raise JevError("invalid_config")
        if not isinstance(model, str) or model not in ALLOWED_MODELS:
            raise JevError("invalid_config")
        if not _number(timeout) or not 0 < timeout <= 30:
            raise JevError("invalid_config")
        if isinstance(max_calls, bool) or not isinstance(max_calls, int) or max_calls < 0:
            raise JevError("invalid_config")
        if opener is not None and not callable(opener):
            raise JevError("invalid_config")
        self._key, self._model = api_key, model
        self._timeout, self._max_calls = timeout, max_calls
        self._opener = opener if opener is not None else urllib.request.build_opener(_NoRedirect()).open
        self._calls = 0
        self._lock = threading.Lock()

    @property
    def calls(self):
        with self._lock:
            return self._calls

    @property
    def model(self):
        return self._model

    def decide(self, state, questions):
        questions = _questions(questions)
        raw = _request_body(self._model, state, questions)
        with self._lock:
            if self._calls >= self._max_calls:
                raise JevError("call_limit")
            self._calls += 1
        started = time.monotonic()
        body = _bounded_post(raw, self._key, ENDPOINT, self._timeout, self._opener)
        result = _validated_response(body, self._model, questions)
        result["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
        result["provider_calls"] = 1
        return result

"""Small process-local dispatch and retry boundaries; no durable user data."""

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
import math
import threading
import time

from .events import canonical_json


class DispatchLimits:
    """Reserve an attempt before dispatch. Failures still spend that attempt."""

    def __init__(self, max_model_calls=100, max_concurrency=2):
        for name, value in (("max_model_calls", max_model_calls), ("max_concurrency", max_concurrency)):
            if type(value) is not int or not 1 <= value <= 1_000_000:
                raise ValueError(f"{name} must be a positive integer no greater than 1000000")
        self.max_model_calls = max_model_calls
        self.max_concurrency = max_concurrency
        self.model_calls = 0
        self.active = 0
        self._lock = threading.Lock()

    def enter(self, real_model):
        from .service import RequestError
        with self._lock:
            if self.active >= self.max_concurrency:
                raise RequestError("当前生成请求已达并发上限，请稍后重试。", 429)
            if real_model and self.model_calls >= self.max_model_calls:
                raise RequestError("本进程的模型调用额度已用完，请联系服务维护者。", 429)
            self.active += 1
            if real_model:
                self.model_calls += 1

    def leave(self):
        with self._lock:
            self.active -= 1

    def snapshot(self):
        with self._lock:
            return {"scope": "process", "max_model_calls": self.max_model_calls,
                    "model_calls_reserved": self.model_calls,
                    "max_concurrency": self.max_concurrency, "active": self.active,
                    "resets_on_restart": True}


@dataclass
class _Operation:
    fingerprint: str
    expires_at: float
    lock: object = field(default_factory=threading.Lock)
    result: object = None
    error: tuple | None = None
    done: bool = False
    users: int = 0


class OperationStore:
    """Bounded retry receipts. Capacity fails closed instead of evicting live IDs."""

    def __init__(self, ttl=1800, capacity=1024, clock=time.monotonic):
        self.ttl, self.capacity, self.clock = ttl, capacity, clock
        self._entries = OrderedDict()
        self._lock = threading.Lock()

    def execute(self, scope, key, fingerprint, function):
        from .service import RequestError
        if key is None:
            return function()
        if (type(key) is not str or not 1 <= len(key) <= 128
                or any(ord(char) < 33 or ord(char) > 126 for char in key)):
            raise RequestError("操作 ID 必须是 1 至 128 位无空格 ASCII 字符")
        identity = (scope, key)
        with self._lock:
            now = self.clock()
            for old in [old for old, entry in self._entries.items()
                        if not entry.users and entry.expires_at <= now]:
                del self._entries[old]
            entry = self._entries.get(identity)
            if entry is None:
                if len(self._entries) >= self.capacity:
                    raise RequestError("重试记录已达容量上限，请稍后再试。", 429)
                entry = _Operation(fingerprint, now + self.ttl)
                self._entries[identity] = entry
            elif entry.fingerprint != fingerprint:
                raise RequestError("同一操作 ID 不能用于不同请求", 409)
            entry.users += 1
        try:
            with entry.lock:
                if entry.done:
                    if entry.error:
                        raise RequestError(*entry.error)
                    return deepcopy(entry.result)
                try:
                    result = function()
                except RequestError as exc:
                    # Limits reject before dispatch; the same ID can retry.
                    if exc.status != 429:
                        entry.error, entry.done = (str(exc), exc.status), True
                    raise
                except Exception:
                    # An ambiguous dispatch must not be repeated by a lost-
                    # response retry. Never retain an exception with secrets.
                    entry.error = ("本地服务暂时无法处理请求", 500)
                    entry.done = True
                    raise
                entry.result = deepcopy(result)
                entry.done = True
                entry.expires_at = self.clock() + self.ttl
                return result
        finally:
            with self._lock:
                entry.users -= 1

    def rewrite(self, function):
        """Apply `function(result)` to every settled result, e.g. to retract a
        record receipt whose write was lost after the reply was stored."""
        with self._lock:
            for entry in self._entries.values():
                if entry.done and isinstance(entry.result, dict):
                    function(entry.result)


def request_metrics(source, prompt, calls=None):
    """Logical JSON bytes and an explicitly labeled estimate, never billing."""
    raw_bytes = len(canonical_json(source).encode("utf-8"))
    prompt_bytes = len(canonical_json(prompt).encode("utf-8"))
    calls = calls or []
    return {"raw_bytes": raw_bytes, "prompt_bytes": prompt_bytes,
            "estimated_input_tokens": math.ceil(prompt_bytes / 3),
            "raw_estimated_input_tokens": math.ceil(raw_bytes / 3),
            "token_estimate_method": "utf8_bytes_div_3_estimate",
            "estimate_scope": "logical JSON; not tokenizer or wire accounting",
            "usage": deepcopy(calls[-1].get("usage")) if calls else None,
            "render_requests": len(calls),
            "provider_calls": sum(call.get("provider_calls", 0) for call in calls)}

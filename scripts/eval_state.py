"""Durable, single-writer checkpoints for paid offline evaluations.

Only explicitly selected configuration and result fields should be passed here.
This module never reads credentials, writes a final report, or retries a request
whose outcome may be unknown after interruption.
"""

from copy import deepcopy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import threading


SCHEMA = "norn.eval.samples.v1"


class RunStateError(RuntimeError):
    pass


class RunLockedError(RunStateError):
    pass


class ConfigurationMismatchError(RunStateError):
    pass


class CorruptStateError(RunStateError):
    pass


class UncertainRequestError(RunStateError):
    """An earlier request may have spent tokens without a durable result."""


class RequestNotDispatchedError(RunStateError):
    """The caller confirms no HTTP attempt was sent, so retrying is safe."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class RunState:
    """Append-only JSONL state, held under a process lock for the whole run.

    ``execute`` checkpoints a pending request before calling ``fn``. Completed
    keys are reused; an interrupted pending key fails closed on resume. Threads
    executing the same key share the first result without duplicate dispatch.
    ``fn`` may raise ``RequestNotDispatchedError`` only when it can prove that
    no HTTP attempt was sent; that explicit outcome permits a later execution.
    The final report remains entirely the caller's responsibility.
    """

    def __init__(self, output: Path, configuration: dict, resume=False):
        if not isinstance(configuration, dict):
            raise TypeError("configuration must be a JSON object")
        self.output = Path(output).expanduser().resolve()
        self.samples = self.output.with_name(self.output.name + ".samples.jsonl")
        self.lock_path = self.samples.with_name(self.samples.name + ".lock")
        self.configuration_hash = hashlib.sha256(_json(configuration).encode("utf-8")).hexdigest()
        self.resume = bool(resume)
        self._condition = threading.Condition(threading.RLock())
        self._finished = {}
        self._pending = set()
        self._inflight = {}
        self._file = None
        self._lock_fd = None
        self._broken = False

    def __enter__(self):
        with self._condition:
            if self._lock_fd is not None:
                raise RunStateError("evaluation state is already open")
            self.output.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(self.lock_path, flags, 0o600)
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise RunLockedError("another evaluation owns this output") from exc
                self._lock_fd = fd
                if self.resume:
                    if not self.samples.is_file():
                        raise RunStateError("resume requires an existing samples log")
                    self._file = open(self.samples, "r+b")
                    self._load()
                else:
                    if self.output.exists() or self.samples.exists():
                        raise RunStateError("output or samples already exists; choose resume or a new output")
                    log_fd = os.open(self.samples, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
                    self._file = os.fdopen(log_fd, "w+b")
                    self._append({"kind": "run_manifest", "schema": SCHEMA,
                                  "configuration_hash": self.configuration_hash})
                return self
            except BaseException:
                if self._file is not None:
                    self._file.close()
                    self._file = None
                if self._lock_fd is not None:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                    self._lock_fd = None
                os.close(fd)
                raise

    def __exit__(self, exc_type, exc_value, traceback):
        with self._condition:
            # A worker may still be persisting its result when another worker
            # raises. Keep the process lock until all local executions settle.
            while self._inflight:
                self._condition.wait()
            try:
                if self._file is not None:
                    self._file.close()
            finally:
                self._file = None
                if self._lock_fd is not None:
                    fd, self._lock_fd = self._lock_fd, None
                    try:
                        fcntl.flock(fd, fcntl.LOCK_UN)
                    finally:
                        os.close(fd)
        return False

    def _ensure_open(self):
        if self._file is None or self._lock_fd is None:
            raise RunStateError("evaluation state must be used in a with block")
        if self._broken:
            raise RunStateError("samples write failed; reopen with resume before continuing")

    @staticmethod
    def _identity(kind, key):
        if not isinstance(kind, str) or not kind or kind == "run_manifest":
            raise ValueError("kind must be a nonempty result category")
        if not isinstance(key, str) or not key:
            raise ValueError("cache key must be a nonempty string")
        return kind, key

    def _append(self, value):
        data = (_json(value) + "\n").encode("utf-8")
        try:
            self._file.seek(0, os.SEEK_END)
            self._file.write(data)
            self._file.flush()
            os.fsync(self._file.fileno())
        except BaseException:
            self._broken = True
            raise

    def _load(self):
        raw = self._file.read()
        lines = raw.splitlines(keepends=True)
        if not lines:
            raise CorruptStateError("samples log has no run manifest")
        entries = []
        offset = 0
        repair_at = None
        for index, line in enumerate(lines):
            try:
                entry = json.loads(line)
            except (ValueError, UnicodeDecodeError) as exc:
                if index > 0 and index == len(lines) - 1 and not line.endswith(b"\n"):
                    repair_at = offset
                    break
                raise CorruptStateError(f"invalid samples JSON on line {index + 1}") from exc
            if not isinstance(entry, dict):
                raise CorruptStateError(f"samples line {index + 1} must be an object")
            entries.append(entry)
            offset += len(line)
        manifest = entries[0]
        if manifest.get("kind") != "run_manifest" or manifest.get("schema") != SCHEMA:
            raise CorruptStateError("samples manifest has an unsupported format")
        if manifest.get("configuration_hash") != self.configuration_hash:
            raise ConfigurationMismatchError("resume configuration does not match the samples manifest")
        finished, pending = {}, set()
        for index, entry in enumerate(entries[1:], 2):
            try:
                identity = self._identity(entry.get("kind"), entry.get("cache_key"))
            except ValueError as exc:
                raise CorruptStateError(f"invalid sample identity on line {index}") from exc
            state = entry.get("state")
            if state == "pending":
                if identity in pending or identity in finished:
                    raise CorruptStateError(f"duplicate pending request on line {index}")
                pending.add(identity)
            elif state == "finished":
                row = entry.get("row")
                if not isinstance(row, dict) or row.get("kind") != identity[0] or row.get("cache_key") != identity[1]:
                    raise CorruptStateError(f"invalid completed sample on line {index}")
                if identity in finished:
                    raise CorruptStateError(f"duplicate completed sample on line {index}")
                finished[identity] = row
                pending.discard(identity)
            elif state == "not_dispatched":
                if identity not in pending or identity in finished:
                    raise CorruptStateError(f"invalid undispatched request on line {index}")
                pending.remove(identity)
            else:
                raise CorruptStateError(f"unknown sample state on line {index}")
        # Validate the manifest and all complete records before changing a file.
        if repair_at is not None:
            self._file.seek(repair_at)
            self._file.truncate()
            self._file.flush()
            os.fsync(self._file.fileno())
        elif raw and not raw.endswith(b"\n"):
            self._file.seek(0, os.SEEK_END)
            self._file.write(b"\n")
            self._file.flush()
            os.fsync(self._file.fileno())
        self._finished, self._pending = finished, pending

    def get(self, kind, key):
        identity = self._identity(kind, key)
        with self._condition:
            self._ensure_open()
            return deepcopy(self._finished.get(identity))

    def records(self, kind):
        if not isinstance(kind, str) or not kind:
            raise ValueError("kind must be a nonempty result category")
        with self._condition:
            self._ensure_open()
            return [deepcopy(row) for identity, row in self._finished.items() if identity[0] == kind]

    def _put(self, identity, row):
        if not isinstance(row, dict):
            raise TypeError("completed sample must be a JSON object")
        result = deepcopy(row)
        for name, value in zip(("kind", "cache_key"), identity):
            if name in result and result[name] != value:
                raise ValueError("completed sample identity conflicts with its cache key")
            result[name] = value
        # Verify serializability before touching the log or the in-memory cache.
        result = json.loads(_json(result))
        previous = self._finished.get(identity)
        if previous is not None:
            if previous != result:
                raise RunStateError("a completed sample cannot be overwritten")
            return deepcopy(previous)
        self._append({"kind": identity[0], "cache_key": identity[1], "state": "finished", "row": result})
        self._finished[identity] = result
        self._pending.discard(identity)
        return deepcopy(result)

    def put(self, kind, key, row):
        identity = self._identity(kind, key)
        with self._condition:
            self._ensure_open()
            if identity in self._inflight and self._inflight[identity] != threading.get_ident():
                raise RunStateError("another thread is executing this sample")
            return self._put(identity, row)

    def execute(self, kind, key, fn):
        identity = self._identity(kind, key)
        with self._condition:
            self._ensure_open()
            while identity in self._inflight:
                if self._inflight[identity] == threading.get_ident():
                    raise RunStateError("recursive execution of the same sample is unsupported")
                self._condition.wait()
                self._ensure_open()
            previous = self._finished.get(identity)
            if previous is not None:
                return deepcopy(previous)
            if identity in self._pending:
                raise UncertainRequestError("sample has an unresolved request; it will not be dispatched again")
            self._append({"kind": kind, "cache_key": key, "state": "pending"})
            self._pending.add(identity)
            self._inflight[identity] = threading.get_ident()
        try:
            row = fn()
            with self._condition:
                self._ensure_open()
                return self._put(identity, row)
        except RequestNotDispatchedError:
            with self._condition:
                self._ensure_open()
                if identity not in self._pending or identity in self._finished:
                    raise RunStateError("only an unresolved pending request can be marked undispatched")
                self._append({"kind": kind, "cache_key": key, "state": "not_dispatched"})
                self._pending.remove(identity)
            raise
        finally:
            with self._condition:
                self._inflight.pop(identity, None)
                self._condition.notify_all()

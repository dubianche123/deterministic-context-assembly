"""Synchronous SQLite receipts and admission limits for paid cloud calls.

These stores contain no provider credentials. A pending receipt is deliberately
not retried: a lost response cannot establish whether the provider billed it.
Callers persist their session snapshot in ``settle``'s transaction callback.
"""

import json
import math
import time

from .ffi_compat import optional_js


RECEIPT_TTL = 30 * 60
LEASE_TTL = 120
DAILY_LIMIT = 100
ACTIVE_LIMIT = 4
RECEIPT_CAPACITY = 40
_DAY = 24 * 60 * 60
_MAX_RESULT_BYTES = 16 * 1024 * 1024


class ReceiptConflict(ValueError):
    """An operation ID or settled result was reused with different input."""


class ReceiptCapacity(ValueError):
    """The actor has no room for another durable paid-call receipt."""


def _field(row, name):
    """Cloudflare returns JsProxy objects; local SQLite tests return dicts."""
    if isinstance(row, dict):
        return optional_js(row[name])
    try:
        return optional_js(row[name])
    except (TypeError, KeyError, AttributeError):
        return optional_js(getattr(row, name))


def _rows(sql, query, *bindings):
    # Consume the cursor before any callback or asynchronous boundary.
    return list(sql.exec(query, *bindings).toArray())


def _identity(value, label, maximum=256):
    if (type(value) is not str or not 1 <= len(value) <= maximum
            or any(ord(char) < 33 or ord(char) > 126 for char in value)):
        raise ValueError(f"{label} must be a bounded printable identifier")
    return value


def _now(clock):
    now = clock()
    if type(now) not in (float, int) or not math.isfinite(now) or now < 0:
        raise ValueError("Clock must return a finite nonnegative epoch timestamp")
    return now


def _limit(value, label):
    if type(value) is not int or not 0 <= value <= 100_000:
        raise ValueError(f"{label} must be a bounded nonnegative integer")
    return value


def _encode_result(result):
    if type(result) is not dict:
        raise ValueError("A paid receipt result must be a JSON object")
    pending = [(result, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > 64 or nodes > 1_000_000:
            raise ValueError("Paid receipt result exceeds JSON structural bounds")
        kind = type(item)
        if item is None or kind in (str, bool, int):
            continue
        if kind is float:
            if not math.isfinite(item):
                raise ValueError("Paid receipt result numbers must be finite")
        elif kind is list:
            pending.extend((child, depth + 1) for child in item)
        elif kind is dict:
            if any(type(key) is not str for key in item):
                raise ValueError("Paid receipt result object keys must be strings")
            pending.extend((child, depth + 1) for child in item.values())
        else:
            raise ValueError("Paid receipt result accepts only plain JSON values")
    try:
        encoded = json.dumps(result, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False)
        if len(encoded.encode("utf-8")) > _MAX_RESULT_BYTES:
            raise ValueError("Paid receipt result exceeds 16 MiB")
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("Paid receipt result is not bounded UTF-8 JSON") from exc
    return encoded


class PaidReceipts:
    """Per-browser, durable at-most-once receipts with a fixed 30-minute TTL.

    ``transaction_sync`` is DurableObjectStorage.transactionSync or an equivalent
    synchronous transaction runner. ``reserve`` returns ``created=True`` only to
    the first caller; existing pending or settled receipts never permit another
    provider call. Expiry is measured from reservation and is never refreshed.
    """

    def __init__(self, sql, transaction_sync, *, clock=time.time, capacity=RECEIPT_CAPACITY):
        self.sql = sql
        self.transaction_sync = transaction_sync
        self.clock = clock
        self.capacity = _limit(capacity, "Paid receipt capacity")
        self.sql.exec("""CREATE TABLE IF NOT EXISTS norn_paid_receipts (
            scope TEXT NOT NULL,
            operation_key TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('pending', 'settled')),
            expires_at REAL NOT NULL,
            result_json TEXT,
            PRIMARY KEY (scope, operation_key),
            CHECK ((state = 'pending' AND result_json IS NULL)
                OR (state = 'settled' AND result_json IS NOT NULL))
        )""")

    def _lookup(self, scope, key, fingerprint, now):
        self.sql.exec("DELETE FROM norn_paid_receipts WHERE expires_at <= ?", now)
        rows = _rows(self.sql, """SELECT fingerprint, state, expires_at, result_json
            FROM norn_paid_receipts WHERE scope = ? AND operation_key = ?""", scope, key)
        if not rows:
            return None
        row = rows[0]
        if _field(row, "fingerprint") != fingerprint:
            raise ReceiptConflict("Paid operation ID was already used with different input")
        result_json = _field(row, "result_json")
        return {"state": _field(row, "state"),
                "result": json.loads(result_json) if result_json is not None else None,
                "expires_at": _field(row, "expires_at")}

    def _arguments(self, scope, key, fingerprint):
        _identity(scope, "Paid operation scope")
        _identity(key, "Paid operation ID", 128)
        _identity(fingerprint, "Paid operation fingerprint", 128)

    def get(self, scope, key, fingerprint):
        self._arguments(scope, key, fingerprint)
        now = _now(self.clock)
        return self.transaction_sync(lambda: self._lookup(scope, key, fingerprint, now))

    def reserve(self, scope, key, fingerprint):
        self._arguments(scope, key, fingerprint)
        now = _now(self.clock)

        def reserve_once():
            existing = self._lookup(scope, key, fingerprint, now)
            if existing is not None:
                return {**existing, "created": False}
            count = _rows(self.sql, "SELECT COUNT(*) AS count FROM norn_paid_receipts")[0]
            if _field(count, "count") >= self.capacity:
                raise ReceiptCapacity("Paid receipt capacity is full; wait for an existing receipt to expire")
            expires_at = now + RECEIPT_TTL
            self.sql.exec("""INSERT INTO norn_paid_receipts
                (scope, operation_key, fingerprint, state, expires_at, result_json)
                VALUES (?, ?, ?, 'pending', ?, NULL)""", scope, key, fingerprint, expires_at)
            return {"state": "pending", "result": None,
                    "expires_at": expires_at, "created": True}

        return self.transaction_sync(reserve_once)

    def discard_pending(self, scope, key, fingerprint):
        """Release an un-dispatched reservation after explicit budget denial.

        Callers must only use this before any provider request was dispatched.
        Settled receipts are immutable, and mismatched fingerprints still fail.
        """
        self._arguments(scope, key, fingerprint)
        now = _now(self.clock)

        def discard_once():
            existing = self._lookup(scope, key, fingerprint, now)
            if existing is None or existing["state"] != "pending":
                return False
            self.sql.exec("""DELETE FROM norn_paid_receipts
                WHERE scope = ? AND operation_key = ? AND fingerprint = ? AND state = 'pending'""",
                scope, key, fingerprint)
            return True

        return self.transaction_sync(discard_once)

    def next_expiry(self):
        """Prune expired receipts and return the next absolute alarm deadline."""
        now = _now(self.clock)

        def expiry_once():
            self.sql.exec("DELETE FROM norn_paid_receipts WHERE expires_at <= ?", now)
            rows = _rows(self.sql, "SELECT MIN(expires_at) AS expires_at FROM norn_paid_receipts")
            return _field(rows[0], "expires_at")

        return self.transaction_sync(expiry_once)

    def settle(self, scope, key, fingerprint, result, save_snapshot_callback):
        """Persist result and session snapshot together, or retain pending on failure.

        Settling an identical result again is a no-op. A different result cannot
        overwrite a settled receipt. The callback must only do synchronous writes
        on this same SQLite storage and must not start its own transaction.
        """
        self._arguments(scope, key, fingerprint)
        if not callable(save_snapshot_callback):
            raise ValueError("Snapshot callback must be synchronous and callable")
        encoded = _encode_result(result)
        now = _now(self.clock)

        def settle_once():
            existing = self._lookup(scope, key, fingerprint, now)
            if existing is None:
                raise ValueError("Paid receipt is missing or expired")
            if existing["state"] == "settled":
                if _encode_result(existing["result"]) != encoded:
                    raise ReceiptConflict("A settled paid receipt cannot be overwritten")
                return existing
            callback_result = save_snapshot_callback()
            if hasattr(callback_result, "__await__"):
                if hasattr(callback_result, "close"):
                    callback_result.close()
                raise ValueError("Snapshot callback must not return an awaitable")
            self.sql.exec("""UPDATE norn_paid_receipts SET state = 'settled', result_json = ?
                WHERE scope = ? AND operation_key = ? AND fingerprint = ?""",
                encoded, scope, key, fingerprint)
            return {"state": "settled", "result": json.loads(encoded),
                    "expires_at": existing["expires_at"]}

        return self.transaction_sync(settle_once)


class QuotaStore:
    """One short global coordination atom for paid-call admission.

    Accepted attempts consume the UTC day's budget permanently. A 120-second
    lease bounds concurrency after crashes; releasing a lease does not refund
    that attempt. Repeated accepted IDs are denied without counting or extending
    a lease. Today's and yesterday's IDs remain to recognize cross-midnight
    retries; callers' paid receipts expire after only 30 minutes.
    """

    def __init__(self, sql, transaction_sync, *, clock=time.time):
        self.sql = sql
        self.transaction_sync = transaction_sync
        self.clock = clock
        self.sql.exec("""CREATE TABLE IF NOT EXISTS norn_budget_days (
            day INTEGER PRIMARY KEY,
            used INTEGER NOT NULL CHECK (used >= 0)
        )""")
        self.sql.exec("""CREATE TABLE IF NOT EXISTS norn_budget_attempts (
            attempt_id TEXT PRIMARY KEY,
            day INTEGER NOT NULL,
            lease_expires_at REAL NOT NULL,
            released INTEGER NOT NULL DEFAULT 0 CHECK (released IN (0, 1))
        )""")

    def reserve(self, attempt_id, max_daily=DAILY_LIMIT, max_active=ACTIVE_LIMIT):
        _identity(attempt_id, "Budget attempt ID")
        _limit(max_daily, "Daily budget")
        _limit(max_active, "Concurrent budget")
        now = _now(self.clock)
        day = int(now // _DAY)
        reset_at = (day + 1) * _DAY

        def reserve_once():
            self.sql.exec("""DELETE FROM norn_budget_attempts
                WHERE day < ? AND lease_expires_at <= ?""", day - 1, now)
            self.sql.exec("DELETE FROM norn_budget_days WHERE day < ?", day - 1)
            existing = _rows(self.sql, """SELECT lease_expires_at FROM norn_budget_attempts
                WHERE attempt_id = ?""", attempt_id)
            if existing:
                return {"allowed": False, "reason": "duplicate", "reset_at": reset_at,
                        "lease_expires_at": _field(existing[0], "lease_expires_at")}
            counts = _rows(self.sql, "SELECT used FROM norn_budget_days WHERE day = ?", day)
            used = _field(counts[0], "used") if counts else 0
            if used >= max_daily:
                return {"allowed": False, "reason": "daily_limit", "reset_at": reset_at,
                        "lease_expires_at": None, "retry_at": reset_at}
            active = _rows(self.sql, """SELECT COUNT(*) AS active, MIN(lease_expires_at) AS earliest
                FROM norn_budget_attempts WHERE released = 0 AND lease_expires_at > ?""", now)[0]
            if _field(active, "active") >= max_active:
                return {"allowed": False, "reason": "concurrency_limit", "reset_at": reset_at,
                        "lease_expires_at": None,
                        "retry_at": _field(active, "earliest") or now + LEASE_TTL}
            lease = now + LEASE_TTL
            self.sql.exec("""INSERT INTO norn_budget_days (day, used) VALUES (?, 1)
                ON CONFLICT(day) DO UPDATE SET used = used + 1""", day)
            self.sql.exec("""INSERT INTO norn_budget_attempts
                (attempt_id, day, lease_expires_at, released) VALUES (?, ?, ?, 0)""",
                attempt_id, day, lease)
            return {"allowed": True, "reason": "reserved", "reset_at": reset_at,
                    "lease_expires_at": lease}

        return self.transaction_sync(reserve_once)

    def release(self, attempt_id):
        _identity(attempt_id, "Budget attempt ID")

        def release_once():
            existing = _rows(self.sql, "SELECT released FROM norn_budget_attempts WHERE attempt_id = ?",
                             attempt_id)
            if not existing or _field(existing[0], "released"):
                return {"released": False}
            self.sql.exec("UPDATE norn_budget_attempts SET released = 1 WHERE attempt_id = ?", attempt_id)
            return {"released": True}

        return self.transaction_sync(release_once)

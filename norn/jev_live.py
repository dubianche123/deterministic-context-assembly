"""Explicit, bounded Jev sidecar predictions for an opted-in visit.

The browser opts in before the first deal. Requests contain a completed history
and a just-dealt round only, never that round's answer. This module owns a
separate SQLite capability/receipt store, not a reading or research archive.
"""

import asyncio
import http.client
import json
import math
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

from .cloud_quota import _field, _rows
from .core import engine_module
from .events import content_hash
from .jev import (DEFAULT_MODEL, ENDPOINT, JevError, MAX_RESPONSE_BYTES, STOP,
                  _questions, _request_body, _validated_response)
from .pamela_comparison import options_hash
from .pamela_compare_cli import build_pamela_request, _probability_prediction
from .research_inspection import validated
from .service import RequestError

TTL = 1800
TIMEOUT = 15
MAX_CALLS = 8
MAX_PREFIX_EVENTS = 1024
MAX_PREFIX_ROUNDS = 64
LIMITS = {"max_calls": MAX_CALLS, "ttl_seconds": TTL, "timeout_seconds": TIMEOUT}
_CLIENT = re.compile(r"[0-9a-f]{32}\Z")
_TICKET = re.compile(r"[A-Za-z0-9_-]{43}\Z")


def configured(key):
    return type(key) is str and 1 <= len(key) <= 4096 and all(33 <= ord(c) <= 126 for c in key)


def status(key):
    return {"available": configured(key), "provider": "typesafe", "model": DEFAULT_MODEL,
            "limits": dict(LIMITS), "provider_calls": 0}


def validate_start(body, deck):
    if (type(body) is not dict or set(body) - {"enabled", "mode", "deck", "client_session", "purpose"}
            or not {"enabled", "mode", "deck", "client_session"} <= set(body)
            or body.get("purpose", "rolling") not in ("rolling", "finale")
            or body["enabled"] is not True or body["mode"] != "play"
            or type(body["client_session"]) is not str or not _CLIENT.fullmatch(body["client_session"])
            or deck is not None and body["deck"] != deck.label):
        raise RequestError("Jev must be selected before starting an opted-in visit")


def validate_predict_body(body):
    if (type(body) is not dict or set(body) != {"jev_session", "journal"}
            or type(body["jev_session"]) is not str or not _TICKET.fullmatch(body["jev_session"])
            or type(body["journal"]) is not dict):
        raise RequestError("Jev prediction accepts only its play capability and event prefix")


def prepare(journal, deck):
    """Validate the immutable pre-choice request and build one numerical question."""
    if type(journal) is not dict or type(journal.get("events")) is not list:
        raise RequestError("Jev needs a just-dealt event prefix")
    events = journal["events"]
    if not 1 <= len(events) <= MAX_PREFIX_EVENTS:
        raise RequestError("Jev history exceeds this play session's limit", 413)
    if events[-1].get("type") != "round_started":
        raise RequestError("Jev predictions must be committed before this round's interactions", 409)
    if deck.context is not None:
        raise RequestError("Jev play does not support a nonzero slot-context deck", 409)
    try:
        replay = validated(journal, deck, require_complete=False)
    except ValueError as exc:
        raise RequestError(str(exc)) from exc
    journal = replay["journal"]
    current = journal["events"][-1]
    number = current["round"]
    if not 1 <= number <= MAX_PREFIX_ROUNDS or not 2 <= len(current["card_ids"]) <= 6:
        raise RequestError("Jev play candidates exceed their bounds")
    prefix = {"schema_version": journal["schema_version"], "events": journal["events"][:-1]}
    research = engine_module("research_deck")
    rows = research.observations(prefix)
    if len(rows) != number - 1:
        raise RequestError("Jev needs all earlier completed rounds")
    candidates = list(current["card_ids"])
    state, questions, mappings = build_pamela_request(prefix, rows,
        [{"round": number, "candidate_ids": candidates}], deck)
    questions = _questions(questions)
    encoded = _request_body(DEFAULT_MODEL, state, questions)
    return {"journal": journal, "round": number, "cutoff_rounds": len(rows),
            "prefix_hash": content_hash(prefix), "options_hash": options_hash(candidates, deck),
            "candidate_ids": candidates, "questions": questions, "mappings": mappings,
            "encoded": encoded, "request_hash": content_hash({"state": state, "questions": questions}),
            "fingerprint": content_hash({"journal": journal, "model": DEFAULT_MODEL})}


class JevStore:
    """Durable pre-start capability, monotone history and at-most-once receipts."""

    def __init__(self, sql, transaction_sync, *, clock=time.time):
        self.sql, self.transaction_sync, self.clock = sql, transaction_sync, clock
        sql.exec("""CREATE TABLE IF NOT EXISTS norn_jev_visits (
            ticket TEXT PRIMARY KEY, client_session TEXT UNIQUE NOT NULL,
            deck TEXT NOT NULL, expires_at REAL NOT NULL, last_journal TEXT)""")
        sql.exec("""CREATE TABLE IF NOT EXISTS norn_jev_requests (
            ticket TEXT NOT NULL, round INTEGER NOT NULL, fingerprint TEXT NOT NULL,
            state TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL,
            result_json TEXT, PRIMARY KEY(ticket, round))""")
        columns = [_field(row, "name") for row in _rows(sql, "PRAGMA table_info(norn_jev_visits)")]
        if "purpose" not in columns:
            sql.exec("ALTER TABLE norn_jev_visits ADD COLUMN purpose TEXT NOT NULL DEFAULT 'rolling'")

    def _prune(self, now):
        self.sql.exec("DELETE FROM norn_jev_requests WHERE expires_at <= ?", now)
        self.sql.exec("DELETE FROM norn_jev_visits WHERE expires_at <= ?", now)

    def start(self, body, deck):
        validate_start(body, deck)
        now = self.clock()
        def run():
            self._prune(now)
            old = _rows(self.sql, "SELECT ticket, deck, expires_at, purpose FROM norn_jev_visits WHERE client_session = ?",
                        body["client_session"])
            if old:
                row = old[0]
                if _field(row, "deck") != deck.label or _field(row, "purpose") != body.get("purpose", "rolling"):
                    raise RequestError("This Jev visit belongs to another deck", 409)
                ticket, expiry = _field(row, "ticket"), _field(row, "expires_at")
            else:
                if _field(_rows(self.sql, "SELECT COUNT(*) AS n FROM norn_jev_visits")[0], "n") >= MAX_CALLS:
                    raise RequestError("Too many active Jev play visits", 429)
                ticket, expiry = secrets.token_urlsafe(32), now + TTL
                self.sql.exec("INSERT INTO norn_jev_visits (ticket, client_session, deck, expires_at, last_journal, purpose) VALUES (?, ?, ?, ?, NULL, ?)",
                              ticket, body["client_session"], deck.label, expiry, body.get("purpose", "rolling"))
            return {"status": "ready", "jev_session": ticket, "expires_at": expiry,
                    "model": DEFAULT_MODEL, "provider": "typesafe", "limits": dict(LIMITS),
                    "provider_calls": 0}
        return self.transaction_sync(run)

    def reserve(self, ticket, plan, deck):
        if type(ticket) is not str or not _TICKET.fullmatch(ticket):
            raise RequestError("Please enable Jev before the first deal", 403)
        now = self.clock()
        def run():
            self._prune(now)
            rows = _rows(self.sql, "SELECT deck, expires_at, last_journal, purpose FROM norn_jev_visits WHERE ticket = ?", ticket)
            if not rows:
                raise RequestError("This Jev play visit has expired", 410)
            visit = rows[0]
            if _field(visit, "deck") != deck.label:
                raise RequestError("This Jev visit belongs to another deck", 409)
            previous = _rows(self.sql, "SELECT fingerprint, state, result_json FROM norn_jev_requests WHERE ticket = ? AND round = ?",
                             ticket, plan["round"])
            if previous:
                row = previous[0]
                if _field(row, "fingerprint") != plan["fingerprint"]:
                    raise RequestError("A Jev prediction cannot change its committed candidates or history", 409)
                if _field(row, "state") != "settled":
                    raise RequestError("The earlier Jev result is unknown and will not be requested again", 409)
                result = json.loads(_field(row, "result_json"))
                return {"created": False, "result": {**result, "cached": True,
                    "original_provider_calls": result["provider_calls"], "provider_calls": 0}}
            previous_journal = _field(visit, "last_journal")
            if previous_journal is None:
                if plan["round"] != 1 and _field(visit, "purpose") != "finale":
                    raise RequestError("Jev can only join before the first round", 409)
            else:
                if _field(visit, "purpose") == "finale":
                    raise RequestError("This game has already used its single Jev finale", 409)
                old = json.loads(previous_journal)
                if (old["schema_version"] != plan["journal"]["schema_version"]
                        or old["events"] != plan["journal"]["events"][:len(old["events"])]):
                    raise RequestError("Jev history must extend its earlier committed prefix", 409)
            # Limit all active visits of this actor as well as each individual
            # visit, so opening new client_session IDs cannot reset the budget.
            if _field(_rows(self.sql, "SELECT COUNT(*) AS n FROM norn_jev_requests")[0], "n") >= MAX_CALLS:
                raise RequestError("This browser's Jev allowance is exhausted", 429)
            if _rows(self.sql, "SELECT 1 AS n FROM norn_jev_requests WHERE state = 'pending' AND created_at > ?",
                     now - TIMEOUT - 5):
                raise RequestError("One Jev prediction is already in flight", 429)
            self.sql.exec("INSERT INTO norn_jev_requests VALUES (?, ?, ?, 'pending', ?, ?, NULL)",
                          ticket, plan["round"], plan["fingerprint"], now, _field(visit, "expires_at"))
            self.sql.exec("UPDATE norn_jev_visits SET last_journal = ? WHERE ticket = ?",
                          json.dumps(plan["journal"], ensure_ascii=False, allow_nan=False), ticket)
            return {"created": True}
        return self.transaction_sync(run)

    def denied_before_dispatch(self, ticket, plan):
        self.transaction_sync(lambda: self.sql.exec(
            "DELETE FROM norn_jev_requests WHERE ticket = ? AND round = ? AND fingerprint = ? AND state = 'pending'",
            ticket, plan["round"], plan["fingerprint"]))

    def next_expiry(self):
        def run():
            self._prune(self.clock())
            return _field(_rows(self.sql, "SELECT MIN(expires_at) AS expiry FROM norn_jev_visits")[0], "expiry")
        return self.transaction_sync(run)

    def settle(self, ticket, plan, result):
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        def run():
            rows = _rows(self.sql, "SELECT fingerprint, state FROM norn_jev_requests WHERE ticket = ? AND round = ?",
                         ticket, plan["round"])
            if not rows or _field(rows[0], "fingerprint") != plan["fingerprint"] or _field(rows[0], "state") != "pending":
                raise RequestError("The Jev receipt cannot be settled", 409)
            self.sql.exec("UPDATE norn_jev_requests SET state = 'settled', result_json = ? WHERE ticket = ? AND round = ?",
                          encoded, ticket, plan["round"])
        self.transaction_sync(run)


async def _receive(encoded, key, transport):
    status_code, headers, body = await transport(ENDPOINT, encoded, key, TIMEOUT)
    try:
        if status_code != 200:
            raise JevError("http_error")
        declared = headers.get("content-length")
        if declared is not None and (type(declared) is not str or not declared.isascii()
                or not declared.isdecimal() or len(declared) > 10 or int(declared) > MAX_RESPONSE_BYTES):
            raise JevError("response_too_large")
        if isinstance(body, bytes):
            raw = body
        else:
            parts, total = [], 0
            async for chunk in body:
                if type(chunk) is not bytes:
                    raise JevError("invalid_response")
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    raise JevError("response_too_large")
                parts.append(chunk)
            raw = b"".join(parts)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise JevError("response_too_large")
        if declared is not None and len(raw) != int(declared):
            raise JevError("invalid_response")
        return raw
    finally:
        close = getattr(body, "aclose", None)
        if close is not None:
            await close()


async def predict(store, body, deck, key, *, actor_id, quota, transport=None):
    """One bounded request, with a durable pending receipt before external I/O."""
    validate_predict_body(body)
    if not configured(key):
        return {"status": "unavailable", "reason": "not_configured", "provider_calls": 0,
                "model": DEFAULT_MODEL}
    plan = prepare(body["journal"], deck)
    ticket = body["jev_session"]
    reservation = store.reserve(ticket, plan, deck)
    if not reservation["created"]:
        return reservation["result"]
    attempt = content_hash({"actor": actor_id, "ticket": ticket, "round": plan["round"],
                            "request": plan["request_hash"]})
    admission = await quota.reserve(attempt)
    if type(admission) is str:
        admission = json.loads(admission)
    if not admission.get("allowed"):
        if admission.get("reason") != "duplicate":
            store.denied_before_dispatch(ticket, plan)
        raise RequestError("Jev is at its separate shared limit; continue with the local models", 429)
    result = {"status": "failed", "reason": "unavailable_or_invalid_response",
              "provider": "typesafe", "model": DEFAULT_MODEL, "provider_calls": 1,
              "round": plan["round"], "cutoff_rounds": plan["cutoff_rounds"],
              "prefix_hash": plan["prefix_hash"], "options_hash": plan["options_hash"],
              "candidate_ids": plan["candidate_ids"], "request_hash": plan["request_hash"],
              "mode": "play_precommit", "cached": False}
    try:
        if transport is None:
            from .cloud_provider import _worker_transport
            transport = _worker_transport
        raw = await asyncio.wait_for(_receive(plan["encoded"], key, transport), TIMEOUT)
        response = _validated_response(raw, DEFAULT_MODEL, plan["questions"])
        question = f"round_{plan['round']:04d}"
        mapping = plan["mappings"][question]
        probabilities = {mapping[item]: value for item, value in response["answers"][question]["probabilities"].items()}
        total = math.fsum(probabilities[card] for card in plan["candidate_ids"])
        if total <= 0:
            raise JevError("invalid_response")
        lane = _probability_prediction([probabilities[card] / total for card in plan["candidate_ids"]],
                                        plan["candidate_ids"], None, False)
        result.update(lane)
        result.update(usage=response["usage"], wire_stop_conditioned_out=True)
        result.pop("reason", None)
    except Exception:
        # Never expose provider bodies, request text, credentials or exception text.
        pass
    finally:
        try:
            await quota.release(attempt)
        except Exception:
            pass
    store.settle(ticket, plan, result)
    return result


async def local_transport(url, encoded, key, timeout):
    """Fixed-origin stdlib transport with a shared wall-time body-read deadline."""
    def request():
        target = urlsplit(ENDPOINT)
        connection = http.client.HTTPSConnection(target.hostname, timeout=timeout)
        response = None
        deadline = time.monotonic() + timeout
        try:
            connection.connect()
            sock = connection.sock
            connection.request("POST", target.path, encoded, {"Authorization": "Bearer " + key,
                "Content-Type": "application/json", "Accept": "application/json"})
            sock.settimeout(max(.001, deadline - time.monotonic()))
            response = connection.getresponse()
            parts, total = [], 0
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError()
                sock.settimeout(max(.001, deadline - time.monotonic()))
                part = response.read(min(65536, MAX_RESPONSE_BYTES + 1 - total))
                total += len(part)
                if total > MAX_RESPONSE_BYTES:
                    raise JevError("response_too_large")
                if not part:
                    break
                parts.append(part)
            return response.status, {"content-length": response.getheader("Content-Length")}, b"".join(parts)
        finally:
            if response is not None:
                response.close()
            connection.close()
    return await asyncio.to_thread(request)


class LocalJev:
    """Process-local optional Jev state for the threaded local HTTP server."""
    def __init__(self, deck, key="", *, transport=None, clock=time.time):
        import sqlite3
        from types import SimpleNamespace
        from .cloud_quota import QuotaStore
        self.deck, self.key, self.transport = deck, key, transport or local_transport
        self.connection = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
        self.lock = threading.RLock()
        class SQL:
            def exec(inner, query, *values):
                with self.lock:
                    cursor = self.connection.execute(query, values)
                    names = [column[0] for column in cursor.description] if cursor.description else []
                    rows = [dict(zip(names, row)) for row in cursor.fetchall()] if names else []
                    return SimpleNamespace(toArray=lambda: rows)
        sql = SQL()
        def transaction(callback):
            with self.lock:
                self.connection.execute("BEGIN IMMEDIATE")
                try:
                    value = callback()
                except BaseException:
                    self.connection.rollback()
                    raise
                self.connection.commit()
                return value
        self.store = JevStore(sql, transaction, clock=clock)
        quota = QuotaStore(sql, transaction, clock=clock)
        class Quota:
            async def reserve(inner, attempt):
                return quota.reserve(attempt, max_daily=100, max_active=2)
            async def release(inner, attempt):
                return quota.release(attempt)
        self.quota = Quota()

    def status(self):
        return status(self.key if self.deck is not None and self.deck.context is None else "")

    def start(self, body):
        validate_start(body, self.deck)
        if not self.status()["available"]:
            return {"status": "unavailable", **self.status(), "reason": "not_configured"}
        return self.store.start(body, self.deck)

    def predict(self, body):
        validate_predict_body(body)
        if not self.status()["available"]:
            return {"status": "unavailable", **self.status(), "reason": "not_configured"}
        return asyncio.run(predict(self.store, body, self.deck, self.key,
            actor_id="local-play", quota=self.quota, transport=self.transport))

#!/usr/bin/env python3
"""Bounded consent-v4 acceptance on the deployed Norn or a loopback test server.

Uses a fresh synthetic visitor only. Open-round choices and attention are
checked without analysis. Feedback configuration and an explicitly simulated
visible hint are checked as a separate archive sidecar. A completely empty selection exercises
full-record replacement through Norn's local empty-reading rule; a transport
allowlist rejects nonempty analysis and all dialogue requests before dispatch.
The archive token is read only into memory. Cleanup runs even after failure;
an unconfirmed cleanup retains only a 0600 deletion capability in /tmp.
"""
import argparse
from copy import deepcopy
import http.client
import json
import os
from pathlib import Path
import random
import re
import secrets
import sys
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from norn.collection import CONSENT_VERSION, visitor_id  # noqa: E402
from norn.core import engine_module  # noqa: E402
from norn.events import EVENTS_SCHEMA_VERSION  # noqa: E402
from norn.experience import EXPERIENCE_VERSION, validate_experience  # noqa: E402

BASE = "https://norn-machine.dubianche123.workers.dev"
TOKEN_FILE = Path.home() / ".norn" / "archive-token"
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_PAGES = 500
COOKIE = re.compile(r"__Host-norn-actor=[A-Za-z0-9_-]{43}\Z")


class VerificationError(ValueError):
    pass


def trusted_origin(value):
    if type(value) is not str:
        raise VerificationError("Invalid acceptance origin")
    try:
        parsed = urlsplit(value)
        parsed.port
    except (TypeError, ValueError):
        raise VerificationError("Invalid acceptance origin") from None
    if (parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment
            or not (value.rstrip("/") == BASE or
                    parsed.scheme in ("http", "https") and parsed.hostname in ("127.0.0.1", "localhost", "::1"))):
        raise VerificationError("Only the production origin or loopback is allowed")
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def assert_empty_analysis(body):
    """Never let this verifier dispatch a nonempty choice to the paid route."""
    if type(body) is not dict or set(body) - {"journal", "request_id", "consent", "visitor", "session_key", "language", "experience"}:
        raise VerificationError("Only empty synthetic analysis is allowed")
    journal = body.get("journal")
    events = journal.get("events") if type(journal) is dict else None
    if (type(events) is not list or not events or len(events) > 32
            or any(type(e) is not dict or e.get("type") in ("card_selected", "card_deselected") for e in events)
            or events[-1].get("type") != "session_completed"
            or journal.get("schema_version") != EVENTS_SCHEMA_VERSION):
        raise VerificationError("Only a sealed journal without any selections is allowed")
    if "experience" in body:
        try:
            validate_experience(body["experience"], journal)
        except ValueError:
            raise VerificationError("Only a validated empty-visit experience is allowed") from None


class Client:
    """No redirects, automatic retries, arbitrary origins, or unbounded reads."""
    def __init__(self, base):
        self.base = trusted_origin(base)
        self.target = urlsplit(self.base)
        self.cookie = None
        self.calls = []

    def request(self, path, body=None, headers=None):
        parsed = urlsplit(path)
        allowed_get = {"/api/health", "/runtime-config.js", "/data/decks/pamela-v1.json", "/api/archive/export"}
        allowed_post = {"/api/record/partial", "/api/record/withdraw", "/api/analyze"}
        method = "POST" if body is not None else "GET"
        if parsed.scheme or parsed.netloc or parsed.fragment or parsed.path not in (allowed_post if body is not None else allowed_get):
            raise VerificationError("Acceptance route is not allowed")
        if parsed.query and parsed.path != "/api/archive/export":
            raise VerificationError("Unexpected acceptance query")
        if parsed.path == "/api/analyze":
            assert_empty_analysis(body)
        request_headers = {"User-Agent": "norn-release-check/4.0", **(headers or {})}
        if self.cookie:
            request_headers["Cookie"] = self.cookie
        payload = None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if payload is not None:
            request_headers["Content-Type"] = "application/json"
        connection_type = http.client.HTTPSConnection if self.target.scheme == "https" else http.client.HTTPConnection
        connection = connection_type(self.target.hostname, self.target.port, timeout=60)
        try:
            connection.request(method, path, payload, request_headers)
            response = connection.getresponse()
            renewed = response.getheader("Set-Cookie")
            if renewed:
                candidate = renewed.split(";", 1)[0]
                if not COOKIE.fullmatch(candidate):
                    raise VerificationError("Invalid routing cookie")
                self.cookie = candidate
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise VerificationError("Acceptance response exceeds its size limit")
            self.calls.append({"method": method, "route": parsed.path, "status": response.status,
                               "response_bytes": len(raw)})
            return response.status, raw.decode("utf-8")
        finally:
            connection.close()


def journal_prefix(deck, rounds_done, rng):
    """Completed empty-choice rounds, then a dealt open round; never a selection."""
    research, display = engine_module("research_deck"), engine_module("display_policy")
    events, history = [], []

    def add(kind, **fields):
        events.append({"event_id": f"evt-{len(events) + 1:06d}", "sequence": len(events) + 1, "type": kind, **fields})

    for number in range(1, rounds_done + 2):
        count = rng.randint(4, 6)
        available = display.available_cards(deck.ids, history, count)
        cards, roles = research.draw(research.RANDOM, [], available, count, rng, deck)
        history.append(cards)
        add("round_started", round=number, card_ids=cards,
            display={"policy": research.RANDOM, "deck": deck.label, "roles": roles})
        if number > rounds_done:
            break
        layout = [[i // 2, i % 2] for i in range(len(cards))]
        add("round_ready", round=number, ready_offset_ms=0, unloaded_card_ids=[], layout=layout)
        add("round_completed", round=number, duration_ms=900, hidden_ms=0, seen_card_ids=None, hover=None)
    return {"schema_version": EVENTS_SCHEMA_VERSION, "events": events}


def seal_empty(journal):
    journal = deepcopy(journal)
    events = journal["events"]
    start = next((index for index in range(len(events) - 1, -1, -1)
                  if events[index]["type"] == "round_started"), None)
    if start is None or [event["type"] for event in events[start:]] not in (
            ["round_started"], ["round_started", "round_ready"]):
        raise VerificationError("Expected an open empty round with at most one readiness event")
    current = events[start]
    number, cards = current["round"], current["card_ids"]
    fields_to_append = []
    if events[-1]["type"] != "round_ready":
        fields_to_append.append(("round_ready", {"round": number, "ready_offset_ms": 0,
            "unloaded_card_ids": [], "layout": [[i // 2, i % 2] for i in range(len(cards))]}))
    offset = events[-1].get("ready_offset_ms", 0)
    fields_to_append.extend((
        ("round_completed", {"round": number, "duration_ms": max(900, offset), "hidden_ms": 0,
                             "seen_card_ids": None, "hover": None}),
        ("session_completed", {})))
    for kind, fields in fields_to_append:
        events.append({"event_id": f"evt-{len(events) + 1:06d}", "sequence": len(events) + 1, "type": kind, **fields})
    assert_empty_analysis({"journal": journal})
    return journal


def active_checkpoint(journal):
    """A real select/undo/reselect prefix and two monotonic attention samples.

    This synthetic choice journal must only go to the partial-record route.
    It is deliberately separate from the empty visit's free reveal path.
    """
    journal = deepcopy(journal)
    events = journal["events"]
    start = events[-1]
    if start["type"] != "round_started":
        raise VerificationError("Expected a just-dealt round for the active checkpoint")
    number, cards = start["round"], start["card_ids"]
    layout = [[i // 2, i % 2] for i in range(len(cards))]
    for kind, fields in (
            ("round_ready", {"round": number, "ready_offset_ms": 75,
                             "unloaded_card_ids": [], "layout": layout}),
            ("card_selected", {"round": number, "card_id": cards[0], "t_ms": 120, "input": "mouse", "layout": layout}),
            ("card_deselected", {"round": number, "card_id": cards[0], "t_ms": 240, "input": "mouse", "layout": layout}),
            ("card_selected", {"round": number, "card_id": cards[0], "t_ms": 360, "input": "mouse", "layout": layout})):
        events.append({"event_id": f"evt-{len(events) + 1:06d}", "sequence": len(events) + 1, "type": kind, **fields})
    earlier = {"round": number, "elapsed_ms": 700, "hidden_ms": 0, "ready_offset_ms": 75,
               "seen_card_ids": cards[:1], "hover": [[0, 40, 625]]}
    later = {**earlier, "elapsed_ms": 1300, "seen_card_ids": cards[:2], "hover": [[0, 40, 1225]]}
    return journal, earlier, later


def rows_of(client, token, visitor):
    """Filter in memory; never print, write or report another visitor's row."""
    rows, after = [], ""
    for _ in range(MAX_PAGES):
        status, text = client.request("/api/archive/export?limit=25" + (f"&after={after}" if after else ""),
                                      headers={"Authorization": f"Bearer {token}"})
        if status != 200:
            raise VerificationError("Archive export unavailable")
        page = [json.loads(line) for line in text.splitlines() if line.strip()]
        if not page:
            return rows
        identities = [row.get("record_id") for row in page]
        if (any(type(i) is not str or not re.fullmatch(r"[0-9a-f]{32}", i) for i in identities)
                or identities != sorted(set(identities)) or identities[0] <= after):
            raise VerificationError("Archive export did not advance")
        rows.extend(row for row in page if row.get("visitor_id") == visitor)
        after = identities[-1]
    raise VerificationError("Archive export exceeds the page limit")


def _recovery_file(base, key):
    descriptor, name = tempfile.mkstemp(prefix="norn-partial-cleanup-", suffix=".json",
                                         dir="/tmp" if Path("/tmp").is_dir() else None)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"base": base, "visitor": key, "all": True}, handle)
            handle.write("\n")
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass  # fdopen may already have closed it on a serialization error
        Path(name).unlink(missing_ok=True)
        raise
    return Path(name)


def run_check(client, token):
    """Run checks with unconditional, narrowly scoped test-visitor cleanup."""
    deck = engine_module("research_deck").load_deck("pamela-v1")
    checks, key, recovery = {}, None, None
    report = {"schema_version": "norn.partial-visit-check.v4", "base": client.base,
              "consent_version": CONSENT_VERSION, "deck": deck.label, "model_calls": 0,
              "experience_schema_version": EXPERIENCE_VERSION,
              "checks": checks, "scope": "Synthetic open-round checkpoint, explicit feedback sidecar and empty-reading integration; no model-quality or real-browser exposure claim."}

    def check(name, condition):
        checks[name] = bool(condition)
        if not condition:
            raise VerificationError(name)

    def post(path, body):
        if path == "/api/analyze":
            assert_empty_analysis(body)  # also protects fake/in-process test clients
        status, text = client.request(path, body)
        return status, json.loads(text)

    try:
        status, health = client.request("/api/health")
        check("health_ready", status == 200 and json.loads(health).get("status") == "ok")
        status, config = client.request("/runtime-config.js")
        check("runtime_config", status == 200 and '"collect": true' in config
              and f'"consentVersion": "{CONSENT_VERSION}"' in config and '"deck": "pamela-v1"' in config)
        status, text = client.request("/data/decks/pamela-v1.json")
        check("hosted_deck_version", status == 200 and json.loads(text)["deck_version"] == deck.version)
        status, _ = client.request("/api/archive/export?limit=1", headers={"Authorization": f"Bearer {token}"})
        check("archive_access", status == 200)
        key = secrets.token_urlsafe(24)
        hashed, first_id, second_id = visitor_id(key), secrets.token_hex(16), secrets.token_hex(16)
        recovery = _recovery_file(client.base, key)  # create before the first write
        report["test_visitor_id"] = hashed
        first, second = (journal_prefix(deck, done, random.Random(first_id)) for done in (0, 1))
        def experience(journal, session=first_id):
            # Only the first visit simulates one painted hint. Completing more
            # rounds during seal_empty must not invent additional UI exposure.
            shown = session == first_id and any(event["type"] == "round_completed" and event["round"] == 1
                                                for event in journal["events"])
            return {"schema_version": EXPERIENCE_VERSION,
                    "round_feedback": "on" if session == first_id else "off",
                    "exposures": [{"after_round": 1, "feedback": "blank", "elapsed_ms": 5000}] if shown else []}

        def body(journal, session=first_id):
            return {"consent": CONSENT_VERSION, "visitor": key, "session_key": session,
                    "journal": journal, "experience": experience(journal, session)}

        status, _ = post("/api/record/partial", {"journal": first})
        check("consent_required", status == 400)
        status, result = post("/api/record/partial", body(first))
        check("first_snapshot_stored", status == 200 and result == {"stored": True})
        rows = rows_of(client, token, hashed)
        check("first_snapshot_feedback_on_without_exposure", len(rows) == 1
              and rows[0].get("experience") == experience(first))
        status, result = post("/api/record/partial", body(second))
        check("newer_snapshot_stored", status == 200 and result == {"stored": True})
        rows = rows_of(client, token, hashed)
        check("feedback_exposure_advances_with_prefix", len(rows) == 1
              and rows[0].get("experience") == experience(second))
        status, result = post("/api/record/partial", body(first))
        check("older_snapshot_rejected", status == 200 and result == {"stored": False})
        status, result = post("/api/record/partial", body(second))
        check("same_snapshot_idempotent", status == 200 and result == {"stored": True})
        flipped = {**body(second), "experience": {"schema_version": EXPERIENCE_VERSION,
                                                  "round_feedback": "off", "exposures": []}}
        status, result = post("/api/record/partial", flipped)
        check("feedback_mode_flip_rejected", status == 200 and result == {"stored": False})
        missing = {name: value for name, value in body(second).items() if name != "experience"}
        status, result = post("/api/record/partial", missing)
        check("missing_experience_cannot_erase", status == 200 and result == {"stored": False})
        erased = {**body(second), "experience": experience(first)}
        status, result = post("/api/record/partial", erased)
        check("feedback_exposure_erasure_rejected", status == 200 and result == {"stored": False})
        rows = rows_of(client, token, hashed)
        check("one_latest_partial_row", len(rows) == 1 and rows[0]["record_id"] == first_id
              and rows[0]["schema_version"] == "norn.partial.v1" and rows[0]["journal"] == second
              and rows[0]["consent_version"] == CONSENT_VERSION)
        check("latest_partial_feedback_exposure_retained", len(rows) == 1
              and rows[0].get("experience") == experience(second))
        status, result = post("/api/record/partial", body(first, second_id))
        check("second_tab_stored", status == 200 and result == {"stored": True})
        rows = {row["record_id"]: row for row in rows_of(client, token, hashed)}
        check("two_visits_numbered", len(rows) == 2 and rows[first_id]["visit"] == 1 and rows[second_id]["visit"] == 2)
        check("on_off_visits_distinct_in_archive", len(rows) == 2
              and rows[first_id].get("experience") == experience(second)
              and rows[second_id].get("experience") == experience(first, second_id))
        active, earlier, later = active_checkpoint(first)
        active_body = {**body(active, second_id), "open_round": earlier}
        status, result = post("/api/record/partial", active_body)
        check("open_round_choices_stored", status == 200 and result == {"stored": True})
        rows = {row["record_id"]: row for row in rows_of(client, token, hashed)}
        check("open_round_selection_and_undo_preserved", len(rows) == 2
              and rows[second_id]["journal"] == active and rows[second_id].get("open_round") == earlier
              and rows[second_id]["visit"] == 2)
        advanced_body = {**active_body, "open_round": later}
        status, result = post("/api/record/partial", advanced_body)
        check("same_prefix_attention_advances", status == 200 and result == {"stored": True})
        status, result = post("/api/record/partial", active_body)
        check("stale_attention_rejected", status == 200 and result == {"stored": False})
        status, result = post("/api/record/partial", body(first, second_id))
        check("stale_prefix_cannot_erase_active_choices", status == 200 and result == {"stored": False})
        rows = {row["record_id"]: row for row in rows_of(client, token, hashed)}
        check("latest_open_round_attention_retained", len(rows) == 2
              and rows[second_id]["journal"] == active and rows[second_id].get("open_round") == later
              and rows[second_id]["visit"] == 2)
        check("attention_upgrade_keeps_feedback_modes", len(rows) == 2
              and rows[first_id].get("experience") == experience(second)
              and rows[second_id].get("experience") == experience(active, second_id))
        full = body(seal_empty(second))
        full["request_id"] = "empty-acceptance-" + secrets.token_hex(12)
        status, result = post("/api/analyze", full)
        check("empty_reading_makes_no_model_call", status == 200 and result.get("provider_calls") == 0
              and result.get("empty_selection") is True)
        check("full_receipt_uses_same_session_key", result.get("record") == {"record_id": first_id})
        rows = {row["record_id"]: row for row in rows_of(client, token, hashed)}
        check("full_upgrade_keeps_visit_number", len(rows) == 2 and rows[first_id]["schema_version"] == "norn.record.v2"
              and rows[first_id]["visit"] == 1 and rows[second_id]["visit"] == 2)
        check("full_upgrade_preserves_feedback_exposure", len(rows) == 2
              and rows[first_id].get("experience") == experience(second)
              and rows[second_id].get("experience") == experience(active, second_id))
        status, result = post("/api/record/partial", body(second))
        check("late_partial_cannot_recreate_completed_visit", status == 200 and result == {"stored": False})
        status, result = post("/api/record/withdraw", {"visitor": key, "record_id": first_id})
        check("single_withdrawal_confirmed", status == 200 and result == {"withdrawn": True})
        status, result = post("/api/record/partial", body(second))
        check("single_withdrawal_blocks_late_write", status == 200 and result == {"stored": False})
        status, result = post("/api/record/withdraw", {"visitor": key, "all": True})
        check("all_withdrawal_confirmed", status == 200 and result == {"withdrawn": True})
        for name, identity in (("all_withdrawal_blocks_old_visit", second_id),
                               ("all_withdrawal_blocks_new_visit_on_old_key", secrets.token_hex(16))):
            status, result = post("/api/record/partial", body(first, identity))
            check(name, status == 200 and result == {"stored": False})
    except Exception as exc:
        report["failure_type"] = type(exc).__name__  # no raw server text, token, reading or capability
    finally:
        if key is not None:
            cleanup_ok = False
            for _ in range(2):  # an idempotent test-visitor withdrawal only, never a model retry
                try:
                    status, result = post("/api/record/withdraw", {"visitor": key, "all": True})
                    cleanup_ok = status == 200 and result == {"withdrawn": True} and not rows_of(client, token, visitor_id(key))
                    if cleanup_ok:
                        break
                except Exception:
                    continue
            checks["cleanup_confirmed_no_test_rows"] = cleanup_ok
            if recovery is not None and cleanup_ok:
                recovery.unlink(missing_ok=True)
            elif recovery is not None:
                # The report intentionally excludes this deletion capability.
                print(f"Test cleanup unconfirmed; private recovery file: {recovery}", file=sys.stderr)
    report["http_calls"] = list(getattr(client, "calls", []))
    report["passed"] = bool(checks) and all(checks.values()) and "failure_type" not in report
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.output.exists() or not args.output.parent.is_dir():
        parser.error("output must be a new file in an existing directory")
    try:
        client = Client(args.base)  # validate the destination before reading the operator credential
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if not token or "\n" in token or "\r" in token:
            raise VerificationError("Archive token is unavailable")
        report = run_check(client, token)
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, allow_nan=False, indent=2)
            handle.write("\n")
    except (OSError, ValueError) as exc:
        print(f"Partial-visit verification failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(json.dumps({"checks": len(report["checks"]), "passed": report["passed"], "model_calls": 0,
                      "report": str(args.output)}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())

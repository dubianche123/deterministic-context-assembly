#!/usr/bin/env python3
"""Bounded Last Card release check using one disposable synthetic visitor.

The default stops after the sealed finale and calls no models. --live explicitly
permits one Jev prediction, one reading, and one follow-up; retries of the same
IDs only verify receipts. No provider key, private prose, or other visitor's
record is printed or written. The test visitor is withdrawn even on failure.
"""
import argparse
from copy import deepcopy
import http.client
import json
from pathlib import Path
import random
import secrets
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.verify_partial_visit import (BASE, TOKEN_FILE, COOKIE, MAX_RESPONSE_BYTES,
    VerificationError, trusted_origin, rows_of, _recovery_file)  # noqa: E402
from norn.collection import CONSENT_VERSION, visitor_id  # noqa: E402
from norn.core import engine_module  # noqa: E402
from norn.events import EVENTS_SCHEMA_VERSION  # noqa: E402
from norn.finale import commitment_for  # noqa: E402
from norn.product import PRODUCT_VERSION, PACING_POLICY_VERSION  # noqa: E402


class Client:
    def __init__(self, base, live=False):
        self.base, self.live = trusted_origin(base), live
        self.target, self.cookie, self.calls = urlsplit(self.base), None, []
        self.dispatched = {"/api/jev/finale": 0, "/api/analyze": 0, "/api/dialogue": 0}
        self.identities = {}

    def request(self, path, body=None, headers=None):
        route = urlsplit(path)
        gets = {"/api/health", "/api/jev/status", "/api/archive/export"}
        posts = {"/api/finale/start", "/api/finale/choose", "/api/pacing",
                 "/api/record/partial", "/api/record/withdraw"}
        if self.live:
            posts |= {"/api/jev/start", *self.dispatched}
        if (route.scheme or route.netloc or route.fragment or route.path not in (posts if body is not None else gets)
                or route.query and route.path != "/api/archive/export"):
            raise VerificationError("Release-check route not permitted")
        if route.path in self.dispatched:
            # At most one unique paid operation for each endpoint. An identical
            # reading retry verifies a durable receipt, never submits new input.
            encoded_id = json.dumps(body, sort_keys=True)
            if route.path in self.identities and self.identities[route.path] != encoded_id:
                raise VerificationError("Paid operation changed during verification")
            self.identities[route.path] = encoded_id
            self.dispatched[route.path] += 1
            if self.dispatched[route.path] > 2:
                raise VerificationError("Paid receipt retry limit exceeded")
        values = {"User-Agent": "norn-last-card-check/1", **(headers or {})}
        if self.cookie:
            values["Cookie"] = self.cookie
        raw = None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
        if raw is not None:
            values["Content-Type"] = "application/json"
        kind = http.client.HTTPSConnection if self.target.scheme == "https" else http.client.HTTPConnection
        connection = kind(self.target.hostname, self.target.port, timeout=60)
        try:
            connection.request("POST" if raw is not None else "GET", path, raw, values)
            response = connection.getresponse()
            cookie = response.getheader("Set-Cookie")
            if cookie:
                cookie = cookie.split(";", 1)[0]
                if not COOKIE.fullmatch(cookie):
                    raise VerificationError("Unexpected actor cookie")
                self.cookie = cookie
            result = response.read(MAX_RESPONSE_BYTES + 1)
            if len(result) > MAX_RESPONSE_BYTES:
                raise VerificationError("Oversized release-check response")
            self.calls.append({"route": route.path, "status": response.status})
            return response.status, result.decode()
        finally:
            connection.close()


def fixture(deck):
    research = engine_module("research_deck")
    events, seen, rows = [], set(), []
    rng = random.Random(72891)
    def add(kind, **fields):
        events.append({"event_id": f"release-{len(events) + 1}", "sequence": len(events) + 1,
                       "type": kind, **fields})
    for number in range(1, 5):
        cards, roles = research.draw(research.RANDOM, rows, [c for c in deck.ids if c not in seen], 4, rng, deck)
        seen.update(cards)
        add("round_started", round=number, card_ids=cards,
            display={"policy": research.RANDOM, "deck": deck.label, "roles": roles})
        add("round_ready", round=number, ready_offset_ms=100, unloaded_card_ids=[], layout=[[0, 0], [0, 1], [1, 0], [1, 1]])
        add("card_selected", round=number, card_id=cards[0], t_ms=300, input="touch",
            layout=[[0, 0], [0, 1], [1, 0], [1, 1]])
        add("round_completed", round=number, duration_ms=900, hidden_ms=0, seen_card_ids=None, hover=None)
        # Drawing is random here; it does not fit or select a winning fixture.
    add("session_completed")
    return {"schema_version": EVENTS_SCHEMA_VERSION, "events": events}


def run_check(client, token):
    checks, key, recovery = {}, None, None
    report = {"schema_version": "norn.last-card-check.v1", "base": client.base, "live": client.live,
              "checks": checks, "provider_calls": {"jev": 0, "reading": 0, "followup": 0},
              "scope": "Synthetic integration only; no human preference or model-quality conclusion."}
    def check(name, okay):
        checks[name] = bool(okay)
        if not okay:
            raise VerificationError(name)
    def post(route, body):
        status, text = client.request(route, body)
        return status, json.loads(text)
    try:
        status, text = client.request("/api/health")
        check("health", status == 200 and json.loads(text)["status"] == "ok")
        key, visit = secrets.token_urlsafe(24), secrets.token_hex(16)
        recovery = _recovery_file(client.base, key)
        deck = engine_module("research_deck").load_deck("pamela-v1")
        journal = fixture(deck)
        product = {"schema_version": PRODUCT_VERSION, "pacing_policy": PACING_POLICY_VERSION}
        privacy = {"consent": CONSENT_VERSION, "visitor": key, "session_key": visit}
        payload = {"journal": journal, "product": product, **privacy}
        status, result = post("/api/record/partial", payload)
        check("sealed_exploration_saved", status == 200 and result.get("stored") is True)
        if client.live:
            status, result = post("/api/jev/start", {"enabled": True, "mode": "play", "deck": deck.label,
                "client_session": secrets.token_hex(16), "purpose": "finale"})
            check("jev_preentry_capability", status == 200 and result.get("status") == "ready" and result.get("provider_calls") == 0)
            ticket = result["jev_session"]
        start = {**payload, "challenge_id": "release-" + secrets.token_hex(12), "language": "zh",
                 **({"jev_session": ticket} if client.live else {})}
        status, public = post("/api/finale/start", start)
        check("hidden_sealed_forecast", status == 200 and not {"predicted_card_id", "probabilities", "salt"} & set(public))
        seen = {c for e in journal["events"] if e["type"] == "round_started" for c in e["card_ids"]}
        check("four_unseen_cards", len(set(public["candidate_ids"])) == 4 and not seen & set(public["candidate_ids"]))
        check("start_receipt_stable", post("/api/finale/start", start) == (200, public))
        if client.live:
            status, response = post("/api/jev/finale", {"challenge_id": start["challenge_id"]})
            report["provider_calls"]["jev"] += response.get("provider_calls", 0)
            check("jev_ready_without_answer_leak", status == 200 and response.get("status") == "ready"
                  and not {"predicted_card_id", "probabilities"} & set(response))
        choice = {"challenge_id": start["challenge_id"], "card_id": public["candidate_ids"][0], "elapsed_ms": 2000,
                  "observation": {"ready_offset_ms": 500, "hidden_ms": 100, "layout": [[0, 0], [0, 1], [1, 0], [1, 1]], "input": "touch"}}
        status, result = post("/api/finale/choose", choice)
        check("receipt_opens_original_commitment", status == 200 and result["commitment"] == public["commitment"] == commitment_for(result))
        check("choice_retry_stable", post("/api/finale/choose", choice) == (200, result))
        changed = post("/api/finale/choose", {**choice, "card_id": public["candidate_ids"][1]})
        check("choice_cannot_change", changed[0] == 409)
        rows = rows_of(client, token, visitor_id(key))
        check("final_choice_saved_before_reading", len(rows) == 1 and rows[0].get("finale") == result
              and rows[0]["record_id"] == visit and rows[0]["journal"] == journal)
        check("terminal_choice_not_training", choice["card_id"] not in seen)
        if client.live:
            analysis = {**payload, "finale_id": start["challenge_id"], "language": "zh", "request_id": "release-reading-" + visit}
            status, reading = post("/api/analyze", analysis)
            report["provider_calls"]["reading"] += reading.get("provider_calls", 0)
            check("live_persona_reading", status == 200 and reading.get("status") == "ok" and reading.get("reading")
                  and reading.get("persona", {}).get("card_id") == choice["card_id"] and reading.get("max_dialogue_turns") == 3)
            check("reading_preserves_seal", reading["finale"] == result and reading["profile_hash"] == result["profile_hash"])
            check("reading_retry_no_new_call", post("/api/analyze", analysis) == (200, reading))
            status, followup = post("/api/dialogue", {"session_id": reading["session_id"], "user_message": "给这个角色一句座右铭。", "turn_id": "release-ask-" + visit})
            report["provider_calls"]["followup"] += followup.get("provider_calls", 0)
            check("live_followup", status == 200 and followup.get("status") == "ok" and followup.get("reply"))
            rows = rows_of(client, token, visitor_id(key))
            check("full_record_replaces_partial", len(rows) == 1 and rows[0]["schema_version"] == "norn.record.v2"
                  and rows[0]["record_id"] == visit and rows[0].get("finale") == result)
        check("bounded_provider_calls", all(value <= 1 for value in report["provider_calls"].values()))
    except Exception as exc:
        report["failure_type"] = type(exc).__name__
    finally:
        if key is not None:
            clean = False
            try:
                status, response = post("/api/record/withdraw", {"visitor": key, "all": True})
                clean = status == 200 and response.get("withdrawn") is True and not rows_of(client, token, visitor_id(key))
            except Exception:
                pass
            checks["test_visitor_withdrawn"] = clean
            if recovery is not None and clean:
                recovery.unlink(missing_ok=True)
            elif recovery is not None:
                print(f"Cleanup unconfirmed; private recovery file: {recovery}", file=sys.stderr)
    report["http_calls"] = client.calls
    report["passed"] = bool(checks) and all(checks.values()) and "failure_type" not in report
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--live", action="store_true", help="Permit one Jev, reading and follow-up each")
    parser.add_argument("--token-file", type=Path, default=TOKEN_FILE)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or not args.output.parent.is_dir():
        parser.error("output must be a new file in an existing directory")
    client = Client(args.base, args.live)
    token = args.token_file.read_text().strip()
    if not token or "\n" in token or "\r" in token:
        parser.error("archive token is unavailable")
    report = run_check(client, token)
    with args.output.open("x") as handle:
        json.dump(report, handle, ensure_ascii=False, allow_nan=False, indent=2)
        handle.write("\n")
    print(json.dumps({"passed": report["passed"], "checks": len(report["checks"]),
                      "provider_calls": report["provider_calls"], "report": str(args.output)}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())

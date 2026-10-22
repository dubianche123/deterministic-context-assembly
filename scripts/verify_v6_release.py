#!/usr/bin/env python3
"""Exercise the PAMELA/v6 release. Offline by default; --live allows two model calls.

Records only checks, hashes, resource usage and transport timings. Synthetic
choices are integration fixtures, never evidence of preference accuracy.
No archive consent is sent, so the fixture does not enter research collection.
"""

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from norn.core import engine_module
from norn.events import EVENTS_SCHEMA_VERSION
from norn.events import content_hash
from norn.replay import replay_document
from verify_cloudflare import Client, COOKIE, require


class CheckpointClient(Client):
    """Keep the routing capability private so a resumed check reuses paid receipts."""

    def __init__(self, origin, *, private_state=None, **kwargs):
        super().__init__(origin, **kwargs)
        self.private_state = private_state
        if private_state and private_state.exists():
            require(private_state.stat().st_mode & 0o077 == 0, "Private checkpoint must be owner-only")
            state = json.loads(private_state.read_text())
            require(state.get("origin") == self.origin and COOKIE.fullmatch(state.get("cookie", "")),
                    "Private checkpoint does not match this origin")
            self.cookie = state["cookie"]

    def request(self, *args, **kwargs):
        result = super().request(*args, **kwargs)
        if self.private_state and self.cookie:
            with os.fdopen(os.open(self.private_state, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as handle:
                json.dump({"origin": self.origin, "cookie": self.cookie}, handle)
        return result


def fixture(rounds=8):
    research = engine_module("research_deck")
    deck = research.load_deck("pamela-v1")
    rng, events, history = random.Random(20261022), [], []

    def add(kind, **fields):
        events.append({"event_id": f"evt-{len(events) + 1:06d}", "sequence": len(events) + 1,
                       "type": kind, **fields})

    for number in range(1, rounds + 1):
        available = engine_module("display_policy").available_cards(deck.ids, history, 4)
        cards, roles = research.draw(research.MIXED, research.observations({"events": events}),
                                     available, 4, rng, deck)
        history.append(cards)
        layout = [[i // 2, i % 2] for i in range(4)]
        add("round_started", round=number, card_ids=cards,
            display={"policy": research.MIXED, "deck": deck.label, "roles": roles})
        add("round_ready", round=number, ready_offset_ms=0, unloaded_card_ids=[], layout=layout)
        card = max(cards, key=lambda item: deck.x2[item][1])
        add("card_selected", round=number, card_id=card, t_ms=1000, input="mouse", layout=layout)
        add("round_completed", round=number, duration_ms=3000, hidden_ms=0, seen_card_ids=None, hover=None)
    add("session_completed")
    return {"schema_version": EVENTS_SCHEMA_VERSION, "events": events}


def verify(url, *, live=False, private_state=None):
    client = CheckpointClient(url, live=live, timeout=90, retries=0, private_state=private_state)
    checks = {}

    def check(name, condition):
        require(condition, name)
        checks[name] = True

    health, _ = client.ok("GET", "/api/health")
    check("expected_renderer", health["simulated"] is (not live))
    if live:
        check("official_deepseek_model", health.get("model") == "deepseek-flash")
    connection_type = http.client.HTTPSConnection if client.target.scheme == "https" else http.client.HTTPConnection
    connection = connection_type(client.target.hostname, client.target.port, timeout=30)
    try:
        connection.request("GET", "/runtime-config.js")
        response = connection.getresponse()
        require(response.status == 200, "runtime config must be accessible")
        config = json.loads(response.read().decode().split(" = ", 1)[1].rstrip(";\n"))
    finally:
        connection.close()
    check("main_deck_pamela", config.get("deck") == "pamela-v1")
    journal = fixture()
    prefix = {"schema_version": journal["schema_version"], "events": journal["events"][:6]}
    preview, _ = client.ok("POST", "/api/preview", {"journal": prefix, "compact": True})
    check("open_prefix_preview_is_pure", preview["provider_calls"] == 0 and not preview["complete"])
    path, _ = client.ok("POST", "/api/trajectory", {"journal": journal})
    check("nine_replay_states", path["schema_version"] == "norn.research-trajectory.v1"
          and len(path["states"]) == 9 and path["provider_calls"] == 0)
    request = {"journal": journal, "language": "en", "request_id": "v6-release-reading"}
    reading, _ = client.ok("POST", "/api/analyze", request)
    check("reading_created", bool(reading.get("reading")) and bool(reading.get("session_id")))
    check("not_collected", not reading.get("record"))
    repeated, _ = client.ok("POST", "/api/analyze", request)
    check("reading_retry_idempotent", repeated == reading)
    session = reading["session_id"]
    exported, before = client.ok("GET", f"/api/sessions/{session}/export")
    ledger = exported["contribution_ledger"]
    check("all_scores_reconstructed", len(ledger["cards"]) == 256 and all(
        len(row["terms"]) == 9 and sum(row["terms"]) == row["score"]
        and all(t == w * x for t, w, x in zip(row["terms"], ledger["weights"], row["features"]))
        for row in ledger["cards"]))
    check("export_content_hashes_valid", all(
        content_hash({key: value for key, value in document.items() if key != field}) == document[field]
        for document, field in ((exported["profile"], "profile_hash"), (ledger, "ledger_hash"))))
    # Test exact local replay separately. v6 retains the frozen native-libm
    # contract: a WASM export may be rejected by CPython for a one-ulp fit
    # difference. This verifier records that limitation, without weakening
    # either replay endpoint or claiming exact cross-runtime equivalence.
    try:
        rebuilt = replay_document(exported)
        exact_local_replay = rebuilt["profile"] == exported["profile"] and rebuilt["contribution_ledger"] == ledger
    except ValueError:
        exact_local_replay = False
    imported, _ = client.ok("POST", "/api/replay", exported)
    check("hosted_import_verified", imported.get("verified") is True)
    followup = {"session_id": session, "user_message": "Which choices support that reading?",
                "turn_id": "v6-release-followup"}
    reply, _ = client.ok("POST", "/api/dialogue", followup)
    repeated, _ = client.ok("POST", "/api/dialogue", followup)
    check("followup_retry_idempotent", reply == repeated and reply.get("turn_count") == 1)
    final, _ = client.ok("GET", f"/api/sessions/{session}/inspect")
    _, after = client.ok("GET", f"/api/sessions/{session}/export")
    check("export_frozen_after_followup", before == after)
    model_calls = sum(call.get("provider_calls", 0) for call in final.get("calls", []))
    check("bounded_model_calls", model_calls == (2 if live else 0))
    saved_path, _ = client.ok("GET", f"/api/sessions/{session}/trajectory")
    check("saved_and_live_trajectory_equal", saved_path["states"] == path["states"])
    check("frozen_profile_identity", reply["profile_hash"] == reading["profile_hash"]
          == exported["profile"]["profile_hash"])
    usage = {}
    for counter in ("input_tokens", "output_tokens", "cache_read_input_tokens"):
        values = [call.get("usage", {}).get(counter) for call in final.get("calls", [])]
        usage[counter] = sum(values) if all(type(value) is int for value in values) else None
    return {"schema_version": "norn.v6-release-check.v1", "target_origin": client.origin,
            "fixture_scope": "Synthetic integration only; no human preference or accuracy claim",
            "model": "deepseek-flash" if live else "mock", "model_calls": model_calls,
            "collection_consent": False, "checks": checks, "usage": usage,
            "exact_local_replay": exact_local_replay,
            "numerical_contract": ledger["numerical_contract"],
            "limitations": ([] if exact_local_replay else [
                "Strict CPython replay rejected this native-libm export. Hosted replay is still fully refitted and verified; no tolerance was substituted."]),
            "profile_hash": exported["profile"]["profile_hash"], "ledger_hash": ledger["ledger_hash"],
            "export_sha256": hashlib.sha256(before).hexdigest(), "http_calls": client.calls}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--live", action="store_true", help="allow one real reading and one follow-up (2 calls)")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--private-state", type=Path, help="owner-only cookie checkpoint; reuse it to retry without fresh paid calls")
    args = parser.parse_args()
    if args.live and not args.private_state:
        parser.error("--live requires --private-state so repeated checks reuse the same paid receipts")
    report = verify(args.url, live=args.live, private_state=args.private_state)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({"checks": len(report["checks"]), "model_calls": report["model_calls"],
                      "exact_local_replay": report["exact_local_replay"],
                      "report": str(args.output)}))

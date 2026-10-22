#!/usr/bin/env python3
r"""Verify a hosted mock Norn against this checkout without model/API charges.

Fresh workflow (24 fixture rounds, reading and three idempotent follow-ups):
  python3 scripts/verify_cloudflare.py --url https://YOUR-WORKER.workers.dev \
      --output /tmp/norn-smoke.json --private-state /tmp/norn-private.json

After republishing the Worker, reuse the actor without repeating the dialogue:
  python3 scripts/verify_cloudflare.py --resume /tmp/norn-private.json \
      --output /tmp/norn-resume.json

Cookies are held in memory unless --private-state is explicitly requested.
Reports never contain cookies, server session IDs, readings or user questions.
Only network/transport failures are retried, using the same idempotency IDs;
HTTP error responses are returned immediately and checked by the workflow.
"""

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from norn.replay import replay_document


PRIVATE_SCHEMA = "norn.cloud-smoke.private.v1"
REPORT_SCHEMA = "norn.cloud-smoke.v1"
QUESTION = "这些线索意味着什么？"
READING_ID = "runtime-reading-24"
COOKIE = re.compile(r"__Host-norn-actor=[A-Za-z0-9_-]{43}\Z")
SESSION = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


class VerificationError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise VerificationError(message)


def digest_bytes(value):
    return hashlib.sha256(value).hexdigest()


def digest_json(value):
    return digest_bytes(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                  sort_keys=True, separators=(",", ":")).encode("utf-8"))


def origin_url(value):
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise VerificationError("URL must be an HTTP(S) origin") from exc
    require(parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username
            and not parsed.password and parsed.path in ("", "/") and not parsed.query
            and not parsed.fragment, "URL must be an HTTP(S) origin without credentials or a path")
    require(parsed.scheme == "https" or parsed.hostname in ("localhost", "127.0.0.1", "::1"),
            "Use HTTPS for a hosted deployment; HTTP is allowed for loopback testing")
    return f"{parsed.scheme}://{parsed.netloc.lower()}".rstrip("/")


def route_label(path):
    return re.sub(r"(/api/sessions/)[^/]+/", r"\1<session>/", path.split("?", 1)[0])


class Client:
    def __init__(self, origin, *, retries=2, timeout=30, cookie=None, live=False):
        self.origin = origin_url(origin)
        self.target = urlsplit(self.origin)
        self.retries, self.timeout, self.cookie = retries, timeout, cookie
        self.calls = []
        self.live = live

    def request(self, method, path, body=None, *, use_cookie=True):
        headers = {"Content-Type": "application/json"}
        if use_cookie and self.cookie:
            headers["Cookie"] = self.cookie
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        connection_type = http.client.HTTPSConnection if self.target.scheme == "https" else http.client.HTTPConnection
        started = time.perf_counter()
        for attempt in range(self.retries + 1):
            connection = connection_type(self.target.hostname, self.target.port, timeout=self.timeout)
            try:
                connection.request(method, path, payload, headers)
                response = connection.getresponse()
                renewed = response.getheader("Set-Cookie")
                if use_cookie and renewed:
                    candidate = renewed.split(";", 1)[0]
                    require(COOKIE.fullmatch(candidate), "Server returned an invalid actor cookie")
                    # Keep the capability even if receiving the response body
                    # fails: a retry must reach the same actor and receipt.
                    self.cookie = candidate
                    headers["Cookie"] = candidate
                raw = response.read()
                status = response.status
                break
            except (OSError, http.client.HTTPException) as exc:
                if attempt == self.retries:
                    raise VerificationError(f"Transport failed for {method} {route_label(path)}") from None
                time.sleep(attempt + 1)
            finally:
                connection.close()
        self.calls.append({"method": method, "route": route_label(path), "status": status,
                           "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                           "response_bytes": len(raw), "network_attempts": attempt + 1})
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError):
            raise VerificationError(f"Expected JSON for {method} {route_label(path)}") from None
        require(type(result) is dict, f"Expected a JSON object for {method} {route_label(path)}")
        return status, result, raw

    def ok(self, method, path, body=None):
        status, result, raw = self.request(method, path, body)
        require(status == 200, f"Unexpected HTTP {status} for {method} {route_label(path)}")
        return result, raw


def verify_export(document):
    try:
        verified = replay_document(document)
        require(verified["profile"] == document["profile"], "Local replay differs from the exported profile")
    except (KeyError, ValueError):
        raise VerificationError("Downloaded export failed local canonical replay verification") from None


def report_base(client, mode):
    return {"schema_version": REPORT_SCHEMA, "mode": mode,
            "runtime": "Cloudflare workerd / Python 3.14", "target_origin": client.origin,
            "provider": "openai-compatible" if client.live else "mock", "calls": client.calls, "model_calls": 0,
            "latency_scope": "client wall time including transport retries; not Worker CPU time",
            "network_retry_policy": {"additional_attempts": client.retries,
                                     "http_status_responses_retried": False}, "checks": {}}


def fresh_workflow(client):
    fixture = ROOT / "tests" / "fixtures" / "legacy-profile-v1.json"
    journal = json.loads(fixture.read_text(encoding="utf-8"))["journal"]
    health, _ = client.ok("GET", "/api/health")
    require(health.get("provider") == ("openai-compatible" if client.live else "mock")
            and health.get("simulated") is (not client.live),
            "Deployment mode differs from verifier; opt in with --live for four paid model calls")
    if client.live:
        require(health.get("model") == "deepseek-flash", "Unexpected live model")
    client.ok("POST", "/api/preview", {"journal": journal, "compact": True})
    analysis_request = {"journal": journal, "request_id": READING_ID}
    reading, _ = client.ok("POST", "/api/analyze", analysis_request)
    session = reading.get("session_id")
    require(type(session) is str and SESSION.fullmatch(session), "Reading did not create a valid session")
    repeated, _ = client.ok("POST", "/api/analyze", analysis_request)
    require(repeated == reading, "Analysis retry did not reuse its original result")
    initial, _ = client.ok("GET", f"/api/sessions/{session}/inspect")
    compact, _ = client.ok("GET", f"/api/sessions/{session}/inspect?compact=1")
    require(compact.get("preference_map") == initial.get("preference_map"),
            "Compact inspection changed the authoritative map scores")
    exported, export_bytes = client.ok("GET", f"/api/sessions/{session}/export")
    answers = [reading["reading"]]
    for turn in range(1, 4):
        last_turn_request = {"session_id": session, "user_message": QUESTION,
                             "turn_id": f"runtime-turn-{turn}"}
        reply, _ = client.ok("POST", "/api/dialogue", last_turn_request)
        require(reply.get("turn_count") == turn, "Dialogue did not consume exactly one turn")
        repeated, _ = client.ok("POST", "/api/dialogue", last_turn_request)
        require(repeated == reply, "Dialogue retry did not reuse its original result")
        answers.append(reply["reply"])
    final, _ = client.ok("GET", f"/api/sessions/{session}/inspect")
    require(len(final.get("calls", [])) == 4, "Expected one reading and three dialogue calls")
    model_calls = sum(call.get("provider_calls", 0) for call in final["calls"])
    require(model_calls == (4 if client.live else 0), "Unexpected number of model dispatches")
    prefix = initial["calls"][0]["prompt"]["messages"] + [{"role": "assistant", "content": answers[0]}]
    for call, answer in zip(final["calls"][1:], answers[1:]):
        require(call["prompt"]["messages"][:-1] == prefix, "Native dialogue prefix changed")
        prefix = call["prompt"]["messages"] + [{"role": "assistant", "content": answer}]
    require(final.get("context") == initial.get("context"), "Frozen context changed during dialogue")
    require(final.get("profile") == initial.get("profile"), "Frozen profile changed during dialogue")
    _, final_bytes = client.ok("GET", f"/api/sessions/{session}/export")
    require(final_bytes == export_bytes, "Dialogue changed the original export bytes")
    verify_export(exported)
    client.ok("POST", "/api/preview", {"journal": {"schema_version": journal["schema_version"],
                                                  "events": journal["events"][:5]}, "compact": True})
    replayed, _ = client.ok("POST", "/api/replay", exported)
    require(replayed.get("verified") is True, "Hosted import did not verify the export")
    after_pure, _ = client.ok("GET", f"/api/sessions/{session}/inspect")
    require(after_pure.get("calls") == final.get("calls"), "Pure replay or preview changed the conversation")
    status, _, _ = client.request("GET", f"/api/sessions/{session}/inspect", use_cookie=False)
    require(status == 404, "A different actor could inspect this session")
    for suffix in ("?compact=0", "?compact=1&compact=1", "?invalid=1"):
        status, _, _ = client.request("GET", f"/api/sessions/{session}/inspect" + suffix)
        require(status == 400, "Inspection accepted an invalid query parameter")
    profile = exported["profile"]
    report = report_base(client, "fresh")
    report.update(profile_version=profile["schema_version"], algorithm_version=profile["algorithm_version"],
                  model_version=profile["preference_model"]["model_version"], rounds=reading["total_rounds"],
                  selections=reading["total_selections"], render_calls=4, dialogue_turns=final["turn_count"],
                  map_points=len(compact["preference_map"]["points"]))
    report["model_calls"] = model_calls
    report["model"] = final["calls"][0].get("model")
    report["usage"] = {}
    for counter in ("input_tokens", "output_tokens", "cache_read_input_tokens", "uncached_input_tokens"):
        values = [call.get("usage", {}).get(counter) for call in final["calls"]]
        report["usage"][counter] = sum(values) if all(type(value) is int for value in values) else None
    report["checks"] = {"rpc_request_stream": True, "sqlite_transaction_sync": True,
                        "alarm_scheduled": True, "same_cookie_session_restoration": True,
                        "different_cookie_isolation": True, "reading_and_turn_idempotency": True,
                        "full_context_frozen": True, "export_bytes_unchanged_after_dialogue": True,
                        "cpython_replay_of_wasm_export": True, "pure_preview_import_preserve_conversation": True,
                        "strict_inspect_query": True, "native_dialogue_prefix_frozen": True}
    private = {"schema_version": PRIVATE_SCHEMA, "target_origin": client.origin, "cookie": client.cookie,
               "session_id": session, "analysis_request": analysis_request,
               "analysis_response_sha256": digest_json(reading), "last_turn_request": last_turn_request,
               "last_turn_response_sha256": digest_json(reply), "export_sha256": digest_bytes(export_bytes),
               "context_sha256": digest_json(final["context"]), "profile_hash": profile["profile_hash"],
               "turn_count": final["turn_count"], "render_calls": len(final["calls"])}
    return report, export_bytes, private


def load_private(path):
    require(stat.S_IMODE(path.stat().st_mode) & 0o077 == 0, "Private state must have owner-only permissions (0600)")
    value = json.loads(path.read_text(encoding="utf-8"))
    require(type(value) is dict and value.get("schema_version") in (None, PRIVATE_SCHEMA),
            "Unsupported private-state format")
    cookie, session = value.get("cookie"), value.get("session_id", value.get("session"))
    require(type(cookie) is str and COOKIE.fullmatch(cookie), "Invalid private-state actor cookie")
    require(type(session) is str and SESSION.fullmatch(session), "Invalid private-state session identifier")
    value["session_id"] = session
    return value


def resume_workflow(client, state, baseline_export=None):
    session = state["session_id"]
    client.cookie = state["cookie"]
    health, _ = client.ok("GET", "/api/health")
    require(health.get("provider") == ("openai-compatible" if client.live else "mock")
            and health.get("simulated") is (not client.live), "Deployment mode differs from verifier")
    initial, _ = client.ok("GET", f"/api/sessions/{session}/inspect")
    require(initial.get("turn_count") == state.get("turn_count", 3), "Resumed dialogue turn count changed")
    require(len(initial.get("calls", [])) == state.get("render_calls", 4), "Resumed call history changed")
    exported, export_bytes = client.ok("GET", f"/api/sessions/{session}/export")
    verify_export(exported)
    expected_export = state.get("export_sha256")
    if baseline_export is not None:
        baseline_digest = digest_bytes(baseline_export.read_bytes())
        require(expected_export is None or expected_export == baseline_digest,
                "Baseline export differs from the saved verification state")
        expected_export = baseline_digest
    if expected_export is not None:
        require(digest_bytes(export_bytes) == expected_export, "Resumed export differs from the original bytes")
    if state.get("context_sha256"):
        require(digest_json(initial.get("context")) == state["context_sha256"], "Resumed context differs from the original")
    if state.get("profile_hash"):
        require(exported["profile"]["profile_hash"] == state["profile_hash"], "Resumed frozen profile changed")
    analysis_request = state.get("analysis_request") or {"journal": exported["journal"], "request_id": READING_ID}
    reading, _ = client.ok("POST", "/api/analyze", analysis_request)
    require(reading.get("session_id") == session, "Analysis retry created a different session")
    require(reading.get("request_id") == initial["calls"][0].get("request_id"), "Original analysis receipt was not restored")
    if state.get("analysis_response_sha256"):
        require(digest_json(reading) == state["analysis_response_sha256"], "Resumed analysis receipt changed")
    last_turn_request = state.get("last_turn_request") or {
        "session_id": session, "user_message": QUESTION, "turn_id": "runtime-turn-3"}
    require(last_turn_request.get("session_id") == session, "Private last-turn request belongs to another session")
    reply, _ = client.ok("POST", "/api/dialogue", last_turn_request)
    require(reply.get("request_id") == initial["calls"][-1].get("request_id"),
            "Original dialogue receipt was not restored")
    if state.get("last_turn_response_sha256"):
        require(digest_json(reply) == state["last_turn_response_sha256"], "Resumed dialogue receipt changed")
    final, _ = client.ok("GET", f"/api/sessions/{session}/inspect")
    require(final.get("calls") == initial.get("calls"), "Resume consumed another render call")
    require(final.get("context") == initial.get("context") and final.get("profile") == initial.get("profile"),
            "Resume changed the frozen context or profile")
    _, final_bytes = client.ok("GET", f"/api/sessions/{session}/export")
    require(final_bytes == export_bytes, "Retry receipts changed the export")
    report = report_base(client, "resume")
    report.update(profile_version=exported["profile"]["schema_version"], dialogue_turns=final["turn_count"],
                  render_calls=len(final["calls"]))
    report["model_calls_total"] = sum(call.get("provider_calls", 0) for call in final["calls"])
    report["checks"] = {"existing_actor_resume": True, "reading_retry_receipt_restored": True,
                        "dialogue_retry_receipt_restored": True, "no_extra_render_calls": True,
                        "full_context_frozen": True, "cpython_replay_of_wasm_export": True,
                        "export_matches_original_baseline": True if expected_export is not None else None}
    report["scope"] = "Checks the supplied existing actor; record deployment version changes separately."
    updated = {**state, "schema_version": PRIVATE_SCHEMA, "target_origin": client.origin,
               "cookie": client.cookie, "analysis_request": analysis_request,
               "analysis_response_sha256": digest_json(reading), "last_turn_request": last_turn_request,
               "last_turn_response_sha256": digest_json(reply), "export_sha256": digest_bytes(export_bytes),
               "context_sha256": digest_json(final["context"]), "profile_hash": exported["profile"]["profile_hash"],
               "turn_count": final["turn_count"], "render_calls": len(final["calls"])}
    return report, export_bytes, updated


def write_private(path, state):
    path = path.resolve()
    require(not path.is_relative_to(ROOT), "Store private capabilities outside the repository")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(state, output, ensure_ascii=False, indent=2, allow_nan=False)
        output.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", help="Hosted HTTPS origin, or loopback HTTP origin")
    parser.add_argument("--live", action="store_true", help="Explicitly allow four paid DeepSeek calls for a fresh run; resume only reuses receipts")
    parser.add_argument("--output", type=Path, help="Write the capability-free JSON report")
    parser.add_argument("--export", type=Path, help="Optionally save the verified fixture replay export")
    parser.add_argument("--private-state", type=Path, help="Explicitly create an owner-only capability file outside the repository")
    parser.add_argument("--resume", type=Path, help="Resume an existing actor without repeating three dialogue turns")
    parser.add_argument("--baseline-export", type=Path, help="Original export baseline, useful with the old temporary cookie format")
    parser.add_argument("--retries", type=int, default=2, help="Additional network-only attempts, default 2 (maximum 4)")
    parser.add_argument("--timeout", type=float, default=30, help="Per-attempt socket timeout in seconds, default 30")
    args = parser.parse_args(argv)
    require(0 <= args.retries <= 4 and 0 < args.timeout <= 60, "Retry or timeout setting is outside its bounds")
    state = load_private(args.resume) if args.resume else None
    origin = args.url or (state or {}).get("target_origin")
    require(origin, "Specify --url for a fresh run or a legacy private-state file")
    origin = origin_url(origin)
    if state and state.get("target_origin"):
        require(origin == origin_url(state["target_origin"]), "Never send a private actor cookie to a different origin")
    paths = [value.resolve() for value in (args.output, args.export, args.private_state, args.resume) if value]
    require(len(paths) == len(set(paths)), "Output, export and private-state paths must be different")
    if args.private_state:
        require(not args.private_state.exists(), "Private-state destination already exists; choose a new path")
        require(not args.private_state.resolve().is_relative_to(ROOT), "Store private capabilities outside the repository")
    client = Client(origin, retries=args.retries, timeout=args.timeout, live=args.live)
    report, exported, private = (resume_workflow(client, state, args.baseline_export) if state
                                 else fresh_workflow(client))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    if args.export:
        args.export.parent.mkdir(parents=True, exist_ok=True)
        args.export.write_bytes(exported)
    if args.private_state:
        write_private(args.private_state, private)
    # The private state is never returned to stdout or merged into the report.
    print(json.dumps({**{name: value for name, value in report.items() if name != "calls"},
                      "http_requests": len(report["calls"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (VerificationError, OSError, KeyError, TypeError, ValueError) as error:
        # Avoid a traceback or raw transport/SDK error string containing a URL,
        # cookie, response body or private-state document.
        message = str(error) if isinstance(error, VerificationError) else type(error).__name__
        print("Verification failed: " + message, file=sys.stderr)
        raise SystemExit(1)

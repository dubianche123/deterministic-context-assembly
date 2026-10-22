"""Bounded JSON persistence for an isolated Cloudflare conversation actor.

The local runtime deliberately remains process-local.  This adapter is the
cloud boundary: epoch timestamps survive actor eviction, while locks and live
dispatches never become persistent state.  Callers must serialize requests to
one actor and save only after the request has finished.
"""

from copy import deepcopy
import math
import re
import time

from .events import canonical_json
from .runtime import OperationStore, _Operation
from .service import NornService, Session, SessionStore


ACTOR_SCHEMA_VERSION = "norn.actor.v1"
LIVE_ACTOR_SCHEMA_VERSION = "norn.actor.v2"
MAX_STATE_BYTES = 16 * 1024 * 1024
MAX_OPERATIONS = 40
MAX_LIVE_MODEL_CALLS = 12
MOCK_GENERATION_ROUTE = {"provider": "mock"}
DEEPSEEK_GENERATION_ROUTE = {"provider": "openai-compatible",
                           "base_url": "https://api.deepseek.com/v1",
                           "model": "deepseek-flash", "thinking": "disabled"}
_TTL = 1800
_SESSION_FIELDS = {"id", "analysis", "reading", "max_turns", "expires_at",
                   "replay_bundle", "history", "context", "prompt", "calls", "turns"}
_OPERATION_FIELDS = {"scope", "key", "fingerprint", "expires_at", "result", "error", "done"}
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _object(value, fields, label):
    if type(value) is not dict or set(value) != fields:
        raise ValueError(f"{label} has an invalid object schema")


def _integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} is outside its integer bounds")


def _timestamp(value):
    if (type(value) not in (int, float) or not math.isfinite(value)
            or not 0 <= value <= 253402300799):
        raise ValueError("Actor expiry must be a finite epoch timestamp")


def _identifier(value):
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise ValueError("Actor session ID is invalid")


def _json_snapshot(value):
    """Validate without coercion before making a detached, canonical JSON copy."""
    pending = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > 1_000_000 or depth > 64:
            raise ValueError("Actor state exceeds JSON structural bounds")
        kind = type(item)
        if item is None or kind is bool:
            continue
        if kind is int:
            if abs(item) > 9_007_199_254_740_991:
                raise ValueError("Actor JSON integer exceeds exact portable bounds")
        elif kind is float:
            if not math.isfinite(item):
                raise ValueError("Actor JSON numbers must be finite")
        elif kind is str:
            if len(item) > MAX_STATE_BYTES:
                raise ValueError("Actor JSON string exceeds bounds")
        elif kind is list:
            if len(item) > 100_000:
                raise ValueError("Actor JSON array exceeds bounds")
            pending.extend((child, depth + 1) for child in item)
        elif kind is dict:
            if len(item) > 100_000 or any(type(key) is not str for key in item):
                raise ValueError("Actor JSON object exceeds bounds or has non-string keys")
            pending.extend((child, depth + 1) for child in item.values())
        else:
            raise ValueError("Actor state accepts only plain JSON values")
    try:
        encoded = canonical_json(value).encode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Actor state is not valid canonical UTF-8 JSON") from exc
    if len(encoded) > MAX_STATE_BYTES:
        raise ValueError("Actor state exceeds 16 MiB")
    return deepcopy(value)


def _messages(messages, label, maximum=16):
    if type(messages) is not list or len(messages) > maximum:
        raise ValueError(f"{label} exceeds message bounds")
    for index, message in enumerate(messages):
        _object(message, {"role", "content"}, label)
        if (message["role"] != ("user" if index % 2 == 0 else "assistant")
                or type(message["content"]) is not str
                or not message["content"].strip() or len(message["content"]) > 100_000):
            raise ValueError(f"{label} must contain bounded alternating native messages")


def _generation_route(route):
    if (type(route) is not dict or
            route not in (MOCK_GENERATION_ROUTE, DEEPSEEK_GENERATION_ROUTE)):
        raise ValueError("Actor generation route is not an approved non-secret route")


def _session(row, *, live=False):
    fields = _SESSION_FIELDS | {"generation_route"} if live else _SESSION_FIELDS
    # A post-reading rating is optional; snapshots written before it existed
    # remain valid unchanged.
    optional = {"post_session", "record_id"} & set(row) if type(row) is dict else set()
    _object(row, fields | optional, "Actor session")
    if "record_id" in row and (type(row["record_id"]) is not str
                               or re.fullmatch(r"[0-9a-f]{32}", row["record_id"]) is None):
        raise ValueError("Actor collection record ID is invalid")
    if live:
        _generation_route(row["generation_route"])
    _identifier(row["id"])
    _timestamp(row["expires_at"])
    _integer(row["max_turns"], 1, 3, "Actor maximum turns")
    _integer(row["turns"], 0, row["max_turns"], "Actor turns")
    if type(row["reading"]) is not str or len(row["reading"]) > 100_000:
        raise ValueError("Actor reading must be a bounded string")
    for field in ("analysis", "context", "prompt"):
        if type(row[field]) is not dict:
            raise ValueError(f"Actor {field} must be an object")
    if row["replay_bundle"] is not None and type(row["replay_bundle"]) is not dict:
        raise ValueError("Actor replay bundle must be an object or null")
    if type(row["calls"]) is not list or len(row["calls"]) > 32:
        raise ValueError("Actor calls must be a bounded array")
    if any(type(call) is not dict for call in row["calls"]):
        raise ValueError("Actor calls must contain objects")
    _messages(row["history"], "Actor history")
    _object(row["context"], {"schema_version", "prompt_version", "profile_hash", "mode", "block",
                             "canonical_block", "context_hash"}, "Actor frozen context")
    if row["context"]["mode"] not in ("sparse_mode", "dense_mode", "overflow_mode"):
        raise ValueError("Actor frozen context mode is invalid")
    # This invokes only canonical frozen-context validation, never generation or
    # profile training.  It rejects poisoned native histories before restoring.
    from .context import build_analysis_prompt
    initial = build_analysis_prompt(row["context"])
    messages = row["history"]
    prefix = initial["messages"]
    if (messages[:len(prefix)] != prefix or len(messages) != len(prefix) + 1 + 2 * row["turns"]
            or messages[len(prefix)]["content"] != row["reading"]):
        raise ValueError("Actor history differs from its authoritative turn count or reading")
    _object(row["prompt"], set(initial), "Actor prompt")
    _messages(row["prompt"]["messages"], "Actor prompt messages")
    if (row["prompt"]["system"] != initial["system"]
            or row["prompt"]["cache"] != initial["cache"]
            or row["prompt"]["messages"][:len(prefix)] != prefix):
        raise ValueError("Actor prompt differs from its frozen context")
    _integer(row["prompt"]["max_tokens"], 1, 10_000, "Actor prompt token bound")
    temperature = row["prompt"]["temperature"]
    if type(temperature) not in (int, float) or not 0 <= temperature <= 2:
        raise ValueError("Actor prompt temperature is invalid")
    bundle = row["replay_bundle"]
    if bundle is not None:
        fields = {"schema_version", "journal", "profile"}
        research = bundle.get("schema_version") == "norn.research-replay.v1"
        if research and "contribution_ledger" in bundle:
            fields |= {"contribution_ledger"}
        _object(bundle, fields, "Actor replay bundle")
        profile = bundle["profile"]
        if (type(profile) is not dict or row["analysis"] != profile.get("analysis")
                or row["context"]["profile_hash"] != profile.get("profile_hash")):
            raise ValueError("Actor session differs from its frozen replay profile")
        if research and "contribution_ledger" in bundle:
            # Reconstruct only score terms, never refit the sealed model during
            # actor restoration. An independent hash cannot legitimize forged
            # contributions, so compare with their actual effective weights.
            from .core import engine_module
            from .research_inspection import contribution_ledger
            try:
                deck_name = profile["deck"].split("@", 1)[0]
                deck = engine_module("research_deck").load_deck(deck_name)
                if profile["deck"] != deck.label:
                    raise ValueError("Actor contribution ledger belongs to another deck version")
                expected = contribution_ledger(profile, deck)
                if canonical_json(bundle["contribution_ledger"]) != canonical_json(expected):
                    raise ValueError("Actor contribution ledger differs from its effective scores")
            except (KeyError, TypeError, IndexError) as exc:
                raise ValueError("Actor contribution ledger is invalid") from exc
    if "post_session" in row:
        from .events import validate_post_session
        if bundle is None:
            raise ValueError("Actor post-session record requires a replay profile")
        validate_post_session(row["post_session"], profile_hash=bundle["profile"]["profile_hash"],
                              selected_card_ids=[item["card_id"] for item in
                                                 row["analysis"]["attribution"]["selections"]])


def _operation(row):
    _object(row, _OPERATION_FIELDS, "Actor operation")
    scope = row["scope"]
    if type(scope) is not str:
        raise ValueError("Actor operation scope must be a string")
    if scope not in ("analysis", "finale", "finale-choice"):
        if not scope.startswith("dialogue:"):
            raise ValueError("Actor operation scope is invalid")
        _identifier(scope[len("dialogue:"):])
    key = row["key"]
    if (type(key) is not str or not 1 <= len(key) <= 128
            or any(ord(char) < 33 or ord(char) > 126 for char in key)):
        raise ValueError("Actor operation ID is invalid")
    if type(row["fingerprint"]) is not str or not _HASH.fullmatch(row["fingerprint"]):
        raise ValueError("Actor operation fingerprint is invalid")
    _timestamp(row["expires_at"])
    if type(row["done"]) is not bool:
        raise ValueError("Actor operation completion must be boolean")
    result, error = row["result"], row["error"]
    if result is not None and type(result) is not dict:
        raise ValueError("Actor operation result must be an object or null")
    if error is not None:
        if (type(error) is not list or len(error) != 2 or type(error[0]) is not str
                or len(error[0]) > 4096):
            raise ValueError("Actor operation error must contain a bounded message and status")
        _integer(error[1], 400, 599, "Actor error status")
    if (not row["done"] and (result is not None or error is not None)
            or row["done"] and ((result is None) == (error is None))):
        raise ValueError("Actor operation result does not match its completion state")


def _validated_state(state):
    state = _json_snapshot(state)
    _object(state, {"schema_version", "sessions", "operations", "model_calls_reserved"}, "Actor state")
    if state["schema_version"] not in (ACTOR_SCHEMA_VERSION, LIVE_ACTOR_SCHEMA_VERSION):
        raise ValueError("Unsupported actor state schema")
    live = state["schema_version"] == LIVE_ACTOR_SCHEMA_VERSION
    _integer(state["model_calls_reserved"], 0, MAX_LIVE_MODEL_CALLS if live else 1,
             "Actor model call counter")
    for field, limit in (("sessions", 1), ("operations", MAX_OPERATIONS)):
        if type(state[field]) is not list or len(state[field]) > limit:
            raise ValueError(f"Actor {field} exceeds its bound")
    for session in state["sessions"]:
        _session(session, live=live)
    seen = set()
    for operation in state["operations"]:
        _operation(operation)
        identity = (operation["scope"], operation["key"])
        if identity in seen:
            raise ValueError("Actor contains duplicate operation IDs")
        seen.add(identity)
    return state


def new_actor_service(state=None, *, clock=time.time, provider="mock", config=None, deck=None):
    """Restore one browser's service, keeping routing and absolute expiries.

    The real transport receives its credential separately from this adapter.
    Only the fixed DeepSeek route is accepted; an API key is never retained in
    the service config or its durable snapshot.
    """
    restored = _validated_state(state) if state is not None else None
    if provider not in ("mock", "openai-compatible"):
        raise ValueError("Actor provider must use an approved generation route")
    live = provider != "mock" or bool(restored and restored["schema_version"] == LIVE_ACTOR_SCHEMA_VERSION)
    if provider != "mock":
        if config is not None and type(config) is not dict:
            raise ValueError("Actor provider config must be an object")
        values = dict(config or {})
        if set(values) - {"base_url", "model", "api_key", "thinking"}:
            raise ValueError("Actor provider config contains unsupported fields")
        route = {"provider": provider,
                 **{key: values.get(key, value) for key, value in DEEPSEEK_GENERATION_ROUTE.items()
                    if key != "provider"}}
        _generation_route(route)
        config = {"base_url": route["base_url"], "model": route["model"], "api_key": "",
                  "cache_enabled": False, "reasoning_effort": "off"}
    now = clock()
    _timestamp(now)
    sessions = SessionStore(ttl=_TTL, max_sessions=1, clock=clock)
    service = NornService(provider=provider, config=config, sessions=sessions,
                          max_model_calls=MAX_LIVE_MODEL_CALLS if live else 1, max_concurrency=1, deck=deck)
    service._actor_schema_version = LIVE_ACTOR_SCHEMA_VERSION if live else ACTOR_SCHEMA_VERSION
    service.operations = OperationStore(ttl=_TTL, capacity=MAX_OPERATIONS, clock=clock)
    if restored is None:
        return service
    for row in restored["sessions"]:
        if row["expires_at"] > now:
            values = {key: value for key, value in row.items() if key != "id"}
            values.setdefault("generation_route", deepcopy(MOCK_GENERATION_ROUTE))
            sessions._sessions[row["id"]] = Session(**values)
    for row in restored["operations"]:
        if row["expires_at"] > now:
            entry = _Operation(fingerprint=row["fingerprint"], expires_at=row["expires_at"],
                               result=row["result"], error=tuple(row["error"]) if row["error"] else None,
                               done=row["done"])
            service.operations._entries[(row["scope"], row["key"])] = entry
    service.limits.model_calls = restored["model_calls_reserved"]
    return service


def dump_actor_state(service):
    """Return a detached JSON snapshot, pruning expired data without refreshing TTL."""
    if (not isinstance(service, NornService) or service.provider not in ("mock", "openai-compatible")
            or service.sessions.max_sessions != 1 or service.operations.capacity != MAX_OPERATIONS
            or service.limits.max_model_calls not in (1, MAX_LIVE_MODEL_CALLS)
            or service.limits.max_concurrency != 1):
        raise ValueError("Only isolated actor services can be persisted")
    live = (service.provider != "mock"
            or getattr(service, "_actor_schema_version", ACTOR_SCHEMA_VERSION) == LIVE_ACTOR_SCHEMA_VERSION)
    if live and service.limits.max_model_calls != MAX_LIVE_MODEL_CALLS:
        raise ValueError("Live actor service has an invalid model call limit")
    if not live and service.limits.max_model_calls != 1:
        raise ValueError("Mock actor service has an invalid model call limit")
    if service.provider != "mock":
        _generation_route({"provider": service.provider,
                           "base_url": service.config.get("base_url"),
                           "model": service.config.get("model"), "thinking": "disabled"})
    with service.limits._lock:
        if service.limits.active:
            raise ValueError("Cannot persist an actor while generation is active")
        model_calls = service.limits.model_calls
    with service.sessions._lock, service.operations._lock:
        now = service.sessions.clock()
        _timestamp(now)
        if any(entry.users for entry in service.operations._entries.values()):
            raise ValueError("Cannot persist an actor while an operation is active")
        if any(session.lock.locked() for session in service.sessions._sessions.values()):
            raise ValueError("Cannot persist an actor while a conversation is active")
        service.sessions._expire(now)
        for identity in [identity for identity, entry in service.operations._entries.items()
                         if entry.expires_at <= now]:
            del service.operations._entries[identity]
        fields = _SESSION_FIELDS | {"generation_route"} if live else _SESSION_FIELDS
        if not live and any(session.generation_route not in ({}, MOCK_GENERATION_ROUTE)
                            for session in service.sessions._sessions.values()):
            raise ValueError("Mock actor session contains an unexpected generation route")
        sessions = [{"id": key, **{field: getattr(session, field) for field in fields - {"id"}},
                     **({"post_session": session.post_session} if session.post_session is not None else {}),
                     **({"record_id": session.record_id} if session.record_id is not None else {})}
                    for key, session in service.sessions._sessions.items()]
        operations = [{"scope": scope, "key": key, "fingerprint": entry.fingerprint,
                       "expires_at": entry.expires_at, "result": entry.result,
                       "error": list(entry.error) if entry.error is not None else None, "done": entry.done}
                      for (scope, key), entry in service.operations._entries.items()]
        state = {"schema_version": LIVE_ACTOR_SCHEMA_VERSION if live else ACTOR_SCHEMA_VERSION, "sessions": sessions,
                 "operations": operations, "model_calls_reserved": model_calls}
        return _validated_state(state)


def next_expiry(state):
    """Return the earliest persisted epoch expiry for a storage-cleanup alarm."""
    state = _validated_state(state)
    expiries = [row["expires_at"] for field in ("sessions", "operations") for row in state[field]]
    return min(expiries) if expiries else None

"""A server-sealed, held-out final favourite; kept outside learning events."""

from copy import deepcopy
import math
import random
import re
import secrets

from .core import engine_module
from .events import canonical_json, content_hash
from .pamela_comparison import prediction

CHALLENGE_VERSION = "norn.finale.challenge.v1"
RESULT_VERSION = "norn.finale.result.v1"
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def commitment_for(private):
    return content_hash({key: private[key] for key in (
        "challenge_id", "candidate_ids", "predicted_card_id", "probabilities",
        "baseline_probabilities", "events_hash", "profile_hash", "salt", "layer")})


def create(journal, deck, challenge_id):
    from .research_inspection import build_bundle
    from .pacing import trace, MAX_ROUNDS
    if type(challenge_id) is not str or not _ID.fullmatch(challenge_id):
        raise ValueError("A finale needs a bounded challenge ID")
    bundle = build_bundle(journal, deck)
    canonical, profile = bundle["journal"], bundle["profile"]
    rounds = profile["analysis"]["total_rounds"]
    if not 1 <= rounds <= MAX_ROUNDS:
        raise ValueError("A finale follows 1–12 completed exploration rounds")
    seen = {card for event in canonical["events"] if event["type"] == "round_started"
            for card in event["card_ids"]}
    available = [card for card in deck.ids if card not in seen]
    if len(available) < 4:
        raise ValueError("Four genuinely unseen finale pictures are required")
    # Uniform held-out candidates: neither the answer nor fitted utility picks
    # the examination set. Salt also randomizes honest top-score ties.
    salt = secrets.token_hex(24)
    rng = random.Random(salt)
    candidates = rng.sample(available, 4)
    effective = profile["analysis"]["preference"]["effective"]
    learned = prediction(effective, candidates, deck, scored=False)
    baseline = prediction([1.0] + [0.0] * 8, candidates, deck, scored=False)
    high = max(learned["probabilities"])
    top = [card for card, p in zip(candidates, learned["probabilities"]) if abs(p - high) <= 1e-12]
    private = {"schema_version": CHALLENGE_VERSION, "challenge_id": challenge_id,
               "candidate_ids": candidates, "predicted_card_id": rng.choice(top),
               "probabilities": learned["probabilities"], "baseline_probabilities": baseline["probabilities"],
               "events_hash": profile["events_hash"], "profile_hash": profile["profile_hash"],
               "salt": salt, "layer": profile["analysis"]["preference"]["status"],
               "journal": canonical, "pacing": trace(canonical, deck),
               "jev": {"status": "off", "provider_calls": 0}}
    private["commitment"] = commitment_for(private)
    return private


def public_challenge(private):
    return {key: deepcopy(private[key]) for key in (
        "schema_version", "challenge_id", "candidate_ids", "commitment", "events_hash", "pacing")}


def observation(value, elapsed_ms):
    if value is None:
        return None
    if type(value) is not dict or set(value) != {"ready_offset_ms", "hidden_ms", "layout", "input"}:
        raise ValueError("Invalid finale observation")
    ready, hidden, layout = value["ready_offset_ms"], value["hidden_ms"], value["layout"]
    if (type(ready) is not int or not 0 <= ready <= elapsed_ms or type(hidden) is not int
            or not 0 <= hidden <= elapsed_ms - ready or value["input"] not in ("mouse", "touch", "keyboard", "unknown")):
        raise ValueError("Finale readiness and hidden time must fit its elapsed window")
    if layout is not None and (type(layout) is not list or len(layout) != 4
            or any(type(slot) is not list or len(slot) != 2 or any(type(i) is not int or not 0 <= i <= 3 for i in slot) for slot in layout)
            or len({tuple(slot) for slot in layout}) != 4):
        raise ValueError("Finale layout must describe four distinct coarse slots")
    return deepcopy(value)


def finish(private, card_id, elapsed_ms, observed=None):
    if card_id not in private["candidate_ids"]:
        raise ValueError("Choose one of the four sealed finale pictures")
    if type(elapsed_ms) is not int or not 0 <= elapsed_ms <= 86_400_000:
        raise ValueError("Finale elapsed time must be bounded milliseconds")
    result = {key: deepcopy(private[key]) for key in (
        "challenge_id", "candidate_ids", "predicted_card_id", "probabilities", "baseline_probabilities",
        "events_hash", "profile_hash", "salt", "layer", "commitment", "pacing", "jev")}
    result.update(schema_version=RESULT_VERSION, chosen_card_id=card_id,
                  hit=card_id == private["predicted_card_id"], elapsed_ms=elapsed_ms)
    if observed is not None:
        result["observation"] = observation(observed, elapsed_ms)
    if result["jev"].get("status") == "ok":
        result["jev"]["hit"] = result["jev"]["predicted_card_id"] == card_id
    return result


def jev_journal(private, deck):
    """Ephemeral prediction projection; never returned as a learning journal."""
    source = private["journal"]
    events = deepcopy(source["events"][:-1])  # remove exploration session_completed
    n = sum(e["type"] == "round_completed" for e in events) + 1
    events.append({"event_id": "finale-" + content_hash(private["challenge_id"])[7:31], "sequence": len(events) + 1,
                   "type": "round_started", "round": n, "card_ids": list(private["candidate_ids"]),
                   "display": {"policy": next(e["display"]["policy"] for e in events if e["type"] == "round_started"),
                               "deck": deck.label, "roles": ["random"] * 4}})
    return {"schema_version": source["schema_version"], "events": events}


def validate_result(value, journal, deck=None):
    required = {"schema_version", "challenge_id", "candidate_ids", "predicted_card_id", "chosen_card_id", "hit",
                "commitment", "salt", "events_hash", "profile_hash", "layer", "probabilities",
                "baseline_probabilities", "elapsed_ms", "pacing", "jev"}
    if type(value) is not dict or not required <= set(value) or set(value) - required - {"observation"} or value["schema_version"] != RESULT_VERSION:
        raise ValueError("Unsupported finale result")
    research = engine_module("research_deck")
    deck = deck or research.load_deck(research.deck_of(journal))
    from .research_inspection import validated
    from .pacing import validate_trace
    replay = validated(journal, deck)
    if value["events_hash"] != replay["events_hash"]:
        raise ValueError("Finale belongs to another frozen exploration")
    candidates = value["candidate_ids"]
    seen = {c for e in replay["journal"]["events"] if e["type"] == "round_started" for c in e["card_ids"]}
    if (type(candidates) is not list or len(candidates) != 4
            or any(type(c) is not str or c not in deck.cards or c in seen for c in candidates)
            or len(set(candidates)) != 4
            or value["predicted_card_id"] not in candidates or value["chosen_card_id"] not in candidates
            or type(value["hit"]) is not bool or value["hit"] != (value["predicted_card_id"] == value["chosen_card_id"])
            or type(value["challenge_id"]) is not str or not _ID.fullmatch(value["challenge_id"])
            or type(value["salt"]) is not str or not re.fullmatch(r"[0-9a-f]{48}", value["salt"])
            or type(value["profile_hash"]) is not str or not re.fullmatch(r"sha256:[0-9a-f]{64}", value["profile_hash"])
            or value["layer"] not in ("population", "sensitivity", "personal")
            or type(value["elapsed_ms"]) is not int or not 0 <= value["elapsed_ms"] <= 86_400_000):
        raise ValueError("Invalid held-out finale choice")
    for key in ("probabilities", "baseline_probabilities"):
        ps = value[key]
        if (type(ps) is not list or len(ps) != 4 or any(type(p) not in (float, int) or not math.isfinite(p)
                or not 0 <= p <= 1 for p in ps) or abs(sum(ps) - 1) > 1e-8):
            raise ValueError("Finale needs normalized probabilities over its four pictures")
    if value["commitment"] != commitment_for(value):
        raise ValueError("Finale differs from its sealed prediction")
    # A self-consistent hash alone is not a replay: reconstruct the fitted
    # profile and both score lanes from the frozen, pre-choice exploration.
    numeric = engine_module("numeric")
    with numeric.use(numeric.NATIVE_CONTRACT):
        profile = research.build_profile(replay["journal"], deck, replay["events_hash"])
    preference = profile["analysis"]["preference"]
    expected = prediction(preference["effective"], candidates, deck, scored=False)["probabilities"]
    baseline = prediction([1.0] + [0.0] * 8, candidates, deck, scored=False)["probabilities"]
    if (value["profile_hash"] != profile["profile_hash"] or value["layer"] != preference["status"]
            or canonical_json(value["probabilities"]) != canonical_json(expected)
            or canonical_json(value["baseline_probabilities"]) != canonical_json(baseline)):
        raise ValueError("Finale scores do not rebuild from the frozen exploration")
    high = max(expected)
    if expected[candidates.index(value["predicted_card_id"])] < high - 1e-12:
        raise ValueError("The sealed guess must be a highest-scoring picture")
    validate_trace(value["pacing"], journal)
    if "observation" in value:
        observation(value["observation"], value["elapsed_ms"])
    j = value["jev"]
    if (type(j) is not dict or j.get("status") not in ("off", "pending", "failed", "ok")
            or type(j.get("provider_calls")) is not int or not 0 <= j["provider_calls"] <= 1
            or set(j) - {"status", "provider_calls", "model", "predicted_card_id", "probabilities", "hit"}
            or ("model" in j and (type(j["model"]) is not str or not 1 <= len(j["model"]) <= 128))
            or (j.get("status") != "ok" and {"predicted_card_id", "probabilities", "hit"} & set(j))):
        raise ValueError("Invalid finale opponent status")
    if j["status"] == "ok":
        if (j.get("predicted_card_id") not in candidates or type(j.get("hit")) is not bool
                or j["hit"] != (j["predicted_card_id"] == value["chosen_card_id"])
                or type(j.get("probabilities")) is not list or len(j["probabilities"]) != 4
                or any(type(p) not in (float, int) or not math.isfinite(p) or not 0 <= p <= 1 for p in j["probabilities"])
                or abs(sum(j["probabilities"]) - 1) > 1e-8):
            raise ValueError("Invalid sealed Jev finale prediction")
    canonical_json(value)
    return deepcopy(value)

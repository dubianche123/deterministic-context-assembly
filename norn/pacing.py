"""Auditable exploratory pacing, not a claim that a fixed sample size suffices.

The first policy is deliberately an exploration policy, not a trained stopping
model. Logged offer propensities and a held-out finale make subsequent policy
evaluation possible. A suggestion never forces a person to stop or to continue.
"""

from copy import deepcopy
import hashlib

from .core import engine_module
from .events import canonical_json, content_hash

PACING_VERSION = "norn.pacing.v1"
MAX_ROUNDS = 12  # Product/resource ceiling, not an evidence threshold.


def trace(journal, deck=None):
    from .research_inspection import validated
    research = engine_module("research_deck")
    deck = deck or research.load_deck(research.deck_of(journal))
    replay = validated(journal, deck, require_complete=False)
    canonical = replay["journal"]
    if sum(e["type"] == "round_started" for e in canonical["events"]) > MAX_ROUNDS:
        raise ValueError("This product's resource limit is 12 exploration rounds")
    seed = content_hash({"schema_version": canonical["schema_version"],
                         "first": canonical["events"][:1]})
    numeric = engine_module("numeric")
    decisions, selected, active_ms = [], 0, 0
    completed = [e for e in canonical["events"] if e["type"] == "round_completed"]
    with numeric.use(numeric.NATIVE_CONTRACT):
        state = engine_module("research_model").Session(deck.x2, deck.context)
        for row, event in zip(research.observations(canonical), completed):
            state.add(row)
            number = row["round"]
            selected += len(row["picks"])
            active_ms += max(0, event["duration_ms"] - event.get("hidden_ms", 0))
            layer = state.layer()
            probability, reason = 0.0, "initial_exploration"
            if number >= MAX_ROUNDS:
                probability, reason = 1.0, "product_limit"
            elif number >= 3 and layer != "population":
                probability, reason = 1.0, "personal_predictive_evidence"
            elif number >= 3:
                probability = .25 if number <= 5 else .5 if number <= 8 else .75
                reason = "randomized_offer"
                if active_ms >= 180_000:
                    probability, reason = .8, "time_cost"
            draw = int(hashlib.sha256(f"{seed}:{number}".encode()).hexdigest()[:13], 16) / 16 ** 13
            action = "limit" if number >= MAX_ROUNDS else "offer_finale" if draw < probability else "continue"
            decisions.append({"round": number, "action": action, "reason": reason,
                              "offer_probability": probability, "draw": draw,
                              "layer": layer, "selection_count": selected,
                              "active_ms": active_ms,
                              "evidence": {"E1": state.e1, "E2": state.e2}})
    latest = decisions[-1] if decisions else {"round": 0, "action": "continue", "reason": "initial_exploration"}
    return {"schema_version": PACING_VERSION, "seed": seed,
            "decisions": decisions, "latest": deepcopy(latest)}


def validate_trace(value, journal):
    if type(value) is not dict or set(value) != {"schema_version", "seed", "decisions", "latest"}:
        raise ValueError("Unsupported pacing trace")
    expected = trace(journal)
    if canonical_json(value) != canonical_json(expected):
        raise ValueError("Pacing trace differs from its completed exploration history")
    return deepcopy(value)


def trace_extends(earlier, later):
    if earlier is None or later is None:
        return earlier is None and later is None
    return (earlier["schema_version"] == later["schema_version"] and earlier["seed"] == later["seed"]
            and earlier["decisions"] == later["decisions"][:len(earlier["decisions"])])

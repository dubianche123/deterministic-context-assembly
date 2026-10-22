"""Frozen local predictors for the offline fast-thinker comparison.

The heuristic is a *new probability adapter* around the historical fast
thinker's coordinates.  The historical scorer never supplied choice
probabilities.  Its coordinates are used without fitting or calibration:
u(card) = sum((legacy_coord / 8) * (card_coord / 8)), temperature = 1,
and u(stop) = 0.  Timing and round weighting see the training prefix only.

The ML predictor uses the shipped sequential MNL's Laplace center (its
Newton posterior mode), with exactly the utilities used by its log loss.
All predictors are prepared once; predicting never updates their state.
"""

from copy import deepcopy
import hashlib
import json
import math
import time
from types import MappingProxyType

from .core import engine_module


STOP = "__STOP__"
PROBABILITY_TOLERANCE = 1e-6
__all__ = ["STOP", "LocalPredictor", "prepare_local", "prepare_policy", "validate_probabilities"]


def _candidate_ids(candidates):
    if type(candidates) not in (list, tuple):
        raise ValueError("Candidates must be a list or tuple of distinct card IDs")
    if any(type(card) is not str or not card or card == STOP for card in candidates):
        raise ValueError("Candidates must contain card IDs, without the stop sentinel")
    if len(set(candidates)) != len(candidates):
        raise ValueError("Candidate card IDs must be distinct")
    return tuple(candidates)


def validate_probabilities(distribution, candidates):
    """Reject malformed probabilities; return a validated finite copy.

    Every candidate and STOP must occur exactly once.  Values must be real
    JSON numbers (booleans are rejected), in [0, 1], with sum 1 to 1e-6,
    matching both remote adapters' wire-probability tolerance.
    The validator never repairs, clips, or renormalizes a predictor's output.
    """
    candidates = _candidate_ids(candidates)
    if type(distribution) is not dict:
        raise ValueError("A probability distribution must be a dictionary")
    expected = set(candidates) | {STOP}
    if set(distribution) != expected:
        raise ValueError("Probability keys must be exactly the candidate IDs plus STOP")
    values = list(distribution.values())
    if any(type(value) not in (int, float) or not 0 <= value <= 1
           or not math.isfinite(value) for value in values):
        raise ValueError("Probabilities must be finite numbers between zero and one")
    if not math.isclose(math.fsum(values), 1.0, rel_tol=0.0, abs_tol=PROBABILITY_TOLERANCE):
        raise ValueError("Probabilities must sum to one")
    return {card: float(distribution[card]) for card in (*candidates, STOP)}


class LocalPredictor:
    """An immutable utility table and a fixed stop utility, with provenance."""

    def __init__(self, utilities, stop_utility, metadata, prepare_ms):
        self._utilities = MappingProxyType(dict(utilities))
        self._stop_utility = float(stop_utility)
        self.metadata = deepcopy(metadata)
        self.prepare_ms = float(prepare_ms)

    def predict(self, candidate_ids):
        candidates = _candidate_ids(candidate_ids)
        if any(card not in self._utilities for card in candidates):
            raise ValueError("Unknown card in prediction candidates")
        if not candidates:
            return {STOP: 1.0}
        # The same stable softmax and exp contract as choice_model._log_loss.
        numeric = engine_module("numeric")
        options = [self._utilities[card] for card in candidates] + [self._stop_utility]
        top = max(options)
        masses = [numeric.exp(value - top) for value in options]
        total = math.fsum(masses)
        distribution = dict(zip((*candidates, STOP), (mass / total for mass in masses)))
        return validate_probabilities(distribution, candidates)


def _hash(value):
    canonical = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _choice_basis(rows):
    return [{key: row[key] for key in ("round", "shown", "picks", "stopped")} for row in rows]


def prepare_local(prefix_journal, train_rows):
    """Prepare heuristic, ML, and stop-only on the same frozen prefix.

    ``train_rows`` are choice_model.observations of ``prefix_journal``.  Both
    inputs are copied, and their choice evidence must agree.  Unknown timing
    is omitted from the legacy call rather than fabricated.  No held-out
    events, duration, round count, or labels are accepted by predict().
    """
    model = engine_module("choice_model")
    legacy = engine_module("fast_thinker")
    catalog = model.load_catalog()
    journal, rows = deepcopy(prefix_journal), deepcopy(train_rows)
    if type(rows) not in (list, tuple):
        raise ValueError("Training rows must be a list or tuple")
    prefix_rows = model.observations(journal, catalog)
    if _choice_basis(rows) != _choice_basis(prefix_rows):
        raise ValueError("Training choices must match the completed journal prefix")
    prepared = model.Prepared(rows, catalog)
    cards = legacy._load_cards()
    catalog_identity = {"feature_version": catalog.feature_version,
                        "card_count": len(catalog.ids),
                        "schema_version": model.CATALOG_SCHEMA_VERSION}
    common = {"catalog": catalog_identity, "feature_version": catalog.feature_version,
              "prefix_hash": _hash(journal),
              "training_hash": _hash(_choice_basis(rows)),
              "training_round_count": len(rows),
              "training_choice_count": sum(len(row["picks"]) + bool(row["stopped"])
                                           for row in rows if row["shown"]),
              "frozen_prefix": True}

    started = time.perf_counter()
    selections = [{"id": card, "round": row["round"]} for row in prefix_rows for card in row["picks"]]
    durations = [{"round": row["round"], "duration_ms": row["duration_ms"]}
                 for row in prefix_rows if type(row.get("duration_ms")) in (int, float)
                 and math.isfinite(row["duration_ms"])]
    result = legacy.fast_think(selections, total_rounds=len(prefix_rows), round_durations=durations)
    axes = tuple(result["coords_avg"][axis] / 8.0 for axis in model.AXES)
    heuristic_utilities = {card: sum(axis * value for axis, value in zip(axes, features[:4]))
                           for card, features in zip(catalog.ids, catalog.features)}
    heuristic_metadata = {**common, "version": "norn.fast-thinkers.legacy-axis-softmax.v1",
                          "training_hash": _hash({"choices": _choice_basis(prefix_rows),
                                                  "durations": durations, "total_rounds": len(prefix_rows)}),
                          "source": "norn.engine.fast_thinker.fast_think",
                          "source_version": legacy.ALGORITHM_VERSION,
                          "source_metadata_hash": _hash(cards),
                          "features": list(model.FEATURE_NAMES[:4]),
                          "formula": "u(card)=sum((coords_avg[axis]/8)*(card.coords[axis]/8)); softmax(u, stop=0)",
                          "temperature": 1.0, "stop_utility": 0.0,
                          "fitted": False, "prior_fallback": not selections,
                          "known_duration_count": len(durations),
                          "semantics": "New fixed probability adapter around historical coordinates; not historical choice probabilities or confidence."}
    heuristic = LocalPredictor(heuristic_utilities, 0.0, heuristic_metadata,
                               (time.perf_counter() - started) * 1000)

    started = time.perf_counter()
    theta, _, iterations = model.fit(prepared)
    utilities = {card: sum(weight * value for weight, value in zip(theta[:model.FEATURES], features))
                 for card, features in zip(catalog.ids, catalog.features)}
    ml_metadata = {**common, "version": "norn.fast-thinkers.seq-mnl.v1",
                   "source": "norn.engine.choice_model.fit", "source_version": model.MODEL_VERSION,
                   "features": list(model.FEATURE_NAMES),
                   "formula": "u(card)=sum(theta[k]*catalog.features[card][k]); softmax(u, stop=theta[12])",
                   "posterior_center": "Newton posterior mode (Laplace mean)",
                   "temperature": 1.0, "stop_utility": theta[model.FEATURES],
                   "fitted": True, "prior_fallback": not any(row["shown"] for row in rows),
                   "iterations": iterations,
                   "semantics": "Sequential choice probabilities at the fitted posterior center; not personality probabilities."}
    ml = LocalPredictor(utilities, theta[model.FEATURES], ml_metadata,
                        (time.perf_counter() - started) * 1000)

    started = time.perf_counter()
    stop_bias = model._fit_stop_only(prepared)
    stop_metadata = {**common, "version": "norn.fast-thinkers.stop-only.v1",
                     "source": "norn.engine.choice_model._fit_stop_only", "source_version": model.MODEL_VERSION,
                     "features": [], "formula": "u(card)=0; softmax(u, stop=fitted_stop_bias)",
                     "temperature": 1.0, "stop_utility": stop_bias,
                     "fitted": True, "prior_fallback": not any(row["shown"] for row in rows),
                     "semantics": "Stop-rate baseline with all card weights fixed at zero."}
    stop_only = LocalPredictor(dict.fromkeys(catalog.ids, 0.0), stop_bias, stop_metadata,
                               (time.perf_counter() - started) * 1000)
    return {"heuristic": heuristic, "ml": ml, "stop_only": stop_only}


def prepare_policy(prefix_journal, train_rows, *, state=None):
    """Prepare the versioned shrinkage / no-signal policy, retaining raw ML.

    The completed prefix is the only accepted training evidence. A caller
    evaluating several prefixes may supply an already-computed policy state;
    its training identity must match this exact choice prefix and catalog.
    Prediction uses the state's effective parameters, never its unguarded
    candidate posterior, and cannot fit or update anything.
    """
    started = time.perf_counter()
    model, policy = engine_module("choice_model"), engine_module("choice_policy")
    catalog = model.load_catalog()
    journal, rows = deepcopy(prefix_journal), deepcopy(train_rows)
    if type(rows) not in (list, tuple):
        raise ValueError("Training rows must be a list or tuple")
    if _choice_basis(rows) != _choice_basis(model.observations(journal, catalog)):
        raise ValueError("Training choices must match the completed journal prefix")
    prepared = deepcopy(state) if state is not None else policy.fit_policy(rows, catalog)
    if prepared.get("training_hash") != policy.training_hash(rows, catalog):
        raise ValueError("Policy state must match the completed training prefix")
    theta, guard = prepared.get("effective_theta"), prepared.get("guard")
    if (type(theta) is not list or len(theta) != model.PARAMETERS
            or any(type(value) not in (int, float) or not math.isfinite(value) for value in theta)
            or type(guard) is not dict):
        raise ValueError("Invalid policy prediction state")
    utilities = {card: sum(weight * value for weight, value in zip(theta[:model.FEATURES], features))
                 for card, features in zip(catalog.ids, catalog.features)}
    metadata = {
        "version": "norn.fast-thinkers.guarded-mnl.v1",
        "source": "norn.engine.choice_policy.fit_policy", "source_version": policy.MODEL_VERSION,
        "catalog": {"feature_version": catalog.feature_version, "card_count": len(catalog.ids),
                    "schema_version": model.CATALOG_SCHEMA_VERSION},
        "feature_version": catalog.feature_version, "prefix_hash": _hash(journal),
        "training_hash": prepared["training_hash"], "training_round_count": len(rows),
        "training_choice_count": sum(len(row["picks"]) + bool(row["stopped"])
                                     for row in rows if row["shown"]),
        "frozen_prefix": True, "fitted": True,
        "prior_fallback": guard.get("status") == "baseline", "guard": deepcopy(guard),
        "effective_theta": list(theta), "features": list(model.FEATURE_NAMES),
        "formula": "u(card)=sum(effective_theta[k]*catalog.features[card][k]); softmax(u, stop=effective_theta[12])",
        "temperature": 1.0, "stop_utility": theta[model.FEATURES],
        "semantics": "Training-prefix-only shrinkage and content-evidence gate; baseline means equal card utilities with a fitted stop habit. Not personality confidence.",
    }
    return LocalPredictor(utilities, theta[model.FEATURES], metadata,
                          (time.perf_counter() - started) * 1000)

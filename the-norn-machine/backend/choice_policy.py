"""Versioned conservative prediction policy; historical choice_model is frozen.

The candidate is a stronger-prior MAP. Its training-only prequential content
evidence decides whether effective predictions may use those card weights.
The candidate posterior is never relabeled as a posterior of the hard gate.
"""

from copy import deepcopy
import math

from . import choice_model as base
from . import numeric

MODEL_VERSION = "norn.choice.guarded-mnl.v1"
EVIDENCE_THRESHOLD = 20.0
SEMANTICS = ("Training-only prequential content gate around a small-sample regularized sequential choice model; "
             "fallback keeps all card utilities zero and fits only STOP. Not personality accuracy.")


def training_hash(rows, catalog=None):
    catalog = base.load_catalog(catalog)
    return base._hash({"rows": [{k: row[k] for k in ("round", "shown", "picks", "stopped")} for row in rows],
                       "feature_version": catalog.feature_version, "policy_version": MODEL_VERSION})


def weight_precision(completed_informative_rounds):
    return base.WEIGHT_PRECISION * (1.0 + base.FEATURES / max(1, completed_informative_rounds))


def _fit(prepared, count, precision, start=None):
    """Same Newton objective with an explicit local prior, no global mutation."""
    theta = [0.0] * base.PARAMETERS if start is None else list(start)

    def evaluate(values):
        gradient, hessian = base._evaluate(values, prepared, count)
        for k in range(base.FEATURES):
            difference = precision - base.PRIOR[k]
            gradient[k] -= difference * values[k]
            hessian[k][k] += difference
        return gradient, hessian

    for iteration in range(1, base.MAX_ITERATIONS + 1):
        gradient, hessian = evaluate(theta)
        step = base._solve(hessian, gradient)
        theta = [value + delta for value, delta in zip(theta, step)]
        if max(map(abs, step)) < base.TOLERANCE:
            break
    else:
        raise ValueError("Conservative choice candidate did not converge")
    if not all(math.isfinite(value) for value in theta):
        raise ValueError("Conservative choice candidate diverged")
    _, hessian = evaluate(theta)
    return theta, base._inverse(hessian), iteration


def _content_gain(theta, row, catalog):
    """Predict a completed round with the *preceding* frozen candidate."""
    remaining, gains, informative = list(row["shown"]), [], 0
    for target in row["picks"]:
        utilities = {card: sum(w * x for w, x in zip(theta[:base.FEATURES],
                                                    catalog.features[catalog.index[card]]))
                     for card in remaining}
        if len(remaining) > 1:
            top = max(utilities.values())
            gains.append(numeric.log(len(remaining)) + utilities[target] - top -
                         numeric.log(math.fsum(numeric.exp(value - top) for value in utilities.values())))
            informative += 1
        remaining.remove(target)
    return math.fsum(gains), informative


def _state(rows, catalog, prepared, count, theta, covariance, iterations,
           log_evalue, evidence_choices, evidence_rounds, informative_rounds):
    active = log_evalue >= numeric.log(EVIDENCE_THRESHOLD)
    stop = base._fit_stop_only(base.Prepared(rows, catalog))
    effective = list(theta) if active else [0.0] * base.FEATURES + [stop]
    return {"round": count, "theta": list(theta), "covariance": deepcopy(covariance),
            "iterations": iterations, "effective_theta": effective,
            "training_hash": training_hash(rows, catalog),
            "guard": {"policy_version": MODEL_VERSION, "status": "active" if active else "baseline",
                      "reason": "prequential_content_evidence" if active else (
                          "no_completed_exposure" if not informative_rounds else "insufficient_content_evidence"),
                      "log_evalue": log_evalue, "log_threshold": numeric.log(EVIDENCE_THRESHOLD),
                      "evalue_threshold": EVIDENCE_THRESHOLD, "evidence_card_choices": evidence_choices,
                      "evidence_rounds": evidence_rounds, "training_rounds": informative_rounds,
                      "weight_precision": weight_precision(informative_rounds),
                      "stop_precision": base.STOP_PRECISION,
                      "uncertainty": "Candidate Laplace covariance; not a posterior of the gated predictor."}}


def path(rows, catalog=None):
    """All completed-prefix states; open rows never enter evidence or fits."""
    catalog = base.load_catalog(catalog)
    completed = [row for row in rows if row["stopped"]]
    prepared = base.Prepared(completed, catalog)
    theta, covariance, iterations = _fit(prepared, 0, weight_precision(0))
    states = [_state([], catalog, prepared, 0, theta, covariance, iterations, 0.0, 0, 0, 0)]
    gains, evidence_choices, evidence_rounds, informative = [], 0, 0, 0
    for count, row in enumerate(completed, 1):
        gain, choices = _content_gain(theta, row, catalog)
        gains.append(gain)
        evidence_choices += choices
        evidence_rounds += bool(choices)
        informative += bool(row["shown"])
        theta, covariance, iterations = _fit(prepared, count, weight_precision(informative), theta)
        state = _state(completed[:count], catalog, prepared, count, theta, covariance, iterations,
                       math.fsum(gains), evidence_choices, evidence_rounds, informative)
        state["row"] = deepcopy(row)
        states.append(state)
    return states


def fit_policy(rows, catalog=None):
    """Return a detached candidate/effective state on exactly the supplied facts.

    An optional final open round may update the provisional candidate and STOP
    estimate; it cannot add content evidence or count as a completed round.
    """
    catalog = base.load_catalog(catalog)
    state = path(rows, catalog)[-1]
    if any(not row["stopped"] for row in rows):
        if any(not row["stopped"] for row in rows[:-1]):
            raise ValueError("Only the final choice round may be open")
        prepared = base.Prepared(rows, catalog)
        theta, covariance, iterations = _fit(prepared, len(rows), state["guard"]["weight_precision"], state["theta"])
        active = state["guard"]["status"] == "active"
        stop = base._fit_stop_only(prepared)
        state.update(theta=theta, covariance=covariance, iterations=iterations,
                     effective_theta=list(theta) if active else [0.0] * base.FEATURES + [stop],
                     training_hash=training_hash(rows, catalog))
    return deepcopy(state)


def validate(rows, catalog=None):
    """Same outer chronological split, with the *whole policy* trained inside."""
    catalog = base.load_catalog(catalog)
    completed = [row for row in rows if row["stopped"]]
    result = {"status": "insufficient", "reason": "fewer_than_five_completed_rounds",
              "method": "chronological_holdout_20_percent_guarded_choice_log_loss",
              "denominator": "Engine sequence steps, including zero-loss forced STOP; compare diagnostics exclude forced STOP.",
              "policy_version": MODEL_VERSION, "log_loss": None,
              "random_baseline": {"log_loss": None, "description": "uniform over remaining cards and stop"},
              "stop_only_baseline": {"log_loss": None, "stop_bias": None,
                                     "description": "stop bias fitted on training only; card weights zero"},
              "beats_random": False, "beats_stop_only": False, "beats_baselines": False,
              "train_observation_count": 0, "holdout_observation_count": 0, "holdout_choice_count": 0,
              "claim": "Held-out prediction of this policy; no human or personality accuracy claim."}
    if len(completed) < 5:
        return result
    cutoff = len(completed) - max(1, math.ceil(len(completed) * 0.2))
    state = fit_policy(completed[:cutoff], catalog)
    heldout = base.Prepared(completed[cutoff:], catalog)
    loss, uniform, count = base._log_loss(state["effective_theta"], heldout)
    stop = base._fit_stop_only(base.Prepared(completed[:cutoff], catalog))
    stop_loss, _, _ = base._log_loss([0.0] * base.FEATURES + [stop], heldout)
    enough = count >= 3
    beats_random, beats_stop = bool(enough and loss < uniform), bool(enough and loss < stop_loss)
    result.update(status="measured" if enough else "insufficient", reason=None if enough else "too_few_holdout_choices",
                  log_loss=loss, beats_random=beats_random, beats_stop_only=beats_stop,
                  beats_baselines=beats_random and beats_stop, train_observation_count=cutoff,
                  holdout_observation_count=len(completed) - cutoff, holdout_choice_count=count,
                  training_guard=state["guard"])
    result["random_baseline"]["log_loss"] = uniform
    result["stop_only_baseline"].update(log_loss=stop_loss, stop_bias=stop)
    return result


def summarize(rows, catalog=None, *, selected=(), include_open=False):
    catalog = base.load_catalog(catalog)
    state = fit_policy(rows, catalog)
    theta, covariance = state["theta"], state["covariance"]
    effective, active = state["effective_theta"], state["guard"]["status"] == "active"
    informative = [row for row in rows if row["shown"]]
    picks = sum(len(row["picks"]) for row in informative)
    stops = sum(bool(row["stopped"]) for row in informative)
    scores = dict(zip(catalog.ids, base.card_scores(effective, catalog)))
    evidence = sorted(({"card_id": card, "score": scores[card]} for card in selected if card in scores),
                      key=lambda row: (-row["score"], row["card_id"]))[:8]
    recommended = base.top_cards(list(scores.values()), catalog) if active else []
    roles = {card: [role for role, ids in (("recommended", recommended),
                                         ("selected", [row["card_id"] for row in evidence])) if card in ids]
             for card in dict.fromkeys([row["card_id"] for row in evidence] + recommended)}
    breakdown = base.contributions(effective, covariance, roles, catalog)
    breakdown["uncertainty_semantics"] = state["guard"]["uncertainty"]
    features = [{"name": component.get("name", f"pixel.pc{k + 1}"), "label": base._component_label(component),
                 "weight": effective[4 + k]} for k, component in enumerate(catalog.components[:8])] if active else []
    features.sort(key=lambda row: (-abs(row["weight"]), row["name"]))
    types = base.type_probabilities(theta[:4], [r[:4] for r in covariance[:4]]) if active else [
        {"type": kind, "p": 1 / 16} for kind in sorted(set(catalog.types))]
    return {"status": "trained" if active else "baseline" if informative else "no_observations",
            "trained": bool(informative),
            "model_version": MODEL_VERSION, "feature_version": catalog.feature_version,
            "observation_count": len(informative), "round_count": len(rows), "choice_count": picks + stops,
            "pick_count": picks, "stop_count": stops, "feature_count": base.FEATURES, "parameter_count": base.PARAMETERS,
            "include_open": include_open, "provisional": any(not r["stopped"] for r in rows),
            "training_hash": state["training_hash"], "guard": state["guard"],
            "prior": {"weight_precision": state["guard"]["weight_precision"], "stop_precision": base.STOP_PRECISION},
            "posterior": {"mean": theta, "sd": [numeric.sqrt(covariance[i][i]) for i in range(base.PARAMETERS)],
                          "iterations": state["iterations"], "semantics": "Regularized candidate, before the content gate."},
            "effective": {"theta": effective, "active": active, "uncertainty": state["guard"]["uncertainty"]},
            "axes": {**base._axes(effective, covariance),
                     "semantics": "Effective scoring point; sd/covariance describe the ungated candidate, not a posterior centered here."},
            "stop_bias": effective[base.FEATURES],
            "type_probabilities": types, "top_features": features,
            "type_probability_semantics": ("Candidate axis-sign posterior; not personality probability." if active else
                                           "Uniform display placeholder; no type posterior is asserted while fallback is active."),
            "recommendations": [{"card_id": card, "score": scores[card], "score_kind": "guarded_choice_utility"}
                                for card in recommended],
            "selected_evidence": evidence, "contributions": breakdown, "validation": validate(rows, catalog),
            "score_semantics": "Effective guarded utility; baseline means equal card utilities, not certainty of no taste.",
            "semantics": SEMANTICS}


def trajectory(rows, catalog=None):
    catalog = base.load_catalog(catalog)
    states, previous = [], [0.0] * 4
    for raw in path(rows, catalog):
        theta, covariance = raw["effective_theta"], raw["covariance"]
        active = raw["guard"]["status"] == "active"
        scores = base.card_scores(theta, catalog)
        state = {"round": raw["round"], "mean": [base._r(v, 4) for v in theta[:4]],
                 "sd": [base._r(numeric.sqrt(covariance[i][i]), 4) for i in range(4)],
                 "cov_sn_tf": [[base._r(covariance[a][b], 5) for b in (1, 2)] for a in (1, 2)],
                 "scores": [base._r(v, 3) for v in scores], "top": base.top_cards(scores, catalog) if active else [],
                 "types": [{"type": r["type"], "p": base._r(r["p"], 3)} for r in
                           (base.type_probabilities(theta[:4], [r[:4] for r in covariance[:4]]) if active else
                            [{"type": kind, "p": 1 / 16} for kind in sorted(set(catalog.types))])],
                 "stop_bias": base._r(theta[base.FEATURES], 3), "guard": deepcopy(raw["guard"]),
                 "uncertainty_semantics": "Effective point with candidate covariance; suppress posterior ellipse while on baseline."}
        if raw.get("row") is not None:
            row = raw["row"]
            state.update(shown=list(row["shown"]), picks=list(row["picks"]),
                         delta=[base._r(now - before, 4) for now, before in zip(theta[:4], previous)],
                         duration_ms=row.get("duration_ms"))
            if row.get("unseen"):
                state["unseen"] = list(row["unseen"])
        listed = list(dict.fromkeys(state["top"] + state.get("picks", [])))
        state["contributions"] = base._state_contributions(theta, covariance, listed, catalog)
        state["contributions"]["uncertainty_semantics"] = raw["guard"]["uncertainty"]
        previous = theta[:4]
        states.append(state)
    return {"schema_version": base.TRAJECTORY_SCHEMA_VERSION, "model_version": MODEL_VERSION,
            "feature_version": catalog.feature_version, "axes": list(base.AXES),
            "letters": [list(pair) for pair in base.LETTERS], "states": states, "rounds": len(states) - 1,
            "prior": {"weight_precision_formula": "1.5*(1+12/max(1,n_completed_exposed_rounds))", "stop_precision": .5},
            "components": [{"name": c.get("name"), "label": base._component_label(c),
                            "explained_variance_ratio": c.get("explained_variance_ratio")} for c in catalog.components],
            "cards": [{"id": card, "type": catalog.types[i], "cell": catalog.cells[card]} for i, card in enumerate(catalog.ids)],
            "feature_names": list(base.FEATURE_NAMES), "semantics": SEMANTICS}

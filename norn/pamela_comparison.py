"""Read-only, card-conditional prediction comparisons for the PAMELA deck.

The displayed comparison is prequential: round r uses the model state before
round r. Its options are the completed round's recorded comparison set. That
set may use cumulative visibility measured over the whole round, so this is a
retrospective conditional comparison, not a prospective exposure-policy test.
Neither this module nor the trajectory calls a remote decision service.
"""

from copy import deepcopy
import math

from .core import engine_module
from .events import content_hash

SCHEMA_VERSION = "norn.pamela-comparison.v1"
SCOPE = "card_conditional_first_finally_kept"
PROBABILITY_FLOOR = 1e-12


def options_hash(candidate_ids, deck):
    """Bind the ordered eligible set, representation version and prediction task."""
    return content_hash({"deck": deck.label, "candidate_ids": list(candidate_ids), "scope": SCOPE})


def prediction(weights, candidate_ids, deck, target=None, *, scored=True):
    """Rank the same cards, conditional on keeping a card; no STOP probability.

    Equal utilities have the same competition rank. The stable ID tie break
    in ``order`` is presentation only; top-1 credit splits probability ties.
    """
    candidates = list(candidate_ids)
    scores = [sum(weight * value for weight, value in zip(weights, deck.x2[card]))
              for card in candidates]
    if scores:
        largest = max(scores)
        masses = [math.exp(score - largest) for score in scores]
        total = sum(masses)
        probabilities = [mass / total for mass in masses]
        ranks = [1 + sum(other > score for other in scores) for score in scores]
        ordered = sorted(range(len(candidates)), key=lambda i: (-scores[i], candidates[i]))
    else:
        probabilities, ranks, ordered = [], [], []
    eligible = scored and target in candidates and len(candidates) >= 2
    target_position = candidates.index(target) if eligible else None
    if eligible:
        highest = max(scores)
        ties = sum(score == highest for score in scores)
        probability = probabilities[target_position]
        loss = -math.log(max(probability, PROBABILITY_FLOOR))
        credit = 1.0 / ties if scores[target_position] == highest else 0.0
    else:
        probability = loss = credit = None
    return {"status": "ok", "scores": scores, "probabilities": probabilities,
            "ranks": ranks, "order": [candidates[i] for i in ordered],
            "target_nll": loss, "top1_credit": credit,
            "zero_probability_target": probability == 0 if eligible else False}


def round_metadata(journal):
    """Derive recorded count/layout facts without adding or altering events.

    ``ever_seen_count`` is cumulative over a round, never the first screen.
    Readiness loading and layout are snapshots, not a complete change history.
    Missing measurements stay null rather than becoming measured zeroes.
    """
    result, current, completed = {}, None, 0
    events = journal["events"]
    for position, event in enumerate(events):
        kind = event["type"]
        if kind == "round_started":
            current = {"planned_count": len(event["card_ids"]), "ready_loaded_count": None,
                       "ever_seen_count": None, "ready_columns": None,
                       "eligible_count": None, "eligibility_source": "planned_fallback",
                       "prefix_hash": content_hash({"schema_version": journal["schema_version"],
                                                     "events": events[:position]}),
                       "cutoff_rounds": completed,
                       "planned_ids": list(event["card_ids"])}
            result[event["round"]] = current
        elif kind == "round_ready" and current is not None:
            current["ready_loaded_count"] = current["planned_count"] - len(event["unloaded_card_ids"])
            layout = event.get("layout")
            current["ready_columns"] = len({slot[1] for slot in layout}) if layout is not None else None
        elif kind == "round_completed" and current is not None:
            seen = event.get("seen_card_ids")
            current["ever_seen_count"] = len(seen) if seen is not None else None
            current["eligibility_source"] = "cumulative_seen" if seen is not None else "planned_fallback"
            completed += 1
    return result


def add_cumulative(rounds):
    """Attach descriptive running means; skipped rounds do not inflate n."""
    totals, n = {"baseline": 0.0, "learning": 0.0}, 0
    for row in rounds:
        if row["scored"]:
            n += 1
            for name in totals:
                totals[name] += row["lanes"][name]["target_nll"]
        row["cumulative"] = {"n": n, **{f"{name}_nll": value / n if n else None
                                       for name, value in totals.items()}}
    return {"n": n, **{f"{name}_nll": value / n if n else None for name, value in totals.items()}}


def attach_to_trajectory(path, journal, deck):
    """Reuse a single trajectory's fitted prefix states, without any extra fit."""
    rows = engine_module("research_deck").observations(journal)
    if len(path["states"]) != len(rows) + 1:
        raise ValueError("A comparison needs one state before and after every completed round")
    metadata = round_metadata(journal)
    cases = []
    for position, row in enumerate(rows):
        before = path["states"][position]
        candidates, picks = list(row["shown"]), list(row["picks"])
        target = picks[0] if picks else None
        scored = target is not None and len(candidates) >= 2
        skipped = "no_pick" if target is None else "no_alternative" if not scored else None
        facts = metadata[row["round"]]
        display = {key: facts[key] for key in ("planned_count", "ready_loaded_count", "ever_seen_count",
                                              "ready_columns", "eligibility_source")}
        display["eligible_count"] = len(candidates)
        baseline = prediction([1.0] + [0.0] * 8, candidates, deck, target, scored=scored)
        learned = prediction(before["weights"], candidates, deck, target, scored=scored)
        case = {"round": row["round"], "cutoff_rounds": facts["cutoff_rounds"],
                "prefix_hash": facts["prefix_hash"], "options_hash": options_hash(candidates, deck),
                "candidate_ids": candidates, "picks": picks, "target": target,
                "scored": scored, "skipped_reason": skipped, "layer_before": before["layer"],
                "same_predictions": baseline["probabilities"] == learned["probabilities"],
                "display": display,
                "lanes": {"baseline": baseline, "learning": learned, "jev": {"status": "not_run"}}}
        cases.append(case)
    summary = add_cumulative(cases)
    comparison = {"schema_version": SCHEMA_VERSION, "mode": "prequential", "scope": SCOPE,
                  "events_hash": path["events_hash"], "deck": deck.label, "feature_version": deck.version,
                  "lanes": {"baseline": {"status": "ok", "algorithm": "population_utility"},
                            "learning": {"status": "ok", "algorithm": path["algorithm_version"]},
                            "jev": {"status": "not_run", "reason": "offline_opt_in_only"}},
                  "rounds": cases, "summary": summary, "provider_calls": 0,
                  "probability_floor": PROBABILITY_FLOOR,
                  "semantics": "Round r uses only fitted choices before round r. Probabilities are conditional "
                               "on keeping one card, without STOP; the scored target is the first finally-kept "
                               "card in first-click order. Empty choices and fewer than two eligible cards "
                               "are unscored. Eligibility may use whole-round cumulative visibility, not "
                               "first-screen visibility. NLLs are descriptive, not population accuracy or "
                               "a causal test of display policy. Jev is not called by this view."}
    path["comparison"] = comparison
    path["states"][0]["comparison"] = None
    displayed = set()
    card_positions = {card["id"]: i for i, card in enumerate(path["cards"])}
    for index, state in enumerate(path["states"]):
        if index:
            state["comparison"] = cases[index - 1]
            displayed.update(metadata[rows[index - 1]["round"]]["planned_ids"])
        state["unseen_top"] = sorted((card for card in deck.ids if card not in displayed),
                                     key=lambda card: (-state["scores"][card_positions[card]], card))[:6]
    return path


def prepare_comparison(journal, deck):
    """Build a detached local comparison for an explicit supplied journal."""
    from .research_inspection import trajectory, validated
    replay = validated(journal, deck, require_complete=False)
    return deepcopy(trajectory(replay, deck)["comparison"])

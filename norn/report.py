"""Measurement report over consented records: the numbers to decide and show with.

Everything is recomputed from the raw journals with the explicit v4 raw choice
reference. The v5 guarded policy is evaluated separately by records evaluate;
this historical reference must not silently change. Intervals are 95% visitor-
level bootstrap intervals: all records of one browser are resampled together,
because repeat visits of one person are not independent evidence.

Sections:
- volume and engagement: who came back, how long they played, follow-up turns;
- outcome: the voluntary 1-5 rating and "closest card" answers;
- validity: within-session held-out choices, whether the visitor's closest card
  is the model's top pick, and, for returning browsers, whether one visit's
  model predicts the next visit better than both a stranger's picture weights
  under the visitor's own stop habit and that stop habit alone (the test the
  anonymous visitor link exists for);
- groups: the same outcome numbers split by any record field, e.g. an
  experiment arm, the prompt version or the generating model.
"""

from collections import defaultdict
import json
import math
import random
import statistics

from .collection import PARTIAL_SCHEMA_VERSION
from .core import engine_module

REPORT_SCHEMA_VERSION = "norn.report.v1"
POPULATION_FOLDS = 5


def read_records(path):
    """Records from a `norn records export` / archive export NDJSON file."""
    with open(path, encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _lookup(record, path):
    value = record
    for part in path.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return "(none)" if value is None else str(value)


def _stop_only_theta(model, catalog, rows):
    """The profile's own stop-only baseline (choice_model): stop bias fitted on
    `rows`, every card weight 0. A card model must beat this, not just uniform,
    before it says anything about which pictures someone prefers."""
    return [0.0] * model.FEATURES + [model._fit_stop_only(model.Prepared(rows, catalog))]


def _with_own_stop(model, catalog, theta, rows):
    """`theta`'s card weights with the stop bias refitted on `rows` (posterior
    mode under the profile's stop prior). A stranger's model carries the
    stranger's stop habit; giving it the visitor's own leaves only the picture
    weights to compare. With zero weights this is `_stop_only_theta`."""
    exp, log = math.exp, math.log
    weights, bias = theta[:model.FEATURES], 0.0
    steps = []  # (log-sum-exp of the cards still on offer, stopped) per choice
    for cards, _, choices, _ in model.Prepared(rows, catalog).rows:
        utilities = [math.fsum(w * x for w, x in zip(weights, card)) for card in cards]
        alive = list(range(len(cards)))
        for choice in choices:
            if alive:  # with every card picked, stopping was certain and says nothing
                top = max(utilities[i] for i in alive)
                steps.append((top + log(math.fsum(exp(utilities[i] - top) for i in alive)), choice is None))
            if choice is not None:
                alive.remove(choice)
    # The log posterior is concave in the bias and its gradient is bounded by
    # the number of choices, so the mode lies in this bracket; Newton steps that
    # leave it fall back to bisection.
    low = -(len(steps) / model.STOP_PRECISION + 1.0)
    high = -low
    for _ in range(4 * model.MAX_ITERATIONS):
        gradient, curvature = -model.STOP_PRECISION * bias, model.STOP_PRECISION
        for cards, stopped in steps:
            gap = cards - bias
            p = 1.0 / (1.0 + exp(gap)) if gap < 700 else 0.0
            gradient += stopped - p
            curvature += p * (1.0 - p)
        low, high = (bias, high) if gradient > 0 else (low, bias)
        step = gradient / curvature
        if not low < bias + step < high:
            step = (low + high) / 2 - bias
        bias += step
        if abs(step) < model.TOLERANCE:
            return list(weights) + [bias]
    raise ValueError("Stop bias did not converge")


def _derive(record, model, catalog):
    # Exactly the profile's rows (norn.profile): rounds whose cards never reached
    # the screen add no evidence to the fit but stay in, so validate() draws the
    # same chronological split as the visitor's own profile.
    rows = model.observations(record["journal"], catalog)
    informative = [row for row in rows if row["shown"]]
    theta, _, _ = model.fit(model.Prepared(rows, catalog))
    holdout = model.validate(rows, catalog)
    picks = list(dict.fromkeys(card for row in rows for card in row["picks"]))
    notes = record.get("annotations") or {}
    post = notes.get("post_session") or {}
    closest = None
    if post.get("closest_card_id") in picks and len(picks) > 1:
        scores = dict(zip(catalog.ids, model.card_scores(theta, catalog)))
        order = sorted(picks, key=lambda card: (-scores[card], card))
        position = order.index(post["closest_card_id"])
        closest = {"hit": position == 0, "chance": 1 / len(order), "percentile": 1 - position / (len(order) - 1)}
    return {
        "visitor": record.get("visitor_id") or record.get("record_id"),
        "visit": record.get("visit") or 1,
        "rows": rows, "axes": theta[:4],
        "type": "".join(pair[0 if value >= 0 else 1] for pair, value in zip(model.LETTERS, theta[:4])),
        "rounds": len(informative), "selections": sum(len(row["picks"]) for row in rows),
        "revisions": sum(1 for event in record["journal"]["events"] if event.get("type") == "card_deselected"),
        "holdout_gain": (holdout["random_baseline"]["log_loss"] - holdout["log_loss"]
                         if holdout["status"] == "measured" else None),
        # Same split and baseline as the profile's validation, so the numbers cannot drift apart.
        "holdout_gain_vs_stop": (holdout["stop_only_baseline"]["log_loss"] - holdout["log_loss"]
                                 if holdout["status"] == "measured" else None),
        "choices": sum(len(row["picks"]) + bool(row["stopped"]) for row in informative),
        "rating": post.get("reading_rating"), "closest": closest,
        "turns": (notes.get("engagement") or {}).get("dialogue_turns", 0),
        "day": record.get("created_day"),
    }


def _mean(values):
    values = [value for value in values if value is not None]
    return math.fsum(values) / len(values) if values else None


def _interval(units, statistic, rng, draws):
    """Point estimate and percentile interval, resampling whole visitors."""
    flat = [item for unit in units for item in unit]
    point = statistic(flat)
    if point is None or len(units) < 2:
        return {"value": point, "ci95": None, "visitors": len(units)}
    samples = []
    for _ in range(draws):
        value = statistic([item for _ in units for item in units[rng.randrange(len(units))]])
        if value is not None:
            samples.append(value)
    samples.sort()
    return {"value": point, "ci95": [samples[int(0.025 * len(samples))], samples[int(0.975 * len(samples)) - 1]],
            "visitors": len(units)}


def _by_visitor(items):
    groups = defaultdict(list)
    for item in items:
        groups[item["visitor"]].append(item)
    return [sorted(group, key=lambda item: item["visit"]) for _, group in sorted(groups.items())]


def _summary_stats(items, rng, draws):
    units = _by_visitor(items)
    rated = [item["rating"] for item in items if item["rating"] is not None]
    return {
        "records": len(items), "visitors": len(units),
        "rating_response_rate": len(rated) / len(items) if items else None,
        "rating_mean": _interval(units, lambda flat: _mean(item["rating"] for item in flat), rng, draws),
        "dialogue_turns_mean": _interval(units, lambda flat: _mean(item["turns"] for item in flat), rng, draws),
        "holdout_gain_nats": _interval(units, lambda flat: _mean(item["holdout_gain"] for item in flat), rng, draws),
        # The bar that matters: card preferences beyond the visitor's own stop tendency.
        "holdout_gain_vs_stop_nats": _interval(units, lambda flat: _mean(item["holdout_gain_vs_stop"] for item in flat),
                                               rng, draws),
    }


def _difference(control, variant, key, rng, draws):
    """Variant minus control of one per-record mean, bootstrapping each arm's visitors."""
    left, right = _by_visitor(control), _by_visitor(variant)

    def mean(units):
        return _mean(item[key] for unit in units for item in unit)
    point = None if mean(left) is None or mean(right) is None else mean(right) - mean(left)
    if point is None or len(left) < 2 or len(right) < 2:
        return {"value": point, "ci95": None}
    samples = []
    for _ in range(draws):
        a = mean([left[rng.randrange(len(left))] for _ in left])
        b = mean([right[rng.randrange(len(right))] for _ in right])
        if a is not None and b is not None:
            samples.append(b - a)
    samples.sort()
    return {"value": point, "ci95": [samples[int(0.025 * len(samples))], samples[int(0.975 * len(samples)) - 1]]}


def _choice_loss(model, catalog, theta, rows):
    loss, baseline, count = model._log_loss(theta, model.Prepared(rows, catalog))
    return (loss * count, baseline * count, count) if count else (0.0, 0.0, 0)


def _cross_visit(items, model, catalog, rng, draws):
    """Does a browser's earlier visit predict its next one? Stability and prediction."""
    units = [unit for unit in _by_visitor(items) if len(unit) >= 2]
    pairs = [(unit[k], unit[k + 1]) for unit in units for k in range(len(unit) - 1)]
    result = {"returning_visitors": len(units), "visit_pairs": len(pairs)}
    if len(units) < 2:
        result["status"] = "insufficient_returning_visitors"
        return result
    # Population model per visitor fold, trained only on other visitors' rounds.
    visitors = sorted({item["visitor"] for item in items})
    fold_of = {visitor: index % POPULATION_FOLDS for index, visitor in enumerate(visitors)}
    population = {}
    for fold in range(POPULATION_FOLDS):
        rows = [row for item in items if fold_of[item["visitor"]] != fold for row in item["rows"]]
        population[fold] = model.fit(model.Prepared(rows, catalog))[0] if rows else None
    # The matched baseline: another returning browser's earlier visit with the
    # closest number of choices, so it overfits as much as the visitor's own model.
    fits = {id(earlier): model.fit(model.Prepared(earlier["rows"], catalog))[0] for earlier, _ in pairs}
    scored = []
    for earlier, later in pairs:
        personal = fits[id(earlier)]
        others = [other for other, _ in pairs if other["visitor"] != later["visitor"]]
        gap = min(abs(other["choices"] - earlier["choices"]) for other in others)
        closest = [other for other in others if abs(other["choices"] - earlier["choices"]) == gap]
        match = closest[rng.randrange(len(closest))]
        stranger = _with_own_stop(model, catalog, fits[id(match)], earlier["rows"])
        own, uniform, count = _choice_loss(model, catalog, personal, later["rows"])
        crowd = population[fold_of[later["visitor"]]]
        if not count or crowd is None:
            continue
        crowd_loss = _choice_loss(model, catalog, crowd, later["rows"])[0]
        stranger_loss = _choice_loss(model, catalog, stranger, later["rows"])[0]
        # Own stop habit only: do the person's picture weights add anything at all?
        stop_only = _choice_loss(model, catalog, _stop_only_theta(model, catalog, earlier["rows"]), later["rows"])[0]
        scored.append({"visitor": later["visitor"], "count": count, "personal": own, "population": crowd_loss,
                       "stranger": stranger_loss, "stop_only": stop_only, "uniform": uniform,
                       "evidence_gap": abs(match["choices"] - earlier["choices"]),
                       "same_type": earlier["type"] == later["type"],
                       "sign_agreement": _mean(float((a >= 0) == (b >= 0))
                                               for a, b in zip(earlier["axes"], later["axes"]))})
    scored_units = _by_visitor([{**row, "visit": index} for index, row in enumerate(scored)])

    def per_choice(name, minus=None):
        def statistic(flat):
            total = math.fsum(row["count"] for row in flat)
            if not total:
                return None
            value = math.fsum(row[name] for row in flat) / total
            return value - math.fsum(row[minus] for row in flat) / total if minus else value
        return statistic
    # Chance level for stability: an earlier visit paired with another browser's later visit.
    strangers = [(a, b) for a, _ in pairs for _, b in pairs if a["visitor"] != b["visitor"]]
    if len(strangers) > 20000:
        strangers = rng.sample(strangers, 20000)
    versus_stranger = _interval(scored_units, per_choice("personal", "stranger"), rng, draws)
    versus_stop = _interval(scored_units, per_choice("personal", "stop_only"), rng, draws)

    def below_zero(cell):
        return cell["ci95"] is not None and cell["ci95"][1] < 0
    result.update({
        "status": "measured",
        "log_loss_per_choice": {name: _interval(scored_units, per_choice(name), rng, draws)
                                for name in ("personal", "stranger", "stop_only", "population", "uniform")},
        # Below zero: the visitor's own past picture weights predict better than
        # a stranger's, both under the visitor's own stop habit.
        "personal_minus_stranger": versus_stranger,
        "stranger_evidence_gap_median": statistics.median(row["evidence_gap"] for row in scored) if scored else None,
        "personal_minus_stop_only": versus_stop,
        # Two claims. Person-specific: the visitor's own picture weights beat an
        # equally fitted stranger's (stop habit held equal). Stable preference:
        # that, and they also beat knowing only the visitor's stop habit.
        "person_specific_detected": below_zero(versus_stranger),
        "stable_preference_detected": below_zero(versus_stranger) and below_zero(versus_stop),
        "personal_minus_population": _interval(scored_units, per_choice("personal", "population"), rng, draws),
        "same_type_rate": _interval(scored_units, lambda flat: _mean(float(row["same_type"]) for row in flat),
                                    rng, draws),
        "same_type_chance": _mean(float(a["type"] == b["type"]) for a, b in strangers),
        "axis_sign_agreement": _interval(scored_units, lambda flat: _mean(row["sign_agreement"] for row in flat),
                                         rng, draws),
        "axis_sign_agreement_chance": _mean(_mean(float((x >= 0) == (y >= 0)) for x, y in zip(a["axes"], b["axes"]))
                                            for a, b in strangers),
    })
    return result


def build_report(records, *, by=None, control=None, seed=0, draws=1000):
    """Compute the full report; `by` splits outcome metrics by a dotted record field."""
    model = engine_module("choice_model")
    catalog = model.load_catalog()
    rng = random.Random(seed)
    items, skipped, partial = [], 0, 0
    for record in records:
        if isinstance(record, dict) and record.get("schema_version") == PARTIAL_SCHEMA_VERSION:
            partial += 1  # a visit without a reading: no outcome to measure here
            continue
        try:
            items.append({**_derive(record, model, catalog), "group": _lookup(record, by) if by else None})
        except (KeyError, TypeError, ValueError):
            skipped += 1
    units = _by_visitor(items)
    visits = [len(unit) for unit in units]
    closest = [item for item in items if item["closest"] is not None]
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "model_version": model.MODEL_VERSION,
        "interval": "95% percentile bootstrap over visitors",
        "volume": {
            "records": len(items), "skipped_records": skipped, "partial_records": partial,
            "visitors": len(units),
            "returning_visitors": sum(1 for count in visits if count >= 2),
            "visits_per_visitor": dict(sorted((str(k), visits.count(k)) for k in set(visits))),
            "first_day": min((item["day"] for item in items if item["day"]), default=None),
            "last_day": max((item["day"] for item in items if item["day"]), default=None),
        },
        "engagement": {
            "rounds_median": statistics.median(item["rounds"] for item in items) if items else None,
            "selections_per_round": _interval(units, lambda flat: (
                math.fsum(item["selections"] for item in flat) / max(1, sum(item["rounds"] for item in flat))),
                rng, draws),
            "records_with_revisions": _mean(float(item["revisions"] > 0) for item in items),
            "asked_follow_up": _mean(float(item["turns"] > 0) for item in items),
        },
        "outcome": {
            **_summary_stats(items, rng, draws),
            "rating_distribution": {str(k): sum(1 for item in items if item["rating"] == k) for k in range(1, 6)},
            "closest_card_answers": len(closest),
        },
        "validity": {
            "holdout_beats_uniform": _mean(float(item["holdout_gain"] > 0) for item in items
                                           if item["holdout_gain"] is not None),
            "holdout_beats_stop_only": _mean(float(item["holdout_gain_vs_stop"] > 0) for item in items
                                             if item["holdout_gain_vs_stop"] is not None),
            "closest_card_is_top_pick": _interval(_by_visitor(closest), lambda flat: _mean(
                float(item["closest"]["hit"]) for item in flat), rng, draws),
            "closest_card_top_pick_chance": _mean(item["closest"]["chance"] for item in closest),
            "closest_card_percentile": _interval(_by_visitor(closest), lambda flat: _mean(
                item["closest"]["percentile"] for item in flat), rng, draws),
            "cross_visit": _cross_visit(items, model, catalog, rng, draws),
        },
    }
    if by:
        names = sorted({item["group"] for item in items})
        reference = control if control in names else (names[0] if names else None)
        groups = {}
        for name in names:
            members = [item for item in items if item["group"] == name]
            groups[name] = _summary_stats(members, rng, draws)
            if name != reference:
                control_items = [item for item in items if item["group"] == reference]
                groups[name]["minus_" + str(reference)] = {
                    key: _difference(control_items, members, key, rng, draws)
                    for key in ("rating", "turns", "holdout_gain")}
        report["groups"] = {"by": by, "control": reference, "arms": groups}
    return report


def _fmt(cell, digits=2):
    if not isinstance(cell, dict):
        return "—" if cell is None else (f"{cell:.{digits}f}" if isinstance(cell, float) else str(cell))
    if cell.get("value") is None:
        return "—"
    text = f"{cell['value']:.{digits}f}"
    return text + (f" [{cell['ci95'][0]:.{digits}f}, {cell['ci95'][1]:.{digits}f}]" if cell.get("ci95") else "")


def format_report(report):
    """A short plain-text view of a report for the terminal."""
    volume, engagement, outcome, validity = (report[key] for key in ("volume", "engagement", "outcome", "validity"))
    cross = validity["cross_visit"]
    lines = [
        f"records {volume['records']} · visitors {volume['visitors']} · returning {volume['returning_visitors']}"
        f" · days {volume['first_day']} → {volume['last_day']}",
        f"rounds median {_fmt(engagement['rounds_median'])} · picks/round {_fmt(engagement['selections_per_round'])}"
        f" · asked a follow-up {_fmt(engagement['asked_follow_up'])}",
        f"rating {_fmt(outcome['rating_mean'])} (answered {_fmt(outcome['rating_response_rate'])})"
        f" · distribution {outcome['rating_distribution']}",
        f"held-out gain over uniform {_fmt(outcome['holdout_gain_nats'], 3)} nats/choice"
        f" · sessions beating uniform {_fmt(validity['holdout_beats_uniform'])}",
        f"held-out gain over stop-only {_fmt(outcome['holdout_gain_vs_stop_nats'], 3)} nats/choice"
        f" · sessions beating stop-only {_fmt(validity['holdout_beats_stop_only'])}",
        f"closest card is the model's top pick {_fmt(validity['closest_card_is_top_pick'])}"
        f" vs chance {_fmt(validity['closest_card_top_pick_chance'])}",
    ]
    if cross.get("status") == "measured":
        loss = cross["log_loss_per_choice"]
        lines += [
            f"next visit log-loss: personal {_fmt(loss['personal'], 3)} · stranger {_fmt(loss['stranger'], 3)}"
            f" · stop-only {_fmt(loss['stop_only'], 3)} · population {_fmt(loss['population'], 3)}"
            f" · uniform {_fmt(loss['uniform'], 3)}",
            f"personal−stranger {_fmt(cross['personal_minus_stranger'], 3)}"
            f" (evidence gap median {_fmt(cross['stranger_evidence_gap_median'])})"
            f" · personal−stop-only {_fmt(cross['personal_minus_stop_only'], 3)}"
            f" · personal−population {_fmt(cross['personal_minus_population'], 3)}",
            f"same type next visit {_fmt(cross['same_type_rate'])} vs strangers {_fmt(cross['same_type_chance'])}"
            f" · axis sign agreement {_fmt(cross['axis_sign_agreement'])} vs {_fmt(cross['axis_sign_agreement_chance'])}",
            f"person-specific picture signal: {'detected' if cross['person_specific_detected'] else 'not shown'}"
            f" · stable preference beyond the stop habit: "
            f"{'detected' if cross['stable_preference_detected'] else 'not shown'}",
        ]
    else:
        lines.append(f"cross-visit: {cross.get('status')} ({cross['returning_visitors']} returning visitors)")
    if "groups" in report:
        lines.append(f"by {report['groups']['by']} (control {report['groups']['control']}):")
        for name, row in report["groups"]["arms"].items():
            delta = next((value for key, value in row.items() if key.startswith("minus_")), None)
            lines.append(f"  {name}: n={row['records']} visitors={row['visitors']} rating {_fmt(row['rating_mean'])}"
                         f" turns {_fmt(row['dialogue_turns_mean'])}"
                         + (f" · Δrating {_fmt(delta['rating'])} Δturns {_fmt(delta['turns'])}" if delta else ""))
    return "\n".join(lines)

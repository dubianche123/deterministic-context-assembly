"""Which card features predict a visitor's later choices? The test that picks the model.

For each record, every candidate is fitted on the visit's earlier rounds,
frozen, and scored on the rounds it never saw. The split is the profile's own
validation split (the last 20% of completed rounds held out). `hybrid` keeps
the scoring-v4 reference unchanged. The original feature candidates share one
sequential logit with a stop option and one Gaussian prior; `hybrid_guarded`
instead runs the scoring-v5 shrinkage and training-only content-evidence gate.
Its result compares the whole prediction policy, not just a feature set.
Every card in a held-out round is new to the fitted model,
because a visit never shows a card twice.

Candidates (card features):
  uniform       no model: every remaining card and stop equally likely
  stop_only     the visitor's stop habit, every card weight 0
  axes          the four type axes (16 distinct values, one per type)
  type16        one-hot type
  pixel         the eight pixel components (scaled as in scoring v4)
  hybrid        axes + pixel components: the scoring v4 model itself
  hybrid_shrunk the same features with a four times stronger prior (features halved), for
                visitors whose few choices make the v4 prior overfit
  motif         recurring motifs (on at least 3 cards) from the card annotations
  hybrid_motif  hybrid + motifs
  hybrid_guarded the v5 production policy on the same hybrid features; no-signal
                 fallback is exactly the training-only stop baseline

Metrics on held-out choices, nats per choice (lower is better) except top1:
  all           every choice: each pick among the remaining cards and stop, and the stop
  cards         picks only, among the remaining cards: picture preference without the stop habit
  within_type   picks among the remaining cards of the picked card's type, when there are
                at least two; axes and type16 are at chance here by construction
  top1          share of held-out rounds whose first pick is the model's best card (ties split)

The learning curve refits on the first k completed rounds and scores the rest.
Intervals are 95% bootstrap intervals over visitors, paired across models.

New images (pilot records with a norn.probe journal): each supported candidate
is fitted on the visitor's whole main journal, the model frozen at reveal, and
scored on the probe's choices among the 16 frozen new images. Only uniform,
stop_only and pixel can score new images; they carry no type or motif labels,
and none are invented. `within_scene` replaces `within_type`: the pick between
the photographic and illustrated version of one scene. Style shares and each
visitor's style consistency are reported against chance, model-free.
Needs numpy; the Worker never imports this module.
"""

import math
import random
from types import SimpleNamespace

from .core import engine_module
from .events import replay_events
from .report import _by_visitor, _interval, _lookup

EVALUATION_SCHEMA_VERSION = "norn.evaluation.v1"
MODELS = ("uniform", "stop_only", "axes", "type16", "pixel", "hybrid", "hybrid_shrunk", "motif", "hybrid_motif",
          "hybrid_guarded")
REFERENCE = "hybrid"
METRICS = ("all", "cards", "within_type", "top1")
PREFIXES = (2, 4, 6, 8)
MOTIF_MIN_CARDS = 3
# Fixed before any real data is read; changing them later must be reported as such.
DECISION_RULE = {
    "min_visitors": 100,
    "useful": "model minus stop_only on `all` has a 95% interval entirely below zero",
    "better": "candidate minus hybrid on `all` has a 95% interval entirely below zero",
    "not_worse_within_type": "candidate minus hybrid on `within_type` has an upper bound below +0.02 nats",
    "choose": "a useful, better candidate with the lowest `all` point estimate; else hybrid if useful; "
              "else report useful candidates with insufficient selection evidence; "
              "only when none is useful, no picture model beats the stop habit",
}
WITHIN_TYPE_MARGIN = 0.02
NEW_MATERIAL_MODELS = ("uniform", "stop_only", "pixel")
NEW_MATERIAL_METRICS = ("all", "cards", "within_scene", "top1")
# The primary estimand is the frozen protocol's (benchmarks/generalization/v1/protocol.json,
# human_evaluation_contract.primary_metric). Coverage, sample size and intervals were left to
# the evaluator; they are fixed here on 2026-10-21, before any human outcome was read, and are
# separate from the original-deck selection rule (DECISION_RULE, 100 visitors).
NEW_MATERIAL_RULE = {
    "protocol": "new-material-v1",
    "primary": "pixel minus stop_only on `all` (held-out per-choice NLL including stop; stop_only fitted on "
               "the visitor's original-deck training choices only) has a 95% interval entirely below zero",
    "secondary": ["`cards` minus uniform (picture choice without stop)", "`within_scene` (photograph vs "
                  "illustration of one scene)", "`top1`", "style share and consistency against chance"],
    "min_visitors": 30,
    "usable_record": "main journal with at least 5 completed rounds and a valid probe with at least 2 completed "
                     "rounds; other probe records are counted as unusable, by reason",
    "interval": "95% percentile bootstrap over visitors, all of a visitor's records together, paired across models",
    "fixed_on": "2026-10-21, before any human outcome",
    "caveat": "the eight pixel components barely encode photo vs illustration (photo-minus-illustration "
              "directions have mean cosine -0.02), so a null result does not show that taste fails to transfer",
}
MIN_MAIN_ROUNDS, MIN_PROBE_ROUNDS = 5, 2


def _numpy():
    try:
        import numpy
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RuntimeError("norn records evaluate needs numpy: pip install numpy") from exc
    return numpy


def feature_sets(catalog=None):
    """Card feature matrices in catalog order, and each card's type."""
    np = _numpy()
    model = engine_module("choice_model")
    catalog = model.load_catalog(catalog)
    base = np.array(catalog.features, dtype=float)
    kinds = sorted(set(catalog.types))
    type16 = np.array([[float(kind == own) for kind in kinds] for own in catalog.types])
    cards = engine_module("fast_thinker")._load_cards()
    counts = {}
    for card in catalog.ids:
        for motif in set(cards[card]["motifs"]):
            counts[motif] = counts.get(motif, 0) + 1
    motifs = sorted(motif for motif, count in counts.items() if count >= MOTIF_MIN_CARDS)
    motif = np.array([[float(name in cards[card]["motifs"]) for name in motifs] for card in catalog.ids])
    # Halving features under the same prior is a four times stronger prior on the weights.
    features = {"axes": base[:, :4], "type16": type16, "pixel": base[:, 4:], "hybrid": base,
                "hybrid_shrunk": base * 0.5, "motif": motif, "hybrid_motif": np.hstack([base, motif])}
    return catalog, features, {"motifs": len(motifs), "types": len(kinds)}


class Steps:
    """Choice steps of some rows in long form: each step's options are the
    cards still on offer (catalog indices) plus stop."""

    def __init__(self, rows, catalog):
        np = _numpy()
        cards, owner, chosen, first, rounds = [], [], [], [], []
        for row in rows:
            alive = [catalog.index[card] for card in row["shown"]]
            choices = [catalog.index[card] for card in row["picks"]] + ([None] if row["stopped"] else [])
            if not alive:
                continue  # nothing reached the screen: no choice was offered (as in choice_model.Prepared)
            for number, choice in enumerate(choices):
                step = len(chosen)
                cards += alive
                owner += [step] * len(alive)
                chosen.append(-1 if choice is None else choice)
                first.append(number == 0 and choice is not None)
                rounds.append(row["round"])
                if choice is not None:
                    alive = [card for card in alive if card != choice]
        self.cards, self.owner = np.array(cards, dtype=int), np.array(owner, dtype=int)
        self.chosen, self.first = np.array(chosen, dtype=int), np.array(first, dtype=bool)
        self.count = len(chosen)

    def utilities(self, matrix, weights):
        return matrix[self.cards] @ weights if len(self.cards) else self.cards.astype(float)


def _segment_logsumexp(np, values, owner, count, extra):
    """Per step log(sum(exp(values of its cards)) + exp(extra))."""
    top = np.full(count, -np.inf)
    np.maximum.at(top, owner, values)
    top = np.maximum(top, extra)
    with np.errstate(invalid="ignore", divide="ignore"):  # a forced stop has no cards; its cards-only value is unused
        total = np.exp(extra - top)
        total = np.where(np.isnan(total), 0.0, total)
        np.add.at(total, owner, np.exp(values - top[owner]))
        return top + np.log(total)


def fit(steps, matrix, *, weights=True):
    """Posterior mode of (card weights, stop bias) under the scoring v4 prior."""
    np = _numpy()
    model = engine_module("choice_model")
    size = matrix.shape[1] if weights else 0
    theta = np.zeros(size + 1)
    precision = np.array([model.WEIGHT_PRECISION] * size + [model.STOP_PRECISION])
    if not steps.count:
        return theta
    x = matrix[steps.cards][:, :size]
    stop = steps.chosen < 0
    chosen_x = np.zeros((steps.count, size))
    chosen_x[~stop] = matrix[steps.chosen[~stop]][:, :size]
    target = np.concatenate([chosen_x.sum(axis=0), [stop.sum()]])

    def objective(theta):
        u = x @ theta[:size]
        lse = _segment_logsumexp(np, u, steps.owner, steps.count, np.full(steps.count, theta[size]))
        picked = (chosen_x @ theta[:size])
        value = np.where(stop, theta[size], picked).sum() - lse.sum() - 0.5 * (precision * theta * theta).sum()
        return value, u, lse

    value, u, lse = objective(theta)
    for _ in range(4 * model.MAX_ITERATIONS):
        p = np.exp(u - lse[steps.owner])
        p_stop = np.exp(theta[size] - lse)
        mean = np.zeros((steps.count, size + 1))
        np.add.at(mean, steps.owner, np.hstack([x * p[:, None], np.zeros((len(p), 1))]))
        mean[:, size] = p_stop
        gradient = target - mean.sum(axis=0) - precision * theta
        options = np.vstack([np.hstack([x, np.zeros((len(p), 1))]),
                             np.hstack([np.zeros((steps.count, size)), np.ones((steps.count, 1))])])
        weights_all = np.concatenate([p, p_stop])
        hessian = (options * weights_all[:, None]).T @ options - mean.T @ mean + np.diag(precision)
        step = np.linalg.solve(hessian, gradient)
        scale = 1.0
        while True:  # the log posterior is concave; halve until it does not decrease
            candidate = theta + scale * step
            new_value, new_u, new_lse = objective(candidate)
            if new_value >= value - 1e-12 or scale < 1e-6:
                break
            scale /= 2
        theta, value, u, lse = candidate, new_value, new_u, new_lse
        if np.abs(scale * step).max() < model.TOLERANCE:
            return theta
    raise ValueError("Evaluation model did not converge")


def score(steps, matrix, theta, types):
    """Summed held-out losses and counts per metric for one fitted model.

    ``types=None`` leaves the within-type metric unobserved; it never assigns
    labels to a new material whose independent annotations are unavailable.
    """
    np = _numpy()
    size = len(theta) - 1
    u = steps.utilities(matrix[:, :size], theta[:size]) if size else np.zeros(len(steps.cards))
    out = {}
    lse_all = _segment_logsumexp(np, u, steps.owner, steps.count, np.full(steps.count, theta[size]))
    stop = steps.chosen < 0
    chosen_u = np.full(steps.count, theta[size])
    position = {}
    for index, (owner, card) in enumerate(zip(steps.owner, steps.cards)):
        position[owner, card] = index
    picks = np.flatnonzero(~stop)
    for step in picks:
        chosen_u[step] = u[position[step, steps.chosen[step]]]
    out["all"] = (float((lse_all - chosen_u).sum()), steps.count)
    if len(picks):
        lse_cards = _segment_logsumexp(np, u, steps.owner, steps.count, np.full(steps.count, -np.inf))
        out["cards"] = (float((lse_cards[picks] - chosen_u[picks]).sum()), len(picks))
    else:
        out["cards"] = (0.0, 0)
    loss, count = 0.0, 0
    if types is not None:
        for step in picks:
            members = np.flatnonzero((steps.owner == step)
                                     & (types[steps.cards] == types[steps.chosen[step]]))
            if len(members) >= 2:
                values = u[members]
                top = values.max()
                loss += float(top + math.log(np.exp(values - top).sum()) - chosen_u[step])
                count += 1
    out["within_type"] = (loss, count)
    hits, rounds = 0.0, 0
    for step in np.flatnonzero(steps.first):
        members = np.flatnonzero(steps.owner == step)
        values = u[members]
        best = np.flatnonzero(values >= values.max() - 1e-12)
        hits += float(steps.chosen[step] in steps.cards[members][best]) / len(best)
        rounds += 1
    out["top1"] = (-hits, rounds)  # negated so that lower is better for every metric
    return out


def _completed(rows):
    return [row for row in rows if row["stopped"]]


def evaluate_rows(rows, catalog, features, types, *, train_count=None):
    """Per-model metric sums for one visit: fit on the first rounds, score the rest."""
    np = _numpy()
    completed = _completed(rows)
    if train_count is None:
        if len(completed) < 5:
            return None
        train_count = len(completed) - max(1, math.ceil(len(completed) * 0.2))
    if not 1 <= train_count < len(completed):
        return None
    train, test = Steps(completed[:train_count], catalog), Steps(completed[train_count:], catalog)
    if not test.count:
        return None
    zeros = np.zeros((len(catalog.ids), 0))
    results = {"uniform": score(test, zeros, np.zeros(1), types)}
    # Uniform puts every option at utility 0, including stop.
    results["stop_only"] = score(test, zeros, fit(train, zeros, weights=False), types)
    for name, matrix in features.items():
        results[name] = score(test, matrix, fit(train, matrix), types)
    # Keep the v4 feature-family reference untouched. The new policy is fitted
    # only on the same training rows; no score or label from test reaches its
    # evidence gate, shrinkage strength or final refit.
    policy, numeric = engine_module("choice_policy"), engine_module("numeric")
    with numeric.use(numeric.PORTABLE_CONTRACT):
        state = policy.fit_policy(completed[:train_count], catalog)
    matrix = np.array(catalog.features, dtype=float)
    results["hybrid_guarded"] = score(test, matrix, np.array(state["effective_theta"], dtype=float), types)
    return results


def _items(records, catalog, features, types, by):
    model = engine_module("choice_model")
    main, curves, skipped = [], {k: [] for k in PREFIXES}, 0
    rejected = {}

    def reject(reason):
        nonlocal skipped
        skipped += 1
        rejected[reason] = rejected.get(reason, 0) + 1

    for record in records:
        if type(record) is not dict or "journal" not in record:
            reject("record_shape")
            continue
        try:
            journal = replay_events(record["journal"])["journal"]
        except (KeyError, TypeError, ValueError):
            # Keep bounded structural categories, never visitor content or the
            # validator's raw exception text, in an exported evaluation report.
            reject("journal_validation")
            continue
        try:
            rows = model.observations(journal, catalog)
        except (KeyError, TypeError, ValueError):
            reject("choice_observation")
            continue
        visitor = record.get("visitor_id") or record.get("record_id")
        base = {"visitor": visitor, "visit": record.get("visit") or 1,
                "group": _lookup(record, by) if by else None}
        held_out = evaluate_rows(rows, catalog, features, types)
        if held_out is not None:
            main.append({**base, "metrics": held_out})
        for k in PREFIXES:
            curve = evaluate_rows(rows, catalog, features, types, train_count=k)
            if curve is not None:
                curves[k].append({**base, "metrics": curve})
    return main, curves, skipped, rejected


def _per_choice(model, metric, minus=None):
    def statistic(flat):
        total = sum(item["metrics"][model][metric][1] for item in flat)
        if not total:
            return None
        value = math.fsum(item["metrics"][model][metric][0] for item in flat) / total
        if minus is None:
            return value
        return value - math.fsum(item["metrics"][minus][metric][0] for item in flat) / total
    return statistic


def _sign(metric, cell):
    """top1 is summed as negative hits; report it as a positive share."""
    if metric != "top1" or cell["value"] is None:
        return cell
    flipped = {**cell, "value": -cell["value"]}
    if cell.get("ci95"):
        flipped["ci95"] = [-cell["ci95"][1], -cell["ci95"][0]]
    return flipped


def _candidate_rng(rng, model):
    """Do not change historical candidates' or the frozen probe's resamples."""
    if model != "hybrid_guarded":
        return rng
    separate = random.Random()
    separate.setstate(rng.getstate())
    return separate


def summarize(items, rng, draws):
    units = _by_visitor(items)
    table = {}
    for model in MODELS:
        candidate_rng = _candidate_rng(rng, model)
        table[model] = {}
        for metric in METRICS:
            cell = {"per_choice": _sign(metric, _interval(units, _per_choice(model, metric), candidate_rng, draws)),
                    "choices": sum(item["metrics"][model][metric][1] for item in items)}
            if model != REFERENCE:
                cell["minus_hybrid"] = _sign(metric, _interval(units, _per_choice(model, metric, REFERENCE),
                                                               candidate_rng, draws))
            if metric == "all" and model not in ("uniform", "stop_only"):
                cell["minus_stop_only"] = _interval(units, _per_choice(model, metric, "stop_only"), candidate_rng, draws)
            table[model][metric] = cell
    return {"records": len(items), "visitors": len(units), "models": table}


def decide(summary):
    """Apply DECISION_RULE to the held-out summary."""
    if summary["visitors"] < DECISION_RULE["min_visitors"]:
        return {"verdict": "insufficient_data", "visitors": summary["visitors"],
                "needed": DECISION_RULE["min_visitors"]}
    def below_zero(cell):
        return cell["ci95"] is not None and cell["ci95"][1] < 0

    useful = [model for model in MODELS if model not in ("uniform", "stop_only")
              and below_zero(summary["models"][model]["all"]["minus_stop_only"])]
    better, blockers = [], {}
    for model in useful:
        if model == REFERENCE:
            continue
        cells = summary["models"][model]
        within = cells["within_type"]["minus_hybrid"]
        reasons = []
        if not below_zero(cells["all"]["minus_hybrid"]):
            reasons.append("not_better_than_hybrid")
        if within["ci95"] is None:
            reasons.append("within_type_unavailable")
        elif within["ci95"][1] >= WITHIN_TYPE_MARGIN:
            reasons.append("within_type_margin_not_met")
        if reasons:
            blockers[model] = reasons
            continue
        better.append((cells["all"]["minus_hybrid"]["value"], model))
    if better:
        return {"verdict": "candidate_better", "model": min(better)[1],
                "all_better": [model for _, model in sorted(better)], "beat_stop_only": useful}
    if REFERENCE in useful:
        return {"verdict": "keep_hybrid", "beat_stop_only": useful}
    if useful:
        return {"verdict": "useful_candidates_inconclusive", "beat_stop_only": useful,
                "selection_blockers": blockers}
    return {"verdict": "no_picture_model_beats_stop_only", "beat_stop_only": useful}


def build_evaluation(records, *, by=None, seed=0, draws=1000):
    """Held-out comparison of every candidate, a learning curve and the decision."""
    np = _numpy()
    catalog, features, sizes = feature_sets()
    kinds = sorted(set(catalog.types))
    types = np.array([kinds.index(kind) for kind in catalog.types])
    rng = random.Random(seed)
    main, curves, skipped, rejected = _items(records, catalog, features, types, by)
    summary = summarize(main, rng, draws)
    new_material = build_new_material(records, rng, draws, catalog, features)
    result = {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "model_version": engine_module("choice_model").MODEL_VERSION,
        "candidate_versions": {"hybrid": engine_module("choice_model").MODEL_VERSION,
                               "hybrid_guarded": engine_module("choice_policy").MODEL_VERSION},
        "model_roles": {"hybrid": "frozen scoring-v4 reference",
                        "hybrid_guarded": "scoring-v5 production policy; training-only gate and small-sample prior"},
        "interval": "95% percentile bootstrap over visitors, paired across models",
        "split": "chronological: last 20% of completed rounds held out (the profile's validation split)",
        "candidates": {**{name: int(matrix.shape[1]) for name, matrix in features.items()},
                       "hybrid_guarded": engine_module("choice_model").FEATURES},
        "motifs_on_at_least": MOTIF_MIN_CARDS, "feature_counts": sizes,
        "prior": {"weight_precision": engine_module("choice_model").WEIGHT_PRECISION,
                  "stop_precision": engine_module("choice_model").STOP_PRECISION},
        "skipped_records": skipped,
        "skipped_record_reasons": rejected,
        "held_out": summary,
        "learning_curve": {str(k): {"records": len(rows), "visitors": len(_by_visitor(rows)),
                                    "all": {model: _interval(_by_visitor(rows), _per_choice(model, "all"), _candidate_rng(rng, model),
                                                             draws) for model in MODELS},
                                    "cards": {model: _interval(_by_visitor(rows), _per_choice(model, "cards"),
                                                               _candidate_rng(rng, model), draws) for model in MODELS}}
                           for k, rows in curves.items()},
        "decision_rule": DECISION_RULE,
        "decision": decide(summary),
    }
    if new_material is not None:
        result["new_material"] = new_material
    if by:
        groups = {}
        for name in sorted({item["group"] for item in main}):
            groups[name] = summarize([item for item in main if item["group"] == name], rng, draws)
        result["groups"] = {"by": by, "arms": groups}
    return result


def _new_material_items(records, catalog, features):
    """Per record: candidates fitted on the main journal, scored on the probe's new-image choices."""
    np = _numpy()
    from .probe import load_probe_catalog, probe_rows, validate_probe
    model = engine_module("choice_model")
    items, rejected = [], {}
    for record in records:
        if type(record) is not dict or record.get("probe") is None:
            continue
        try:
            journal = replay_events(record["journal"])["journal"]
            probe = validate_probe(record["probe"])[0]
        except (KeyError, TypeError, ValueError):
            rejected["probe_validation"] = rejected.get("probe_validation", 0) + 1
            continue
        deck = load_probe_catalog(probe["protocol"])
        ids = sorted(deck["cards"])
        scenes = sorted({card["scene"] for card in deck["cards"].values()})
        lookup = SimpleNamespace(index={card: position for position, card in enumerate(ids)})
        pixel = np.array([deck["cards"][card]["pixel8"] for card in ids]) * model.PIXEL_SCALE
        scene_of = np.array([scenes.index(deck["cards"][card]["scene"]) for card in ids])
        rows = probe_rows(probe)
        test = Steps(rows, lookup)
        training = _completed(model.observations(journal, catalog))
        if len(training) < MIN_MAIN_ROUNDS:
            rejected["main_journal_too_short"] = rejected.get("main_journal_too_short", 0) + 1
            continue
        if len(rows) < MIN_PROBE_ROUNDS or not test.count:
            rejected["probe_too_short"] = rejected.get("probe_too_short", 0) + 1
            continue
        train = Steps(training, catalog)
        empty_new, empty_old = np.zeros((len(ids), 0)), np.zeros((len(catalog.ids), 0))
        metrics = {"uniform": score(test, empty_new, np.zeros(1), scene_of),
                   "stop_only": score(test, empty_new, fit(train, empty_old, weights=False), scene_of),
                   "pixel": score(test, pixel, fit(train, features["pixel"]), scene_of)}
        metrics = {name: {("within_scene" if key == "within_type" else key): value for key, value in cells.items()}
                   for name, cells in metrics.items()}
        styles = {}
        for row in rows:
            for card in row["picks"]:
                styles.setdefault(deck["cards"][card]["scene"], set()).add(deck["cards"][card]["style"])
        decisive = [next(iter(chosen)) for chosen in styles.values() if len(chosen) == 1]
        items.append({"visitor": record.get("visitor_id") or record.get("record_id"),
                      "visit": record.get("visit") or 1, "metrics": metrics,
                      "photo": sum(style == "photograph" for style in decisive), "decisive": len(decisive)})
    return items, rejected


def _expected_majority(n):
    """E[max(X, n - X) / n] for X ~ Binomial(n, 1/2): style consistency by chance."""
    return sum(math.comb(n, k) * max(k, n - k) for k in range(n + 1)) / (n * 2 ** n)


def build_new_material(records, rng, draws, catalog=None, features=None):
    """Held-out comparison on the pilot's new images, or None when no record has a probe."""
    if catalog is None or features is None:
        catalog, features, _ = feature_sets()
    items, rejected = _new_material_items(records, catalog, features)
    if not items and not rejected:
        return None
    units = _by_visitor(items)
    table = {}
    for name in NEW_MATERIAL_MODELS:
        table[name] = {}
        for metric in NEW_MATERIAL_METRICS:
            cell = {"per_choice": _sign(metric, _interval(units, _per_choice(name, metric), rng, draws)),
                    "choices": sum(item["metrics"][name][metric][1] for item in items)}
            if name != "uniform":
                cell["minus_uniform"] = _sign(metric, _interval(units, _per_choice(name, metric, "uniform"),
                                                                rng, draws))
            if name == "pixel" and metric == "all":
                cell["minus_stop_only"] = _interval(units, _per_choice(name, metric, "stop_only"), rng, draws)
            table[name][metric] = cell
    consistent = [item for item in items if item["decisive"] >= 2]
    style = {
        "decisive_scenes": sum(item["decisive"] for item in items),
        "photo_share": _interval(units, lambda flat: (sum(item["photo"] for item in flat) / total
                                                      if (total := sum(item["decisive"] for item in flat)) else None),
                                 rng, draws),
        # Above chance: people keep to one style across scenes more than coin flips would.
        "consistency_minus_chance": _interval(_by_visitor(consistent), lambda flat: _mean_or_none(
            max(item["photo"], item["decisive"] - item["photo"]) / item["decisive"]
            - _expected_majority(item["decisive"]) for item in flat), rng, draws),
    }
    verdict = "insufficient_data"
    if len(units) >= NEW_MATERIAL_RULE["min_visitors"]:
        cell = table["pixel"]["all"]["minus_stop_only"]
        verdict = ("pixel_taste_transfers" if cell["ci95"] is not None and cell["ci95"][1] < 0
                   else "transfer_not_shown")
    return {"records": len(items), "visitors": len(units), "skipped_record_reasons": rejected,
            "coverage": {"probe_records": len(items) + sum(rejected.values()), "usable": len(items)},
            "models": table, "style": style, "rule": NEW_MATERIAL_RULE,
            "decision": {"verdict": verdict, "visitors": len(units),
                         "needed": NEW_MATERIAL_RULE["min_visitors"]},
            "unsupported": {"axes": "no independent annotations", "type16": "no independent annotations",
                            "hybrid": "needs axes", "motif": "no annotations"}}


def _mean_or_none(values):
    values = list(values)
    return math.fsum(values) / len(values) if values else None


def _fmt(cell, digits=3):
    if cell is None or cell.get("value") is None:
        return "—"
    text = f"{cell['value']:.{digits}f}"
    return text + (f" [{cell['ci95'][0]:.{digits}f}, {cell['ci95'][1]:.{digits}f}]" if cell.get("ci95") else "")


def format_evaluation(result):
    held = result["held_out"]
    lines = [f"held-out choices: {held['records']} records · {held['visitors']} visitors"
             f" · skipped {result['skipped_records']}",
             f"{'model':13} {'all':>8} {'cards':>8} {'within':>8} {'top1':>6}   all − hybrid"]
    for model in MODELS:
        cells = held["models"][model]
        value = {metric: cells[metric]["per_choice"]["value"] for metric in METRICS}
        row = " ".join("       —" if value[m] is None else f"{value[m]:8.3f}" for m in ("all", "cards", "within_type"))
        top1 = "     —" if value["top1"] is None else f"{value['top1']:6.2f}"
        lines.append(f"{model:13} {row} {top1}   {_fmt(cells['all'].get('minus_hybrid')) if model != REFERENCE else ''}")
    decision = result["decision"]
    lines.append("decision: " + decision["verdict"] + (f" ({decision['model']})" if "model" in decision else "")
                 + (f" — {decision['visitors']}/{decision['needed']} visitors" if "needed" in decision else ""))
    if decision["verdict"] == "useful_candidates_inconclusive":
        lines.append("beat stop-only: " + ", ".join(decision["beat_stop_only"]))
        for model, reasons in sorted(decision["selection_blockers"].items()):
            lines.append(f"selection pending {model}: " + ", ".join(reasons))
    if result.get("skipped_record_reasons"):
        lines.append("skipped reasons: " + ", ".join(f"{reason}={count}" for reason, count in
                                                     sorted(result["skipped_record_reasons"].items())))
    new = result.get("new_material")
    if new:
        lines.append(f"new images: {new['records']} records · {new['visitors']} visitors")
        for name in NEW_MATERIAL_MODELS:
            cells = new["models"][name]
            lines.append(f"  {name:10} all {_fmt(cells['all']['per_choice'])}"
                         + (f" · all − stop-only {_fmt(cells['all']['minus_stop_only'])} (primary)"
                            if name == "pixel" else "")
                         + f" · within scene {_fmt(cells['within_scene']['per_choice'])}"
                         + (f" · cards − uniform {_fmt(cells['cards']['minus_uniform'])}" if name != "uniform" else ""))
        lines.append(f"  photo share {_fmt(new['style']['photo_share'], 2)} · style consistency − chance "
                     f"{_fmt(new['style']['consistency_minus_chance'], 2)}")
        lines.append(f"  new-image decision: {new['decision']['verdict']}"
                     f" ({new['decision']['visitors']}/{new['decision']['needed']} visitors)")
    return "\n".join(lines)

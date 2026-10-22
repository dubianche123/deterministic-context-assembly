"""Bayesian sequential choice model behind scoring v4, standard library only.

Every card has 12 fixed features: its four hand-authored type coordinates / 8
and 0.5 x eight pixel principal components (config/choice_features.json).
A card's utility is u(c) = w . x(c). In a completed round the final picks, in
click order, are sequential choices among the cards still on screen plus a
"stop" option with utility b; the round ends when "stop" wins. A round with no
pick is one stop choice, and an all-pick round still ends by choosing stop.

Prior w ~ N(0, I/1.5), b ~ N(0, 1/0.5). The posterior is the Newton mode of
these 13 parameters with a Laplace (inverse Hessian) covariance. The four axis
weights say which card archetypes this session's choices favoured; they are a
per-session fit on a few dozen clicks, not a personality measurement.

Transcendental math goes through numeric, so a profile's numerical contract
makes native and WebAssembly replay identical.
"""

from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from operator import mul as _mul
from pathlib import Path

if __package__:
    from . import numeric
else:
    import numeric


MODEL_VERSION = "norn.choice.seq-mnl.v1"
CATALOG_SCHEMA_VERSION = "norn.choice-features.v1"
TRAJECTORY_SCHEMA_VERSION = "norn.trajectory.v1"
CONTRIBUTION_SCHEMA_VERSION = "norn.choice.contributions.v1"
AXES = ("E_I", "S_N", "T_F", "J_P")
LETTERS = (("E", "I"), ("N", "S"), ("T", "F"), ("J", "P"))  # (positive, negative)
FEATURES = 12
PARAMETERS = FEATURES + 1
WEIGHT_PRECISION = 1.5
STOP_PRECISION = 0.5
PIXEL_SCALE = 0.5
TOLERANCE = 1e-9
MAX_ITERATIONS = 100
TOP_CARDS = 6
TOP_TYPES = 4
HALTON_POINTS = 2048  # plus antithetic copies: 4096 deterministic draws
MAX_ROUNDS = 256
PRIOR = (WEIGHT_PRECISION,) * FEATURES + (STOP_PRECISION,)
FEATURE_NAMES = tuple(f"axis.{axis}" for axis in AXES) + tuple(f"pixel.pc{k}" for k in range(1, 9))
CONTRIBUTION_TOLERANCE = 1e-12
SEMANTICS = ("Sequential multinomial logit over displayed cards with a stop option; "
             "axis weights describe favoured card archetypes in this session, not personality.")
_CATALOG_CACHE = None
_NORMALS = {}


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _hash(value):
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


class Catalog:
    """Validated, read-only card features in sorted card-ID order."""

    def __init__(self, document):
        if type(document) is not dict or document.get("schema_version") != CATALOG_SCHEMA_VERSION:
            raise ValueError("Unsupported choice feature schema")
        if document.get("feature_version") != _hash({k: v for k, v in document.items() if k != "feature_version"}):
            raise ValueError("Choice feature content hash mismatch")
        if tuple(document.get("axes", ())) != AXES:
            raise ValueError("Choice features must use the four card type axes")
        cards = document.get("cards")
        if type(cards) is not dict or not 2 <= len(cards) <= 512:
            raise ValueError("Choice catalog needs 2 to 512 cards")
        self.ids = sorted(cards)
        self.index = {card: position for position, card in enumerate(self.ids)}
        self.types, self.features, pixel_first = [], [], []
        for card in self.ids:
            row = cards[card]
            axes, pixel, kind = row.get("axes"), row.get("pixel"), row.get("type")
            if (type(axes) is not list or len(axes) != 4 or type(pixel) is not list or len(pixel) != 8
                    or not all(_finite(value) for value in axes + pixel) or any(abs(value) > 1 for value in axes)
                    or type(kind) is not str or len(kind) != 4
                    or any(kind[i] not in LETTERS[i] for i in range(4))):
                raise ValueError("Invalid choice feature row")
            self.types.append(kind)
            self.features.append(tuple(float(value) for value in axes)
                                 + tuple(PIXEL_SCALE * float(value) for value in pixel))
            pixel_first.append(float(pixel[0]))
        self.feature_version = document["feature_version"]
        self.components = deepcopy(document.get("components", []))
        self.cells = _grid(self.ids, self.types, pixel_first)


def _grid(ids, types, pixel_first):
    """16x16 tapestry: S|N halves left/right, T|F halves top/bottom; inside
    each quadrant I|E columns and J|P rows; within a type block, cards run
    along their first pixel component so each block reads as a gradient."""
    by_type = {}
    for position, kind in enumerate(types):
        by_type.setdefault(kind, []).append(position)
    cells = {}
    for kind, members in by_type.items():
        col0 = (2 * (kind[1] == "N") + (kind[0] == "E")) * 4
        row0 = (2 * (kind[2] == "F") + (kind[3] == "P")) * 4
        members.sort(key=lambda position: (pixel_first[position], ids[position]))
        for k, position in enumerate(members):
            cells[ids[position]] = [col0 + k % 4, row0 + k // 4]
    return cells


def load_catalog(document=None):
    """Return the shipped catalog (cached per file version) or validate one."""
    global _CATALOG_CACHE
    if document is not None:
        return document if isinstance(document, Catalog) else Catalog(document)
    path = Path(__file__).parent / "config" / "choice_features.json"
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if _CATALOG_CACHE is None or _CATALOG_CACHE[0] != key:
        with path.open(encoding="utf-8") as stream:
            catalog = Catalog(json.load(stream))
        if __package__:
            from . import fast_thinker
        else:
            import fast_thinker
        metadata = fast_thinker._load_cards()
        if sorted(metadata) != catalog.ids or any(
                catalog.types[i] != metadata[card]["type"]
                or list(catalog.features[i][:4]) != [metadata[card]["coords"][axis] / 8 for axis in AXES]
                for i, card in enumerate(catalog.ids)):
            raise ValueError("Choice features differ from card metadata; rerun scripts/prepare_choice_features.py")
        _CATALOG_CACHE = (key, catalog)
    return _CATALOG_CACHE[1]


# ── Observations ────────────────────────────────────────────

def observations(journal, catalog=None, *, include_open=False):
    """Final picks per round in click order, then the stop choice.

    A pick is the first click of a card that is still selected when its round
    closes. When a v2 journal lists seen_card_ids, cards that never reached the
    screen leave that round's choice set (as in the 979-feature ranker); null
    visibility and v1 journals keep every displayed card. Callers validate the
    journal first (norn.events.replay_events); other event types are ignored.
    """
    catalog = load_catalog(catalog)
    if type(journal) is not dict or type(journal.get("events")) is not list:
        raise ValueError("Choice model needs an event journal")
    rows, shown, active, order, current = [], None, set(), [], None
    for event in journal["events"]:
        kind = event.get("type") if type(event) is dict else None
        if kind == "round_started":
            if current is not None:
                raise ValueError("Round started before the previous round closed")
            if any(card not in catalog.index for card in event["card_ids"]):
                raise ValueError("Unknown card in displayed round")
            shown, order, current = list(event["card_ids"]), [], event["round"]
        elif kind in ("card_selected", "card_deselected"):
            if current is None or event["card_id"] not in shown:
                raise ValueError("Choice event outside its displayed round")
            if kind == "card_selected":
                active.add(event["card_id"])
                order.append(event["card_id"])
            else:
                active.discard(event["card_id"])
        elif kind == "round_completed":
            if current is None:
                raise ValueError("Round completed before it started")
            rows.append(_row(current, shown, order, active, True, event["duration_ms"], event.get("seen_card_ids")))
            current = None
    if include_open and current is not None:
        rows.append(_row(current, shown, order, active, False, None, None))
    if len(rows) > MAX_ROUNDS:
        raise ValueError("Too many rounds for the choice model")
    return rows


def _row(round_number, shown, order, active, stopped, duration, visible):
    compared = list(shown) if visible is None else [card for card in shown if card in set(visible)]
    picks, seen = [], set()
    for card in order:
        if card in active and card not in seen and card in compared:
            picks.append(card)
            seen.add(card)
    row = {"round": round_number, "shown": compared, "picks": picks, "stopped": stopped, "duration_ms": duration}
    if len(compared) != len(shown):
        row["unseen"] = [card for card in shown if card not in compared]
    return row


_CARD_PAIRS = [(a, b) for a in range(FEATURES) for b in range(a, FEATURES)]
_ALL_PAIRS = [(a, b) for a in range(PARAMETERS) for b in range(a, PARAMETERS)]


class Prepared:
    """Rows as feature columns, plus every option's x x^T in pair columns.

    A fit over the first n rows reads only those rows' options, so all
    prefixes of a trajectory share one preparation.
    """

    def __init__(self, rows, catalog):
        self.rows, options = [], []
        for row in rows:
            position = {card: i for i, card in enumerate(row["shown"])}
            cards = [catalog.features[catalog.index[card]] for card in row["shown"]]
            steps = [position[card] for card in row["picks"]] + ([None] if row["stopped"] else [])
            if not cards:
                steps = []  # nothing reached the screen: stop was the only option, no evidence
            chosen = [0.0] * PARAMETERS
            for step in steps:
                if step is None:
                    chosen[FEATURES] += 1.0
                else:
                    chosen = [total + value for total, value in zip(chosen, cards[step] + (0.0,))]
            self.rows.append((cards, [list(column) for column in zip(*cards)], steps, chosen))
            options.extend(cards)
        self.outer = [[card[a] * card[b] for card in options] for a, b in _CARD_PAIRS]

    def __len__(self):
        return len(self.rows)


# ── Posterior ───────────────────────────────────────────────

def _evaluate(theta, prepared, count):
    """Log-posterior gradient and negative Hessian over the first count rows."""
    exp, mul = numeric.exp, _mul
    weights, bias = theta[:FEATURES], theta[FEATURES]
    gradient = [-precision * value for precision, value in zip(PRIOR, theta)]
    exposure, means, stop_total = [], [], 0.0
    for cards, columns, steps, chosen in prepared.rows[:count]:
        if not steps:
            continue
        gradient = [total + value for total, value in zip(gradient, chosen)]
        utilities = [sum(map(mul, weights, card)) for card in cards]
        top = max(max(utilities), bias)
        mass = [exp(value - top) for value in utilities]
        stop_mass = exp(bias - top)
        alive = mass[:]
        seen = [0.0] * len(cards)
        for choice in steps:
            inverse = 1.0 / (stop_mass + sum(alive))
            p = [value * inverse for value in alive]
            seen = [total + value for total, value in zip(seen, p)]
            mean = [sum(map(mul, p, column)) for column in columns]
            mean.append(stop_mass * inverse)
            stop_total += mean[FEATURES]
            means.append(mean)
            if choice is not None:
                alive[choice] = 0.0
        exposure.extend(seen)
    columns = [list(column) for column in zip(*means)] if means else [[] for _ in range(PARAMETERS)]
    for a in range(PARAMETERS):
        gradient[a] -= sum(columns[a])
    upper = {}
    for (a, b), outer in zip(_CARD_PAIRS, prepared.outer):
        upper[a, b] = sum(map(mul, exposure, outer))
    upper[FEATURES, FEATURES] = stop_total
    hessian = [[0.0] * PARAMETERS for _ in range(PARAMETERS)]
    for a, b in _ALL_PAIRS:
        value = upper.get((a, b), 0.0) - sum(map(mul, columns[a], columns[b]))
        if a == b:
            value += PRIOR[a]
        hessian[a][b] = hessian[b][a] = value
    return gradient, hessian


def _solve(matrix, vector):
    """Gaussian elimination with partial pivoting (basic IEEE operations)."""
    size = len(vector)
    rows = [list(row) + [value] for row, value in zip(matrix, vector)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda r: abs(rows[r][column]))
        if rows[pivot][column] == 0.0:
            raise ValueError("Singular choice-model Hessian")
        rows[column], rows[pivot] = rows[pivot], rows[column]
        lead = rows[column]
        for r in range(column + 1, size):
            factor = rows[r][column] / lead[column]
            if factor:
                target = rows[r]
                for c in range(column, size + 1):
                    target[c] -= factor * lead[c]
    solution = [0.0] * size
    for r in range(size - 1, -1, -1):
        total = rows[r][size] - sum(rows[r][c] * solution[c] for c in range(r + 1, size))
        solution[r] = total / rows[r][r]
    return solution


def _inverse(matrix):
    size = len(matrix)
    columns = [_solve(matrix, [1.0 if r == c else 0.0 for r in range(size)]) for c in range(size)]
    return [[columns[c][r] for c in range(size)] for r in range(size)]


def fit(prepared, start=None, count=None):
    """Newton mode and Laplace covariance; warm starts give the same mode."""
    count = len(prepared) if count is None else count
    theta = [0.0] * PARAMETERS if start is None else [float(value) for value in start]
    for iteration in range(1, MAX_ITERATIONS + 1):
        gradient, hessian = _evaluate(theta, prepared, count)
        step = _solve(hessian, gradient)
        theta = [value + delta for value, delta in zip(theta, step)]
        if max(abs(delta) for delta in step) < TOLERANCE:
            break
    else:
        raise ValueError("Choice model did not converge")
    if not all(math.isfinite(value) for value in theta):
        raise ValueError("Choice model diverged")
    return theta, _inverse(hessian), iteration


def card_scores(theta, catalog=None):
    catalog = load_catalog(catalog)
    weights = theta[:FEATURES]
    return [sum(map(_mul, weights, card)) for card in catalog.features]


def top_cards(scores, catalog, count=TOP_CARDS):
    order = sorted(range(len(scores)), key=lambda i: (-scores[i], catalog.ids[i]))
    return [catalog.ids[i] for i in order[:count]]


# ── Type probabilities ──────────────────────────────────────

def _radical_inverse(index, base):
    value, scale = 0.0, 1.0 / base
    while index:
        index, digit = divmod(index, base)
        value += digit * scale
        scale /= base
    return value


def _normal_quantile(p):
    """Acklam's rational approximation (relative error < 1.2e-9)."""
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00)
    low = 0.02425
    if p < low or p > 1 - low:
        q = numeric.sqrt(-2 * numeric.log(p if p < low else 1 - p))
        value = ((((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
                 / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1))
        return value if p < low else -value
    q = p - 0.5
    r = q * q
    return ((((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q
            / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1))


def _normals():
    contract = numeric.current_contract()
    if contract not in _NORMALS:
        points = []
        for index in range(1, HALTON_POINTS + 1):
            point = tuple(_normal_quantile(_radical_inverse(index, base)) for base in (2, 3, 5, 7))
            points.extend((point, tuple(-value for value in point)))
        _NORMALS[contract] = points
    return _NORMALS[contract]


def _cholesky(matrix):
    size = len(matrix)
    lower = [[0.0] * size for _ in range(size)]
    for i in range(size):
        for j in range(i + 1):
            total = matrix[i][j] - sum(lower[i][k] * lower[j][k] for k in range(j))
            if i == j:
                if total <= 0:
                    raise ValueError("Posterior covariance is not positive definite")
                lower[i][i] = numeric.sqrt(total)
            else:
                lower[i][j] = total / lower[j][j]
    return lower


def type_probabilities(mean, covariance, top=TOP_TYPES):
    """Sign pattern of the four axis weights under the Gaussian posterior,
    from 4096 fixed quasi-random draws (Halton bases 2/3/5/7, antithetic)."""
    (l0, _, _, _), (l10, l11, _, _), (l20, l21, l22, _), (l30, l31, l32, l33) = _cholesky(covariance)
    m0, m1, m2, m3 = mean
    counts = Counter()
    for e0, e1, e2, e3 in _normals():
        counts[(m0 + l0 * e0 >= 0, m1 + l10 * e0 + l11 * e1 >= 0,
                m2 + l20 * e0 + l21 * e1 + l22 * e2 >= 0,
                m3 + l30 * e0 + l31 * e1 + l32 * e2 + l33 * e3 >= 0)] += 1
    total = 2 * HALTON_POINTS
    rows = [{"type": "".join(LETTERS[i][0 if signs[i] else 1] for i in range(4)), "p": count / total}
            for signs, count in counts.items()]
    rows.sort(key=lambda row: (-row["p"], row["type"]))
    return rows[:top]


# ── Session summary used by profile v4 ──────────────────────

def _axes(theta, covariance):
    return {"names": list(AXES), "mean": theta[:4],
            "sd": [numeric.sqrt(covariance[i][i]) for i in range(4)],
            "covariance": [row[:4] for row in covariance[:4]]}


def _log_loss(theta, prepared):
    """Mean negative log-likelihood per choice, and the uniform baseline."""
    exp, log = numeric.exp, numeric.log
    weights, bias = theta[:FEATURES], theta[FEATURES]
    model, uniform, count = [], [], 0
    for shown, _, steps, _ in prepared.rows:
        utilities = [sum(map(_mul, weights, card)) for card in shown]
        alive = list(range(len(shown)))
        for choice in steps:
            options = [utilities[i] for i in alive] + [bias]
            top = max(options)
            chosen = bias if choice is None else utilities[choice]
            model.append(top - chosen + log(math.fsum(exp(value - top) for value in options)))
            uniform.append(log(len(options)))
            count += 1
            if choice is not None:
                alive.remove(choice)
    if not count:
        return None, None, 0
    return math.fsum(model) / count, math.fsum(uniform) / count, count


def _fit_stop_only(prepared):
    """Posterior mode of the stop bias alone, every card weight held at 0."""
    exp, bias = numeric.exp, 0.0
    for _ in range(MAX_ITERATIONS):
        gradient, curvature = -STOP_PRECISION * bias, STOP_PRECISION
        for cards, _, steps, _ in prepared.rows:
            remaining = len(cards)
            for choice in steps:
                p = 1.0 / (1.0 + remaining * exp(-bias))  # e^b / (e^b + remaining)
                gradient += (choice is None) - p
                curvature += p * (1.0 - p)
                remaining -= choice is not None
        step = gradient / curvature
        bias += step
        if abs(step) < TOLERANCE:
            return bias
    raise ValueError("Stop-only baseline did not converge")


def validate(rows, catalog=None):
    """Chronological holdout: fit earlier rounds, score the last 20 percent.

    Two baselines: uniform choice, and a stop bias fitted on the same training
    rounds with every card weight at 0. Beating uniform alone can come from
    learning how often this person stops, so the verdict needs both.
    """
    catalog = load_catalog(catalog)
    completed = [row for row in rows if row["stopped"]]
    result = {"status": "insufficient", "reason": "fewer_than_five_completed_rounds",
              "method": "chronological_holdout_20_percent_choice_log_loss",
              "log_loss": None, "random_baseline": {"log_loss": None, "description": "uniform over remaining cards and stop"},
              "stop_only_baseline": {"log_loss": None, "stop_bias": None,
                                     "description": "stop bias fitted on the training rounds; every card weight 0"},
              "beats_random": False, "beats_stop_only": False, "beats_baselines": False,
              "train_observation_count": 0, "holdout_observation_count": 0, "holdout_choice_count": 0,
              "claim": "Measures held-out choices in this session only; no personality accuracy or population "
                       "validation. beats_baselines requires beating both baselines."}
    if len(completed) < 5:
        return result
    cutoff = len(completed) - max(1, math.ceil(len(completed) * 0.2))
    train, holdout = Prepared(completed[:cutoff], catalog), Prepared(completed[cutoff:], catalog)
    theta, _, _ = fit(train)
    loss, baseline, count = _log_loss(theta, holdout)
    stop_bias = _fit_stop_only(train)
    stop_loss, _, _ = _log_loss([0.0] * FEATURES + [stop_bias], holdout)
    enough = count >= 3
    beats_random, beats_stop_only = bool(enough and loss < baseline), bool(enough and loss < stop_loss)
    result.update(status="measured" if enough else "insufficient",
                  reason=None if enough else "fewer_than_three_held_out_choices",
                  log_loss=loss, beats_random=beats_random, beats_stop_only=beats_stop_only,
                  beats_baselines=beats_random and beats_stop_only,
                  train_observation_count=cutoff, holdout_observation_count=len(completed) - cutoff,
                  holdout_choice_count=count)
    result["random_baseline"]["log_loss"] = baseline
    result["stop_only_baseline"].update(log_loss=stop_loss, stop_bias=stop_bias)
    return result


def contributions(theta, covariance, roles, catalog=None):
    """u(c) = sum_k w_k x_k(c), term by term, for the listed cards.

    Each card keeps its 12 feature values, the 12 terms, their sum and the
    posterior sd of that sum (sqrt(x' Cov(w) x)). verify_contributions
    recomputes everything; the profile hash covers the stored copy.
    """
    catalog = load_catalog(catalog)
    weights = list(theta[:FEATURES])
    cards = []
    for card, role in roles.items():
        values = list(catalog.features[catalog.index[card]])
        terms = [weight * value for weight, value in zip(weights, values)]
        variance = sum(values[a] * covariance[a][b] * values[b] for a in range(FEATURES) for b in range(FEATURES))
        cards.append({"card_id": card, "roles": role, "values": values, "terms": terms,
                      "score": math.fsum(terms), "score_sd": numeric.sqrt(max(variance, 0.0))})
    return {"schema_version": CONTRIBUTION_SCHEMA_VERSION, "feature_names": list(FEATURE_NAMES),
            "weights": weights, "weight_sd": [numeric.sqrt(covariance[i][i]) for i in range(FEATURES)],
            "cards": cards,
            "reconstruction": {"formula": "term_k = weight_k * value_k; score = fsum(terms)",
                               "absolute_tolerance": CONTRIBUTION_TOLERANCE}}


def verify_contributions(block, catalog=None):
    """Recompute every value, term and total against the catalog and weights."""
    catalog = load_catalog(catalog)
    if (type(block) is not dict or block.get("schema_version") != CONTRIBUTION_SCHEMA_VERSION
            or block.get("feature_names") != list(FEATURE_NAMES)):
        raise ValueError("Unsupported choice contribution block")
    weights = block.get("weights")
    if type(weights) is not list or len(weights) != FEATURES or not all(_finite(value) for value in weights):
        raise ValueError("Invalid contribution weights")
    for card in block.get("cards", []):
        if card.get("card_id") not in catalog.index:
            raise ValueError("Unknown card in contribution block")
        values = list(catalog.features[catalog.index[card["card_id"]]])
        terms = [weight * value for weight, value in zip(weights, values)]
        if (card.get("values") != values or card.get("terms") != terms
                or abs(math.fsum(terms) - card.get("score", math.inf)) > CONTRIBUTION_TOLERANCE
                or abs(sum(map(_mul, weights, values)) - card["score"]) > CONTRIBUTION_TOLERANCE):
            raise ValueError("Choice contribution does not reconstruct its score")
    return True


def _component_label(component):
    parts = [row["label"] + ("较高" if row["loading"] > 0 else "较低") for row in component.get("top_loadings", [])[:2]]
    return "画面视觉成分：" + "、".join(parts) if parts else "画面视觉成分"


def summarize(rows, catalog=None, *, selected=(), include_open=False):
    """The posterior summary stored in a v4 profile's analysis.preference."""
    catalog = load_catalog(catalog)
    theta, covariance, iterations = fit(Prepared(rows, catalog))
    scores = card_scores(theta, catalog)
    # A round whose cards never reached the screen offered no real choice.
    informative = [row for row in rows if row["shown"]]
    picks = sum(len(row["picks"]) for row in informative)
    stops = sum(1 for row in informative if row["stopped"])
    trained = bool(informative)
    basis = {"rows": [{key: row[key] for key in ("round", "shown", "picks", "stopped")} for row in rows],
             "feature_version": catalog.feature_version, "prior": list(PRIOR), "include_open": include_open}
    features = [{"name": component.get("name", f"pixel.pc{k + 1}"), "label": _component_label(component),
                 "weight": theta[4 + k]} for k, component in enumerate(catalog.components[:8])]
    features.sort(key=lambda row: (-abs(row["weight"]), row["name"]))
    by_id = dict(zip(catalog.ids, scores))
    selected_evidence = sorted(({"card_id": card, "score": by_id[card]} for card in selected if card in by_id),
                               key=lambda row: (-row["score"], row["card_id"]))[:8]
    recommended = top_cards(scores, catalog) if trained else []
    selected_ids = [row["card_id"] for row in selected_evidence]
    roles = {card: [role for role, members in (("recommended", recommended), ("selected", selected_ids))
                    if card in members] for card in dict.fromkeys(selected_ids + recommended)}
    breakdown = contributions(theta, covariance, roles, catalog)
    verify_contributions(breakdown, catalog)
    return {
        "status": "trained" if trained else "no_observations", "trained": trained,
        "model_version": MODEL_VERSION, "feature_version": catalog.feature_version,
        "observation_count": len(informative), "round_count": len(rows),
        "choice_count": picks + stops, "pick_count": picks,
        "stop_count": stops, "feature_count": FEATURES, "parameter_count": PARAMETERS,
        "include_open": include_open, "provisional": any(not row["stopped"] for row in rows),
        "prior": {"weight_precision": WEIGHT_PRECISION, "stop_precision": STOP_PRECISION},
        "training_hash": _hash(basis),
        "posterior": {"mean": theta, "sd": [numeric.sqrt(covariance[i][i]) for i in range(PARAMETERS)],
                      "iterations": iterations},
        "axes": _axes(theta, covariance),
        "stop_bias": theta[FEATURES],
        "type_probabilities": type_probabilities(theta[:4], [row[:4] for row in covariance[:4]]),
        "top_features": features if trained else [],
        "recommendations": [{"card_id": card, "score": by_id[card], "score_kind": "choice_utility"}
                            for card in recommended],
        "selected_evidence": selected_evidence,
        "contributions": breakdown,
        "validation": validate(rows, catalog),
        "score_semantics": "Choice utility relative to this session's other cards; not personality probability.",
        "semantics": SEMANTICS,
    }


# ── Per-round trajectory for the X-Ray ──────────────────────

def _r(value, digits):
    return round(value, digits) + 0.0


def _state_contributions(theta, covariance, cards, catalog):
    """Per-round breakdown for the top guesses and this round's picks, rounded."""
    block = contributions(theta, covariance, {card: [] for card in cards}, catalog)
    return {"weights": [_r(value, 4) for value in block["weights"]],
            "weight_sd": [_r(value, 4) for value in block["weight_sd"]],
            "cards": [{"card_id": row["card_id"], "axes": [_r(term, 3) for term in row["terms"][:4]],
                       "pixel": _r(math.fsum(row["terms"][4:]), 3),
                       "pixel_terms": [_r(term, 3) for term in row["terms"][4:]],
                       "score": _r(row["score"], 3), "sd": _r(row["score_sd"], 3)} for row in block["cards"]]}


def path(rows, catalog=None):
    """Unrounded posterior after each completed round (state 0 = prior)."""
    catalog = load_catalog(catalog)
    completed = [row for row in rows if row["stopped"]]
    prepared = Prepared(completed, catalog)
    states, theta = [], None
    for count in range(len(completed) + 1):
        theta, covariance, iterations = fit(prepared, theta, count)
        states.append({"round": count, "theta": theta, "covariance": covariance, "iterations": iterations,
                       "row": completed[count - 1] if count else None})
    return states


def trajectory(rows, catalog=None):
    """Compact per-round states; a page scrubs them without new requests."""
    catalog = load_catalog(catalog)
    states, previous = [], [0.0] * 4
    for raw in path(rows, catalog):
        theta, covariance = raw["theta"], raw["covariance"]
        mean = theta[:4]
        cov4 = [row[:4] for row in covariance[:4]]
        scores = card_scores(theta, catalog)
        state = {"round": raw["round"],
                 "mean": [_r(value, 4) for value in mean],
                 "sd": [_r(numeric.sqrt(cov4[i][i]), 4) for i in range(4)],
                 "cov_sn_tf": [[_r(cov4[a][b], 5) for b in (1, 2)] for a in (1, 2)],
                 "scores": [_r(value, 3) for value in scores],
                 "top": top_cards(scores, catalog),
                 "types": [{"type": row["type"], "p": _r(row["p"], 3)} for row in type_probabilities(mean, cov4)],
                 "stop_bias": _r(theta[FEATURES], 3)}
        row = raw["row"]
        if row is not None:
            state.update(shown=list(row["shown"]), picks=list(row["picks"]),
                         delta=[_r(now - before, 4) for now, before in zip(mean, previous)],
                         duration_ms=row["duration_ms"])
            if row.get("unseen"):
                state["unseen"] = list(row["unseen"])
        listed = list(dict.fromkeys(state["top"] + state.get("picks", [])))
        state["contributions"] = _state_contributions(theta, covariance, listed, catalog)
        previous = mean
        states.append(state)
    return {"schema_version": TRAJECTORY_SCHEMA_VERSION, "model_version": MODEL_VERSION,
            "feature_version": catalog.feature_version, "axes": list(AXES), "letters": [list(pair) for pair in LETTERS],
            "prior": {"weight_precision": WEIGHT_PRECISION, "stop_precision": STOP_PRECISION},
            "components": [{"name": component.get("name"), "label": _component_label(component),
                            "explained_variance_ratio": component.get("explained_variance_ratio")}
                           for component in catalog.components],
            "cards": [{"id": card, "type": catalog.types[i], "cell": catalog.cells[card]}
                      for i, card in enumerate(catalog.ids)],
            "feature_names": list(FEATURE_NAMES),
            "rounds": len(states) - 1, "states": states, "semantics": SEMANTICS}

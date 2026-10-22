"""Research v6 for a deck with a population reference, in portable Python.

Three nested choice models over a card's features x2 = [u, x8], where u is the
population utility of the card and x8 its eight content components:

    L0  population:    utility = u
    L1  sensitivity:   utility = a * u                 (a: how closely this visitor follows the population)
    L2  deviation:     utility = a * u + x8 . delta    (delta: personal deviation)

Each is a sequential logit with a stop option, fitted by MAP with the
small-sample prior of the research studies: a ~ N(1, 1/q(n)) with
q(n) = 1.5 (1 + 1/max(1, n)); delta ~ N(0, 1/p(n)) with p(n) = 1.5 (1 + 8/max(1, n));
stop ~ N(0, 1/0.5). n counts completed rounds with at least one visible card.

Two prequential e-values decide what may be claimed, each against the
threshold 20 (anytime valid by Ville's inequality):

    E1  L1 against L0 on picks (does this visitor follow the population more or less?)
    E2  L2 against the best sensitivity-only model in hindsight (a composite null
        over a in [-100, 100], universal-inference style)

The effective model is L2 if E2 passes, else L1 if E1 passes, else L0. This is
the arithmetic of norn-research's `research/lapis_active.Session`; rounds may
show any number of cards.

An optional population context (norn-research `research/norn_context`) adds what
other visitors showed about the screen, fixed for the whole session:

    card utility  + c[m][slot]                (slot: reading-order index among the m cards dealt)
    stop utility  + gamma * log(remaining cards)

Rows then carry `slots` and `m`. Without a context every offset is exactly 0.0,
so the arithmetic, and every fitted value and hash, is research v6's.
"""

from . import choice_model as base
from . import numeric

EVIDENCE_THRESHOLD = 20.0
WEIGHT_PRECISION, STOP_PRECISION = 1.5, 0.5
SENSITIVITY_BOUND = 100.0
TOLERANCE, MAX_ITERATIONS = 1e-9, 400
CONTENT = 8


def q_precision(rounds):
    return WEIGHT_PRECISION * (1.0 + 1.0 / max(1, rounds))


def p_precision(rounds):
    return WEIGHT_PRECISION * (1.0 + CONTENT / max(1, rounds))


def _dot(weights, values):
    return sum(w * v for w, v in zip(weights, values))


def card_offsets(row, context):
    """Each shown card's slot offset under a population context; zeros without one."""
    if context is None:
        return [0.0] * len(row["shown"])
    table = context["offsets"].get(str(row["m"]))
    return [0.0] * len(row["shown"]) if table is None else [float(table[slot]) for slot in row["slots"]]


def stop_offset(remaining, context):
    return 0.0 if context is None else context["gamma"] * numeric.log(float(remaining))


def _steps(rows, context=None):
    """(options, chosen index or None, option offsets, stop offset) per choice step;
    a forced stop has no options and is left out."""
    out = []
    for row in rows:
        offset = dict(zip(row["shown"], card_offsets(row, context)))
        alive = list(row["shown"])
        for pick in list(row["picks"]) + [None]:
            if not alive:
                break
            out.append((list(alive), None if pick is None else alive.index(pick),
                        [offset[card] for card in alive], stop_offset(len(alive), context)))
            if pick is not None:
                alive.remove(pick)
    return out


def _objective(theta, steps, x, prior, precision):
    """Log posterior, gradient and negative Hessian of the sequential logit with stop."""
    size = len(theta) - 1
    weights, bias = theta[:size], theta[size]
    value = -0.5 * sum(p * (t - m) ** 2 for p, t, m in zip(precision, theta, prior))
    gradient = [-p * (t - m) for p, t, m in zip(precision, theta, prior)]
    hessian = [[precision[a] if a == b else 0.0 for b in range(size + 1)] for a in range(size + 1)]
    for options, chosen, offsets, extra in steps:
        rows = [x[card] for card in options]
        utilities = [_dot(weights, row) + o for row, o in zip(rows, offsets)]
        stop = bias + extra
        top = max(utilities + [stop])
        masses = [numeric.exp(u - top) for u in utilities]
        stop_mass = numeric.exp(stop - top)
        total = stop_mass + sum(masses)
        value += (stop if chosen is None else utilities[chosen]) - top - numeric.log(total)
        p = [m / total for m in masses]
        p_stop = stop_mass / total
        mean = [sum(pj * row[k] for pj, row in zip(p, rows)) for k in range(size)] + [p_stop]
        for a in range(size + 1):
            gradient[a] += (1.0 if a == size else 0.0) if chosen is None else (rows[chosen][a] if a < size else 0.0)
            gradient[a] -= mean[a]
            for b in range(a, size + 1):
                if a < size and b < size:
                    second = sum(pj * row[a] * row[b] for pj, row in zip(p, rows))
                else:
                    second = p_stop if a == b == size else 0.0
                hessian[a][b] += second - mean[a] * mean[b]
                hessian[b][a] = hessian[a][b]
    return value, gradient, hessian


def fit(rows, x, mean, precision, start=None, context=None):
    """MAP of (weights, stop bias) under N(mean, 1/precision), stop N(0, 1/0.5); damped Newton."""
    prior = list(mean) + [0.0]
    prec = list(precision) + [STOP_PRECISION]
    theta = list(prior) if start is None else [float(v) for v in start]
    steps = _steps(rows, context)
    if not steps:
        return list(prior)
    value, gradient, hessian = _objective(theta, steps, x, prior, prec)
    for _ in range(MAX_ITERATIONS):
        step = base._solve(hessian, gradient)
        scale = 1.0
        while True:
            candidate = [t + scale * s for t, s in zip(theta, step)]
            new = _objective(candidate, steps, x, prior, prec)
            if new[0] >= value - 1e-12 or scale < 1e-6:
                break
            scale /= 2
        theta, (value, gradient, hessian) = candidate, new
        if max(abs(scale * s) for s in step) < TOLERANCE:
            return theta
    raise ValueError("research choice model did not converge")


def pick_log_probs(row, weights, x, context=None):
    """log P(pick | remaining cards) for picks that had a real alternative."""
    offset = dict(zip(row["shown"], card_offsets(row, context)))
    remaining, out = list(row["shown"]), []
    for pick in row["picks"]:
        if len(remaining) > 1:
            utilities = [_dot(weights, x[card]) + offset[card] for card in remaining]
            top = max(utilities)
            out.append(utilities[remaining.index(pick)] - top
                       - numeric.log(sum(numeric.exp(u - top) for u in utilities)))
        remaining.remove(pick)
    return out


def max_sensitivity_loglik(rows, u, start=1.0, context=None):
    """max over a in [-100, 100] of the sensitivity-only pick log-likelihood (concave in a)."""
    picks = []
    for row in rows:
        offset = dict(zip(row["shown"], card_offsets(row, context)))
        remaining = list(row["shown"])
        for pick in row["picks"]:
            if len(remaining) > 1:
                picks.append(([u[card] for card in remaining], u[pick],
                              [offset[card] for card in remaining], offset[pick]))
            remaining.remove(pick)
    if not picks:
        return 0.0, start

    def derivatives(a):
        loglik = first = second = 0.0
        for values, chosen, offsets, chosen_offset in picks:
            scaled = [a * v + o for v, o in zip(values, offsets)]
            top = max(scaled)
            e = [numeric.exp(s - top) for s in scaled]
            total = sum(e)
            p = [v / total for v in e]
            mean = sum(pj * v for pj, v in zip(p, values))
            loglik += a * chosen + chosen_offset - top - numeric.log(total)
            first += chosen - mean
            second -= sum(pj * v * v for pj, v in zip(p, values)) - mean * mean
        return loglik, first, second

    lo, hi = -SENSITIVITY_BOUND, SENSITIVITY_BOUND
    if derivatives(lo)[1] <= 0:
        return derivatives(lo)[0], lo
    if derivatives(hi)[1] >= 0:
        return derivatives(hi)[0], hi
    a = min(max(start, lo), hi)
    for _ in range(200):
        _, gradient, hessian = derivatives(a)
        if gradient > 0:
            lo = a
        else:
            hi = a
        newton = a - gradient / hessian if hessian < 0 else None
        a_new = newton if newton is not None and lo < newton < hi else (lo + hi) / 2
        if abs(a_new - a) < 1e-10 or hi - lo < 1e-10:
            a = a_new
            break
        a = a_new
    return derivatives(a)[0], a


class Session:
    """The research-v6 state, one completed round at a time; x2 maps card -> [u] + x8.

    `context` is the deck's population context (or None), fixed for the session."""

    def __init__(self, x2, context=None):
        self.x2, self.context = x2, context
        self.x1 = {card: row[:1] for card, row in x2.items()}
        self.rows = []
        self.e1 = self.numerator = self.e2 = 0.0
        self.a_hat = 1.0
        self.e1_path, self.e2_path = [], []
        self.l1 = fit([], self.x1, [1.0], [q_precision(0)])
        self.l2 = fit([], self.x2, self.mean2(), self.precision2(0))

    @staticmethod
    def mean2():
        return [1.0] + [0.0] * CONTENT

    @staticmethod
    def precision2(n):
        return [q_precision(n)] + [p_precision(n)] * CONTENT

    def add(self, row):
        """Add one completed round whose `shown` lists the cards that reached the screen."""
        if not row["shown"]:
            return
        context = self.context
        candidate = pick_log_probs(row, self.l1[:1], self.x1, context)
        reference = pick_log_probs(row, [1.0], self.x1, context)
        self.e1 += sum(candidate) - sum(reference)
        self.numerator += sum(pick_log_probs(row, self.l2[:-1], self.x2, context))
        self.rows.append(row)
        n = len(self.rows)
        best, self.a_hat = max_sensitivity_loglik(self.rows, {c: v[0] for c, v in self.x2.items()}, self.a_hat,
                                                  context)
        self.e2 = self.numerator - best
        self.e1_path.append(self.e1)
        self.e2_path.append(self.e2)
        self.l1 = fit(self.rows, self.x1, [1.0], [q_precision(n)], start=self.l1, context=context)
        self.l2 = fit(self.rows, self.x2, self.mean2(), self.precision2(n), start=self.l2, context=context)

    def layer(self):
        threshold = numeric.log(EVIDENCE_THRESHOLD)
        return "personal" if self.e2 >= threshold else "sensitivity" if self.e1 >= threshold else "population"

    def effective(self):
        """Gated utility weights over x2 (L0 is a = 1, delta = 0)."""
        layer = self.layer()
        if layer == "personal":
            return list(self.l2[:-1])
        if layer == "sensitivity":
            return [self.l1[0]] + [0.0] * CONTENT
        return [1.0] + [0.0] * CONTENT

    def candidate_scores(self, cards):
        """L2 candidate utilities (display uses them; they are not a claim)."""
        return {card: _dot(self.l2[:-1], self.x2[card]) for card in cards}

"""Which cards a main-deck round shows, and why: the reference for norn.events.v5.

Each session is randomized once, in the browser, to one display policy:

    norn.display.random.v1  every card drawn uniformly from the cards not yet
                            shown in this pass through the deck (Norn before v5)
    norn.display.mixed.v1   floor(m/2) of the m cards ranked by the visitor's
                            scoring-v5 candidate, the rest drawn uniformly

The candidate is choice_policy's regularized MAP over the completed rounds,
before the content gate: display is not a claim, so the gate does not apply.
Ties (an untrained candidate in round 1) are broken at random. Screen order is
a uniform permutation of the drawn cards, so role and position stay
independent. A mixed round whose candidate could not be fitted is drawn like a
random round and logged with every role "random".

The browser draws rounds (frontend/app.js, same rule). This module verifies
logged rounds, simulates sessions and supports analysis. Display depends only
on completed rounds, so the prequential evidence gate stays valid whenever
the visitor stops.
"""

from . import choice_model as base
from . import choice_policy

RANDOM = "norn.display.random.v1"
MIXED = "norn.display.mixed.v1"
POLICIES = (RANDOM, MIXED)
ROLES = ("random", "predicted")
ARMS = {RANDOM: "random", MIXED: "mixed"}
# A logged prediction counts as top-ranked within this much utility (browser and
# reference may differ in the last bits of floating point).
TIE_TOLERANCE = 1e-9


def predicted_count(cards):
    return cards // 2


def check_display(display, card_ids, policy=None):
    """Structural rules of one round's display record; returns its policy."""
    if type(display) is not dict or set(display) != {"policy", "roles"}:
        raise ValueError("display must contain exactly: policy, roles")
    if display["policy"] not in POLICIES:
        raise ValueError("Unknown display policy")
    if policy is not None and display["policy"] != policy:
        raise ValueError("A session keeps one display policy")
    roles = display["roles"]
    if type(roles) is not list or len(roles) != len(card_ids) or any(role not in ROLES for role in roles):
        raise ValueError("display roles must give one known role per displayed card")
    predicted = roles.count("predicted")
    if display["policy"] == RANDOM and predicted:
        raise ValueError("A random round has no predicted cards")
    if display["policy"] == MIXED and predicted not in (0, predicted_count(len(card_ids))):
        raise ValueError("A mixed round predicts half its cards, rounded down, or none")
    return display["policy"]


def session_arm(journal):
    """"mixed" or "random" for a v5 main journal, None for older journals."""
    for event in journal["events"]:
        if event.get("type") == "round_started" and "display" in event:
            return ARMS[event["display"]["policy"]]
    return None


def candidate_scores(rows, catalog=None):
    """Card utilities of the scoring-v5 candidate fitted on completed rows."""
    catalog = base.load_catalog(catalog)
    completed = [row for row in rows if row["stopped"]]
    informative = sum(bool(row["shown"]) for row in completed)
    theta, _, _ = choice_policy._fit(base.Prepared(completed, catalog), len(completed),
                                     choice_policy.weight_precision(informative))
    return dict(zip(catalog.ids, base.card_scores(theta, catalog)))


def _round_starts(journal):
    return [(position, event) for position, event in enumerate(journal["events"])
            if event.get("type") == "round_started"]


def available_cards(deck, history, count):
    """Cards the browser may draw from: not yet shown in this pass, or the whole deck once too few remain."""
    used = set()
    for shown in history:
        if len(deck) - len(used) < len(shown):
            used = set()
        used |= set(shown)
    if len(deck) - len(used) < count:
        used = set()
    return [card for card in deck if card not in used]


def verify(journal, catalog=None):
    """Recompute every logged display decision of a validated v5 main journal.

    Checks that each round drew from the available cards and, for predicted
    roles, that no available card outranked a predicted one under the
    candidate fitted on the rounds completed before it.
    """
    catalog = base.load_catalog(catalog)
    starts, history, failures, checked = _round_starts(journal), [], [], 0
    for position, event in starts:
        shown = event["card_ids"]
        available = available_cards(catalog.ids, history, len(shown))
        display = event["display"]
        if not set(shown) <= set(available):
            failures.append({"round": event["round"], "reason": "card_not_available"})
        elif "predicted" in display["roles"]:
            prefix = {"schema_version": journal["schema_version"], "events": journal["events"][:position]}
            try:
                scores = candidate_scores(base.observations(prefix, catalog), catalog)
            except ValueError:
                failures.append({"round": event["round"], "reason": "reference_fit_failed"})
                history.append(shown)
                continue
            predicted = [card for card, role in zip(shown, display["roles"]) if role == "predicted"]
            others = [scores[card] for card in available if card not in set(predicted)]
            floor = min(scores[card] for card in predicted)
            if others and max(others) > floor + TIE_TOLERANCE * (1.0 + abs(floor)):
                failures.append({"round": event["round"], "reason": "predicted_not_top"})
            checked += 1
        history.append(shown)
    return {"rounds": len(starts), "predicted_rounds_checked": checked, "failures": failures,
            "verified": not failures}


def draw(policy, rows, available, count, rng, catalog=None):
    """One round by the reference rule: (card_ids in screen order, roles). rng is a random.Random."""
    pool = list(available)
    rng.shuffle(pool)
    chosen, roles = [], []
    if policy == MIXED:
        scores = candidate_scores(rows, catalog)
        k = predicted_count(count)
        ranked = sorted(pool, key=lambda card: -scores[card])  # stable: shuffled order breaks ties
        chosen = ranked[:k]
        roles = ["predicted"] * k
        pool = [card for card in pool if card not in set(chosen)]
    elif policy != RANDOM:
        raise ValueError("Unknown display policy")
    filler = pool[:count - len(chosen)]
    chosen += filler
    roles += ["random"] * len(filler)
    order = list(range(len(chosen)))
    rng.shuffle(order)
    return [chosen[i] for i in order], [roles[i] for i in order]

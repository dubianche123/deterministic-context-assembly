"""Norn's research deck: PAM∃LA images with a population reference, research v6 and display v2.

A research deployment serves one research deck in place of Norn's own cards
(host setting NORN_DECK). The deck file (config/decks/<name>.json) carries each
card's population utility u and eight content components x8, fitted on the
dataset's raters, plus labels for the reading. Every round of a session logs:

    display = {"policy": ..., "deck": "<name>@<deck_version>", "roles": [...]}

Policies (randomized once per session in the browser):
    norn.display.random.v2  every card uniform from the cards not yet shown in this pass
    norn.display.mixed.v2   floor(m/2) cards ranked by the visitor's research-v6 L2
                            candidate; one card from the most popular quarter of the
                            rest and one from the least popular quarter (population u);
                            any remaining card uniform. Screen order is a uniform permutation.

As in display v1, a mixed round whose candidate cannot be fitted is logged with
every role "random", and display reads only completed rounds.
The reading uses research v6 (research_model): the population model unless a
prequential e-value supports the visitor's sensitivity (E1) or a personal
deviation (E2).

A deck may carry a population context (`context`, schema norn.slot-context.v1):
screen-slot offsets per number of cards dealt and a stop slope, fitted on
earlier visitors' sessions. It is part of the deck version, so a session names
the context it used. A deck without one is research v6 exactly.
"""

import json
import math
from pathlib import Path

from . import choice_model as base
from . import display_policy
from . import research_model
from . import session_context

RANDOM = "norn.display.random.v2"
MIXED = "norn.display.mixed.v2"
POLICIES = (RANDOM, MIXED)
ARMS = {RANDOM: "random", MIXED: "mixed"}
ROLES = ("predicted", "contrast_popular", "contrast_unpopular", "random")
SCHEMA_VERSION = "norn.research-deck.v1"
CONTEXT_SCHEMA_VERSION = "norn.slot-context.v1"
PROFILE_SCHEMA_VERSION = "norn.research-profile.v1"
ALGORITHM_VERSION = "norn.research.v6.v1"
DECKS = ("pamela-v1",)
_LOADED = {}


class Deck:
    def __init__(self, document):
        if type(document) is not dict or document.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Unsupported research deck schema")
        if document.get("deck_version") != base._hash({k: v for k, v in document.items() if k != "deck_version"}):
            raise ValueError("Research deck content hash mismatch")
        self.name, self.version = document["deck"], document["deck_version"]
        self.label = f"{self.name}@{self.version}"
        self.cards = document["cards"]
        self.ids = sorted(self.cards)
        self.x2 = {card: [float(row["u"])] + [float(v) for v in row["x8"]] for card, row in self.cards.items()}
        if any(len(v) != 1 + research_model.CONTENT or not all(math.isfinite(x) for x in v) for v in self.x2.values()):
            raise ValueError("Invalid research deck features")
        self.u = {card: v[0] for card, v in self.x2.items()}
        self.context = _check_context(document.get("context"))
        self.document = document


def _check_context(context):
    """A deck's population context, or None; offsets are centred within each card count."""
    if context is None:
        return None
    if (type(context) is not dict or set(context) != {"schema_version", "offsets", "gamma", "provenance"}
            or context["schema_version"] != CONTEXT_SCHEMA_VERSION or type(context["offsets"]) is not dict
            or type(context["provenance"]) is not dict):
        raise ValueError("Invalid deck population context")
    for count, values in context["offsets"].items():
        if (not count.isdecimal() or type(values) is not list or len(values) != int(count)
                or not all(type(v) in (int, float) and math.isfinite(v) for v in values)
                or abs(math.fsum(values)) > 1e-9):
            raise ValueError("Invalid deck population context offsets")
    if type(context["gamma"]) not in (int, float) or not math.isfinite(context["gamma"]):
        raise ValueError("Invalid deck population context stop slope")
    return context


def load_deck(name):
    if name not in DECKS:
        raise ValueError("Unknown research deck")
    if name not in _LOADED:
        path = Path(__file__).parent / "config" / "decks" / f"{name}.json"
        _LOADED[name] = Deck(json.loads(path.read_text(encoding="utf-8")))
    return _LOADED[name]


def deck_of(journal):
    """The deck a v5 journal's display records name, or None for Norn's own deck."""
    for event in journal.get("events", []) if isinstance(journal, dict) else []:
        if isinstance(event, dict) and event.get("type") == "round_started":
            display = event.get("display")
            if isinstance(display, dict) and isinstance(display.get("deck"), str):
                return display["deck"].split("@", 1)[0]
            return None
    return None


def predicted_count(cards):
    return cards // 2


def check_display(display, card_ids, policy=None):
    """Structural rules of one v2 round; returns its policy (events.replay_events calls this)."""
    if type(display) is not dict or set(display) != {"policy", "deck", "roles"}:
        raise ValueError("display must contain exactly: deck, policy, roles")
    if display["policy"] not in POLICIES:
        raise ValueError("Unknown display policy")
    if policy is not None and display["policy"] != policy:
        raise ValueError("A session keeps one display policy")
    if type(display["deck"]) is not str or display["deck"].split("@", 1)[0] not in DECKS:
        raise ValueError("Unknown research deck")
    roles = display["roles"]
    if type(roles) is not list or len(roles) != len(card_ids) or any(role not in ROLES for role in roles):
        raise ValueError("display roles must give one known role per displayed card")
    if display["policy"] == RANDOM or roles.count("random") == len(roles):
        if roles.count("random") != len(roles):
            raise ValueError("A random round has only random cards")
        return display["policy"]
    if (roles.count("predicted") != predicted_count(len(card_ids)) or roles.count("contrast_popular") != 1
            or roles.count("contrast_unpopular") != 1):
        raise ValueError("A mixed round predicts half its cards, rounded down, plus one popular and one unpopular contrast")
    return display["policy"]


def observations(journal):
    """Completed rounds as choice_model.observations reads them: final picks, in first-click
    order, among the cards that reached the screen, then the stop."""
    rows, shown, order, active = [], None, [], set()
    for event in journal["events"]:
        kind = event["type"]
        if kind == "round_started":
            shown, order = list(event["card_ids"]), []
        elif kind == "card_selected":
            active.add(event["card_id"])
            order.append(event["card_id"])
        elif kind == "card_deselected":
            active.discard(event["card_id"])
        elif kind == "round_completed":
            row = base._row(event["round"], shown, order, active, True, event["duration_ms"],
                            event.get("seen_card_ids"))
            # Reading-order slot among all m cards dealt (a population context's offsets).
            row["slots"], row["m"] = [shown.index(card) for card in row["shown"]], len(shown)
            rows.append(row)
    return rows


def session(rows, deck):
    state = research_model.Session(deck.x2, deck.context)
    for row in rows:
        state.add(row)
    return state


def quarters(cards, u):
    """The least and most popular quarters of cards, as numpy.array_split of a stable sort by u."""
    ordered = sorted(cards, key=lambda card: u[card])
    size, extra = divmod(len(ordered), 4)
    bottom = ordered[:size + (1 if extra else 0)]
    top = ordered[len(ordered) - size:] if size else ordered[-1:]
    return bottom, top


def draw(policy, rows, available, count, rng, deck):
    """One round by the reference rule: (card_ids in screen order, roles). rng is a random.Random."""
    pool = list(available)
    rng.shuffle(pool)
    chosen, roles = [], []
    if policy == MIXED:
        scores = session(rows, deck).candidate_scores(pool)
        k = predicted_count(count)
        chosen = sorted(pool, key=lambda card: -scores[card])[:k]
        rest = [card for card in pool if card not in set(chosen)]
        bottom, top = quarters(rest, deck.u)
        popular, unpopular = rng.choice(top), rng.choice(bottom)
        chosen += [popular, unpopular]
        roles = ["predicted"] * k + ["contrast_popular", "contrast_unpopular"]
        pool = [card for card in rest if card not in (popular, unpopular)]
    elif policy != RANDOM:
        raise ValueError("Unknown display policy")
    filler = pool[:count - len(chosen)]
    chosen += filler
    roles += ["random"] * len(filler)
    order = list(range(len(chosen)))
    rng.shuffle(order)
    return [chosen[i] for i in order], [roles[i] for i in order]


def verify(journal, deck):
    """Recompute every logged role of a validated research journal with this reference."""
    starts = [(p, e) for p, e in enumerate(journal["events"]) if e.get("type") == "round_started"]
    history, failures, checked = [], [], 0
    tolerance = display_policy.TIE_TOLERANCE
    for position, event in starts:
        shown, roles = event["card_ids"], event["display"]["roles"]
        available = display_policy.available_cards(deck.ids, history, len(shown))
        history.append(shown)
        if event["display"]["deck"] != deck.label:
            failures.append({"round": event["round"], "reason": "other_deck_version"})
            continue
        if not set(shown) <= set(available):
            failures.append({"round": event["round"], "reason": "card_not_available"})
            continue
        if "predicted" not in roles:
            continue
        rows = observations({"events": journal["events"][:position]})
        try:
            scores = session(rows, deck).candidate_scores(available)
        except ValueError:
            failures.append({"round": event["round"], "reason": "reference_fit_failed"})
            continue
        role_of = dict(zip(shown, roles))
        predicted = [card for card in shown if role_of[card] == "predicted"]
        floor = min(scores[card] for card in predicted)
        if any(scores[card] > floor + tolerance * (1.0 + abs(floor)) for card in available if card not in predicted):
            failures.append({"round": event["round"], "reason": "predicted_not_top"})
        rest = [card for card in available if card not in predicted]
        bottom, top = quarters(rest, deck.u)
        popular = next(card for card in shown if role_of[card] == "contrast_popular")
        unpopular = next(card for card in shown if role_of[card] == "contrast_unpopular")
        if deck.u[popular] < min(deck.u[c] for c in top) or deck.u[unpopular] > max(deck.u[c] for c in bottom):
            failures.append({"round": event["round"], "reason": "contrast_outside_quarter"})
        checked += 1
    return {"rounds": len(starts), "mixed_rounds_checked": checked, "failures": failures, "verified": not failures}


# ── Profile and reading context ─────────────────────────────

def _relation(layer, sensitivity):
    if layer == "population":
        return "not_established"
    if layer == "sensitivity":
        return "follows_more" if sensitivity > 1 else "follows_less" if sensitivity >= 0 else "against"
    return "personal"


CLARITY = {
    "not_established": "还没有足够可靠的线索说明你和大多数人的口味有什么不同，先保留这次实际选择的记录",
    "follows_more": "你的选择比大多数人更贴近大家普遍喜欢的画面",
    "follows_less": "你的选择没那么跟随大家普遍喜欢的画面",
    "against": "你常常留下大多数人不太偏爱的画面",
    "personal": "这次的选择前后一致，有一条属于你自己的线索",
}
CLARITY_EN = {
    "not_established": "There are not yet reliable clues that your taste differs from most people's; we can describe the choices you actually made.",
    "follows_more": "Your choices lean even more than most people's toward widely liked pictures.",
    "follows_less": "Your choices follow widely liked pictures less than most people's do.",
    "against": "You often kept pictures most people are less drawn to.",
    "personal": "Your choices were consistent and show a thread of your own.",
}


def build_profile(journal, deck, events_hash):
    """The v6 state of a completed journal as a profile the reading and the record can carry."""
    rows = observations(journal)
    state = session([row for row in rows if row["shown"]], deck)
    layer = state.layer()
    effective = state.effective()
    utility = {card: research_model._dot(effective, deck.x2[card]) for card in deck.ids}
    final, events, revisions = {}, {}, []
    for event in journal["events"]:
        if event["type"] == "card_selected":
            final[event["card_id"]] = {"card_id": event["card_id"], "round": event["round"], "event_id": event["event_id"]}
        elif event["type"] == "card_deselected":
            final.pop(event["card_id"], None)
            revisions.append({"card_id": event["card_id"], "round": event["round"], "event_id": event["event_id"]})
    shown = {card for row in rows for card in row["shown"]} | {card for e in journal["events"]
                                                              if e["type"] == "round_started" for card in e["card_ids"]}
    selections = sorted(final.values(), key=lambda row: (-utility[row["card_id"]], row["event_id"]))
    rounds = len(rows)
    preference = {"model_version": ALGORITHM_VERSION, "deck": deck.label, "status": layer,
                  "relation": _relation(layer, state.l1[0]), "trained": bool(state.rows),
                  "observation_count": len(state.rows), "sensitivity": state.l1[0],
                  "log_evalues": {"E1": state.e1, "E2": state.e2},
                  "log_threshold": math.log(research_model.EVIDENCE_THRESHOLD),
                  "effective": effective, "candidate": list(state.l2),
                  "recommendations": [{"card_id": card, "score": utility[card]}
                                      for card in sorted((c for c in deck.ids if c not in shown),
                                                         key=lambda c: (-utility[c], c))[:6]]}
    analysis = {"analysis_kind": "learned_preference", "total_rounds": rounds,
                "total_selections": len(final), "data_density": "sparse_mode" if rounds <= 5 else
                "dense_mode" if rounds <= 20 else "overflow_mode",
                "choice_revision": {"total": len(revisions)}, "preference": preference,
                "attribution": {"selections": selections, "revision": {"events": revisions}}}
    profile = {"schema_version": PROFILE_SCHEMA_VERSION, "algorithm_version": ALGORITHM_VERSION,
               "metadata_version": deck.version, "source": "event_journal", "events_hash": events_hash,
               "feature_version": deck.version, "deck": deck.label, "analysis": analysis}
    profile["profile_hash"] = base._hash(profile)
    return profile


def build_context(profile, deck, *, language="zh"):
    """A frozen reading context in the shape session_context's prompts accept (prompt v5)."""
    analysis = profile["analysis"]
    preference = analysis["preference"]
    mode = analysis["data_density"]
    cards = deck.cards
    picked = [row["card_id"] for row in analysis["attribution"]["selections"]]

    def evidence(row, kind):
        card = cards[row["card_id"]]
        return {"kind": kind, "event_id": row["event_id"], "card_id": row["card_id"], "round": row["round"],
                "scene": str(card["scene"])[:80], "reference_kind": "event", "traits": card["traits"][:3]}

    selections = analysis["attribution"]["selections"]
    revisions = analysis["attribution"]["revision"]["events"]
    relation = preference["relation"]
    drawn, next_to_try = [], []
    if relation != "not_established":
        utility = {card: research_model._dot(preference["effective"], deck.x2[card]) for card in deck.ids}
        by_motif = {}
        for card in deck.ids:
            for motif in cards[card]["motifs"]:
                by_motif.setdefault(motif, []).append(card)
        ranked = sorted(((math.fsum(utility[c] for c in ids) / len(ids), motif, ids) for motif, ids in by_motif.items()
                         if len(ids) >= session_context.MIN_MOTIF_CARDS), key=lambda row: (-row[0], row[1]))
        drawn = [{"motif": motif, "picked": sum(card in picked for card in ids)}
                 for _, motif, ids in ranked if any(card in picked for card in ids)][:4]
        next_to_try = [{"scene": str(cards[row["card_id"]]["scene"])[:40], "motifs": cards[row["card_id"]]["motifs"][:2]}
                       for row in preference["recommendations"][:2]]
    english = language == "en"
    block = {
        "schema_version": session_context.CONTEXT_SCHEMA_VERSION, "prompt_version": session_context.PROMPT_V5,
        "profile_version": profile["schema_version"], "profile_hash": profile["profile_hash"],
        "algorithm_version": profile["algorithm_version"], "metadata_version": profile["metadata_version"],
        "source": profile["source"], "events_hash": profile["events_hash"], "mode": mode,
        "analysis_kind": "learned_preference",
        "style": session_context._STYLE_EN[mode] if english else {
            "sparse_mode": "温和、简洁，中文120至220字；证据不足时明确仍在探索。",
            "dense_mode": "具体、克制，中文220至340字；写图片偏好、真实取舍和下一次尝试。",
            "overflow_mode": "沉静、具体，中文300至450字；分段写偏好、选择证据和下一步探索。"}[mode],
        "total_rounds": analysis["total_rounds"], "total_selections": analysis["total_selections"],
        "choice_revision": {"total": analysis["choice_revision"]["total"]},
        "evidence": {"selection_limit": session_context.MAX_SELECTION_EVIDENCE,
                     "revision_limit": session_context.MAX_REVISION_EVIDENCE,
                     "selection_total": len(selections), "revision_total": len(revisions),
                     "selections": [evidence(r, "final_selection") for r in selections[:session_context.MAX_SELECTION_EVIDENCE]],
                     "revisions": [{**evidence(r, "deselection"), "final_selected": r["card_id"] in picked}
                                   for r in revisions[:session_context.MAX_REVISION_EVIDENCE]],
                     "omitted_selections": max(0, len(selections) - session_context.MAX_SELECTION_EVIDENCE),
                     "omitted_revisions": max(0, len(revisions) - session_context.MAX_REVISION_EVIDENCE)},
        "preference": {"status": preference["status"], "model_version": preference["model_version"],
                       "observation_count": preference["observation_count"],
                       "personalization": "not_established" if relation == "not_established" else "active",
                       "drawn_to": drawn, "next_to_try": next_to_try,
                       "evidence_clarity": (CLARITY_EN if english else CLARITY)[relation]},
        "evidence_scope": ("Limited clues from this visit. Event IDs locate records; they do not establish personality causes. "
                           "Do not invent unseen records." if english else
                           "本次会话的有限算法线索；事件ID只用于定位记录，不能证明人格因果。未展示记录不可臆造。"),
        "preference_scope": (
            ("No reliable difference from most people's taste has been established. drawn_to and next_to_try stay empty; "
             "describe only the pictures actually kept." if english else
             "本次尚未建立与大多数人口味不同的可靠线索；drawn_to 和 next_to_try 留空，只描述实际留下和撤回的画面。")
            if relation == "not_established" else
            ("Compared with how many people rated these pictures, drawn_to lists picture elements in what you kept; "
             "next_to_try suggests pictures not shown yet. evidence_clarity says how this visit relates to common taste. "
             "These describe image preferences for this visit, not a personality assessment." if english else
             "与许多人对这些画面的评价相比：drawn_to 是你留下的画面里的元素，next_to_try 是这次还没出现、你可能会停留的画面，"
             "evidence_clarity 说明这次的选择和大家普遍口味的关系。只描述这一次的图片偏好，不是人格测评。")),
    }
    if english:
        block["language"] = "en"
    serialized = session_context._canonical(block)
    return {"schema_version": session_context.CONTEXT_SCHEMA_VERSION, "prompt_version": block["prompt_version"],
            "profile_hash": block["profile_hash"], "mode": mode, "block": block, "canonical_block": serialized,
            "context_hash": session_context._hash(block)}


def arm(journal):
    for event in journal["events"]:
        if event.get("type") == "round_started" and "display" in event:
            return ARMS.get(event["display"]["policy"])
    return None

"""Read-only explanations for the frozen v6 scorer.

This module does not change the scorer, its gate, or its sealed profile. A
separate content-addressed ledger explains the *effective* utility of every
card. Candidate weights are disclosed separately and never called evidence.
"""

from copy import deepcopy
import math

from .core import engine_module
from .events import canonical_json, content_hash, replay_events, validate_post_session

REPLAY_VERSION = "norn.research-replay.v1"
LEDGER_VERSION = "norn.research-contributions.v1"
TRAJECTORY_VERSION = "norn.research-trajectory.v1"
FEATURE_NAMES = ["reference_utility"] + [f"content_{i}" for i in range(1, 9)]

_REFERENCE_CLARITY = {
    "zh": {
        "not_established": "还没有足够可靠的线索说明你与这批图片的参考评分者有什么口味差异；先描述这次实际留下的画面",
        "follows_more": "相较这批图片的参考评分者，你的选择更偏向其中评价较高的画面",
        "follows_less": "你的选择较少跟随这批图片的参考评分者给出的高评价",
        "against": "你常常留下这批图片的参考评分者评价较低的画面",
        "personal": "这次的选择已积累起超出参考评分差异的个人线索",
    },
    "en": {
        "not_established": "There are not yet reliable clues that your taste differs from the reference raters of this image set; describe the pictures you actually kept.",
        "follows_more": "Your choices lean more strongly toward pictures rated highly by the reference raters of this image set.",
        "follows_less": "Your choices follow the high ratings given by the reference raters of this image set less closely.",
        "against": "You often kept pictures rated lower by the reference raters of this image set.",
        "personal": "This visit has accumulated clues to a personal preference beyond differences in reference ratings.",
    },
}


def build_context(profile, deck, *, language="zh", finale=None):
    """Scope product prose to the dataset's raters without changing frozen v6 math.

    Named prose fields and an observed-only product reading intent are adapted.
    Evidence, model results and the profile hash remain those of the frozen
    builder; the canonical context and its hash identify this prose projection.
    """
    context = engine_module("research_deck").build_context(profile, deck, language=language)
    block = context["block"]
    relation = profile["analysis"]["preference"]["relation"]
    english = language == "en"
    block["preference"]["evidence_clarity"] = _REFERENCE_CLARITY["en" if english else "zh"][relation]
    if relation == "not_established":
        block["preference_scope"] = (
            "No reliable difference from the reference raters of this image set has been established. "
            "drawn_to and next_to_try stay empty; describe only the pictures actually kept. "
            "This does not establish agreement with the reference raters."
            if english else
            "本次尚未建立你与这批图片的参考评分者口味不同的可靠线索；drawn_to 和 next_to_try 留空，"
            "只描述实际留下和撤回的画面。证据不足也不代表已经证明你与参考评分者口味相同。")
    else:
        block["preference_scope"] = (
            "Compared with the reference ratings for this image set, drawn_to lists picture elements in what you kept; "
            "next_to_try suggests pictures not shown yet. evidence_clarity describes this visit relative to those "
            "reference ratings. These describe image preferences for this visit, not a personality assessment."
            if english else
            "相较这批图片的参考评分：drawn_to 是你留下的画面里的元素，next_to_try 是这次还没出现、"
            "你可能会停留的画面，evidence_clarity 说明本次选择与这些参考评分的关系。"
            "只描述这一次的图片偏好，不是人格测评。")
    block["evidence_scope"] += (
        " The reference raters of this image set do not represent the population as a whole."
        if english else "参考评分只代表这批图片的参考评分者，不能泛化为所有人的口味。")
    from .portrait import build_reading_intent, portrait_style
    block["reading_intent"] = build_reading_intent(profile, deck, language)
    block["style"] = portrait_style(language)
    context["canonical_block"] = canonical_json(block)
    context["context_hash"] = content_hash(block)
    if finale is not None:
        from .portrait import apply_last_card_context
        return apply_last_card_context(context, profile, deck, finale, language)
    return context


def validated(journal, deck, *, require_complete=True):
    replay = replay_events(journal, cards=deck.cards, require_complete=require_complete)
    for event in replay["journal"]["events"]:
        if event["type"] == "round_started" and event.get("display", {}).get("deck") != deck.label:
            raise ValueError("This record belongs to another deck or deck version")
    return replay


def card_contribution(card, weights, deck):
    features = list(deck.x2[card])
    terms = [weight * value for weight, value in zip(weights, features)]
    return {"card_id": card, "features": features, "terms": terms,
            "population": terms[0], "content": sum(terms[1:]), "score": sum(terms)}


def contribution_ledger(profile, deck):
    """All nine terms sum in model order to each effective utility, without rounding."""
    preference = profile["analysis"]["preference"]
    weights = list(preference["effective"])
    result = {"schema_version": LEDGER_VERSION, "algorithm_version": profile["algorithm_version"],
              "deck": deck.label, "profile_hash": profile["profile_hash"],
              "events_hash": profile["events_hash"], "feature_names": list(FEATURE_NAMES),
              "layer": preference["status"], "weights": weights,
              "candidate": list(preference["candidate"][:-1]),
              "candidate_stop_bias": preference["candidate"][-1],
              "log_evalues": deepcopy(preference["log_evalues"]),
              "log_threshold": preference["log_threshold"],
              "cards": [card_contribution(card, weights, deck) for card in deck.ids],
              "semantics": "Effective card utility = sum(terms), terms[i] = weights[i] * features[i]. "
                           "This is a score decomposition, not causal attribution of individual clicks. "
                           "Candidate weights do not bypass the evidence gate.",
              "numerical_contract": engine_module("numeric").NATIVE_CONTRACT}
    result["ledger_hash"] = content_hash(result)
    return result


def build_bundle(journal, deck):
    replay = validated(journal, deck)
    numeric, research = engine_module("numeric"), engine_module("research_deck")
    with numeric.use(numeric.NATIVE_CONTRACT):
        profile = research.build_profile(replay["journal"], deck, replay["events_hash"])
    return {"schema_version": REPLAY_VERSION, "journal": replay["journal"], "profile": profile,
            "contribution_ledger": contribution_ledger(profile, deck)}


def verify_bundle(document, deck=None):
    if type(document) is not dict or document.get("schema_version") != REPLAY_VERSION:
        raise ValueError("Unsupported research replay bundle")
    required = {"schema_version", "journal", "profile"}
    if not required <= set(document) or set(document) - required - {"contribution_ledger", "post_session"}:
        raise ValueError("Research replay bundle contains unexpected or missing fields")
    canonical_json(document)
    if type(document["journal"]) is not dict or type(document["journal"].get("events")) is not list:
        raise ValueError("Research replay needs an event journal")
    research = engine_module("research_deck")
    deck_name = research.deck_of(document["journal"])
    if deck is None:
        deck = research.load_deck(deck_name)
    if deck_name != deck.name:
        raise ValueError("This record belongs to another deck")
    rebuilt = build_bundle(document["journal"], deck)
    if canonical_json(document["profile"]) != canonical_json(rebuilt["profile"]):
        raise ValueError("Research profile does not match the recomputed event record")
    if "contribution_ledger" in document:
        if canonical_json(document["contribution_ledger"]) != canonical_json(rebuilt["contribution_ledger"]):
            raise ValueError("Contribution ledger does not match the recomputed effective scores")
    if "post_session" in document:
        profile = rebuilt["profile"]
        selected = [row["card_id"] for row in profile["analysis"]["attribution"]["selections"]]
        rebuilt["post_session"] = validate_post_session(document["post_session"],
            profile_hash=profile["profile_hash"], selected_card_ids=selected)
    return rebuilt


def trajectory(replay, deck):
    """One fit path; browser scrubbing changes the displayed state, never the model."""
    research, model, numeric = (engine_module(name) for name in ("research_deck", "research_model", "numeric"))
    rows = research.observations(replay["journal"])
    states = []
    with numeric.use(numeric.NATIVE_CONTRACT):
        state = model.Session(deck.x2, deck.context)

        def snapshot(row=None):
            weights = state.effective()
            scores = [model._dot(weights, deck.x2[card]) for card in deck.ids]
            score_of = dict(zip(deck.ids, scores))
            top = sorted(deck.ids, key=lambda card: (-score_of[card], card))[:6]
            picked = list(row["picks"]) if row else []
            listed = list(dict.fromkeys(top + picked))
            return {"round": row["round"] if row else 0,
                    "shown": list(row["shown"]) if row else [], "picked": picked, "picks": picked,
                    "unseen": list(row.get("unseen", [])) if row else [],
                    "scores": scores, "top": top, "weights": weights, "candidate": list(state.l2[:-1]),
                    "layer": state.layer(), "sensitivity": state.l1[0],
                    "log_evalues": {"E1": state.e1, "E2": state.e2},
                    "log_threshold": math.log(model.EVIDENCE_THRESHOLD),
                    "selection_count": len(picked), "observation_count": len(state.rows),
                    "contributions": [card_contribution(card, weights, deck) for card in listed]}

        states.append(snapshot())
        for row in rows:
            state.add(row)
            states.append(snapshot(row))
    events = replay["journal"]["events"]
    result = {"schema_version": TRAJECTORY_VERSION, "algorithm_version": research.ALGORITHM_VERSION,
            "model_version": research.ALGORITHM_VERSION, "deck": deck.label, "feature_version": deck.version,
            "feature_names": list(FEATURE_NAMES), "states": states, "rounds": len(rows),
            "cards": [{"id": card, "image": f"./assets/decks/{deck.name}/{card}.webp", "type": deck.cards[card].get("type", ""),
                       "u": deck.u[card], "x8": list(deck.x2[card][1:]),
                       "x": deck.x2[card][1], "y": deck.x2[card][2]} for card in deck.ids],
            "complete": replay["complete"], "open_round": bool(events) and events[-1]["type"] not in
                ("round_completed", "session_completed"), "events_hash": replay["events_hash"],
            "event_count": len(events), "provider_calls": 0, "numerical_contract": numeric.NATIVE_CONTRACT,
            "semantics": "Population utility until evidence supports sensitivity or personal deviation. "
                         "The content projection is fixed and is not a personality axis. "
                         "Evidence values are log e-values, not confidence probabilities. "
                         "Open rounds are excluded from fitting."}
    from .pamela_comparison import attach_to_trajectory
    return attach_to_trajectory(result, replay["journal"], deck)


def inspection(profile, journal, deck, *, context=None, prompt=None, calls=None, complete=True, compact=False):
    from .context import build_analysis_prompt
    from .runtime import request_metrics
    context = deepcopy(context) if context is not None else build_context(profile, deck)
    prompt = deepcopy(prompt) if prompt is not None else build_analysis_prompt(context)
    ledger = contribution_ledger(profile, deck)
    result = {"profile": deepcopy(profile), "journal": deepcopy(journal), "profile_hash": profile["profile_hash"],
              "complete": complete, "provisional": not complete, "preference": deepcopy(profile["analysis"]["preference"]),
              "attribution": deepcopy(profile["analysis"]["attribution"]), "contribution_ledger": ledger,
              "mode": profile["analysis"]["data_density"], "context": context, "prompt": prompt,
              "calls": deepcopy(calls or []), "provider_calls": 0,
              "metrics": request_metrics(journal, prompt, calls)}
    if compact:
        # Preserve v6's gate and reference fields; the legacy compact projector
        # deliberately has no knowledge of the research model.
        result.pop("journal")
        result.pop("prompt")
        result.pop("context")
        result["contribution_ledger"] = {key: value for key, value in ledger.items() if key != "cards"}
        result["transport"] = "norn.research-inspection.compact.v1"
    return result

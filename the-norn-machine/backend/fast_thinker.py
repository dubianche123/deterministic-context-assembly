"""
The Norn Machine — Step 1: Fast Thinker
Pure mathematical personality inference. Zero LLM involvement.

Historical scorer: scoring v4 (norn/profile.py choice_analysis) never calls it.
It runs only to replay profiles v1–v3 and for the legacy AWS Lambda handler.

Algorithm:
  1. Weight = round_decay × hesitation_bonus
     - round_decay: later rounds have more weight (user's intuition sharpens)
     - hesitation_bonus: tiny nudge only; load time can pollute raw duration
  2. Weighted average of MBTI 4D coordinates → raw archetype vector
  3. Sign → letter, magnitude → confidence
  4. Aggregate traits & motifs by frequency, weighted by same scheme
  5. Add choice-revision signals from cards the user selected and then cancelled
  6. Extract one optional word-level signature signal from weighted motifs
"""
import json
import hashlib
import math
from pathlib import Path
from collections import Counter

if __package__:
    from . import numeric
    from .ledger import ALGORITHM_VERSION, ATTRIBUTION_SCHEMA_VERSION, build_attribution
else:
    import numeric
    from ledger import ALGORITHM_VERSION, ATTRIBUTION_SCHEMA_VERSION, build_attribution

# Load card metadata once at cold start
_CARDS = None
DIMS = ("E_I", "S_N", "T_F", "J_P")

# Hesitation timing includes image/network/render wait on the frontend, so it must
# never dominate the actual choice signal.
HESITATION_MAX_BONUS = 1.12
HESITATION_MAX_DURATION_MS = 45_000
REVISION_MAX_JP_NUDGE = 2.4

def _load_cards():
    global _CARDS
    if _CARDS is None:
        p = Path(__file__).parent / "config" / "cards_metadata.json"
        with open(p, "r", encoding="utf-8") as f:
            _CARDS = json.load(f)
    return _CARDS


def fast_think_preferences(journals, *, include_open=False, config=None):
    """Learn relative image preference; the four-axis routine below is v1 replay.

    No personality labels or fixed four-axis coordinates enter this model.
    The model's predictions and validation describe choices, not personality.
    """
    if __package__:
        from . import preference_model
    else:
        import preference_model
    checkpoint = preference_model.train(journals, config=config, include_open=include_open)
    return {"model": checkpoint, "summary": preference_model.summarize(checkpoint)}


def fast_think(
    selections: list,
    total_rounds: int,
    round_durations: list = None,
    deselection_events: list = None,
    *,
    selection_event_ids: list = None,
    revision_event_ids: list = None,
) -> dict:
    """
    Args:
        selections: [{"id": "NORN_0042", "round": 1}, ...]
        total_rounds: how many rounds the user went through
        round_durations: [{"round": 1, "duration_ms": 8200}, ...]
                         duration from cards appearing to user clicking next/reveal
        deselection_events: [{"id": "NORN_0042", "round": 1}, ...]
                            selected cards the user later cancelled
        selection_event_ids / revision_event_ids: optional event IDs in input
                            order; snapshots receive stable derived IDs

    Returns:
        Structured analysis dict ready for template routing
    """
    cards = _load_cards()

    selection_ids = _event_ids("selection", selections, selection_event_ids)
    revision_inputs = deselection_events if isinstance(deselection_events, list) else []
    revision_ids = _event_ids("revision", revision_inputs, revision_event_ids)
    if set(selection_ids) & set(revision_ids):
        raise ValueError("Selection and revision event IDs must be globally unique")
    valid_revisions = _valid_choice_revisions(deselection_events)
    revision = _summarize_choice_revision(valid_revisions, total_rounds)
    revision_rows = [
        {"event_id": revision_ids[index], "card_id": event["id"], "round": event["round"]}
        for index, event in valid_revisions
    ]

    # ── Build duration lookup ──
    dur_map = {}
    raw_dur_map = {}
    if round_durations:
        for rd in round_durations:
            raw_duration = int(rd["duration_ms"])
            duration = max(0, min(raw_duration, HESITATION_MAX_DURATION_MS))
            dur_map[rd["round"]] = duration
            raw_dur_map[rd["round"]] = raw_duration

    # ── Compute hesitation percentiles ──
    # Normalize durations to [0, 1] range within the session
    # Then map to a small bonus multiplier: top hesitation → 1.12×, quickest → 1.0×
    hesitation_bonus = {}
    normalized_durations = {}
    if dur_map and len(dur_map) > 1:
        durations = list(dur_map.values())
        d_min = min(durations)
        d_max = max(durations)
        d_range = d_max - d_min if d_max > d_min else 1
        for rnd, dur in dur_map.items():
            normalized = (dur - d_min) / d_range   # 0 = fastest, 1 = slowest
            normalized_durations[rnd] = normalized
            hesitation_bonus[rnd] = 1.0 + normalized * (HESITATION_MAX_BONUS - 1.0)
    else:
        # Single round or no duration data → uniform bonus
        for rnd in range(1, total_rounds + 1):
            hesitation_bonus[rnd] = 1.0

    duration_normalization = {
        "count": len(dur_map),
        "min_ms": min(dur_map.values()) if dur_map else None,
        "max_ms": max(dur_map.values()) if dur_map else None,
        "range_ms": (max(dur_map.values()) - min(dur_map.values())) if dur_map else None,
        "clamp_min_ms": 0,
        "clamp_max_ms": HESITATION_MAX_DURATION_MS,
        "maximum_multiplier": HESITATION_MAX_BONUS,
    }
    round_rows = []
    for rnd in sorted(set(range(1, total_rounds + 1)) | set(dur_map)):
        if rnd not in dur_map:
            normalization = "missing_duration"
        elif len(dur_map) <= 1:
            normalization = "insufficient_durations"
        elif duration_normalization["range_ms"] == 0:
            normalization = "equal_durations"
        else:
            normalization = "session_min_max"
        round_rows.append({
            "round": rnd,
            "raw_duration_ms": raw_dur_map.get(rnd),
            "clamped_duration_ms": dur_map.get(rnd),
            "duration_was_clamped": rnd in dur_map and raw_dur_map[rnd] != dur_map[rnd],
            "normalization": normalization,
            "normalized_duration": normalized_durations.get(rnd),
            "hesitation_multiplier": hesitation_bonus.get(rnd, 1.0),
        })

    # ── Compute per-selection weights ──
    # round_decay: exponential growth so later rounds dominate
    # w(r) = e^(r / total_rounds) normalized
    weighted_coords = {dim: 0.0 for dim in DIMS}
    total_weight = 0.0
    total_base_weight = 0.0
    selection_rows = []
    trait_counter = Counter()
    motif_counter = Counter()
    motif_coord_sums = {}
    weighted_selections = []
    scene_list = []

    for index, sel in enumerate(selections):
        card_id = sel["id"]
        rnd = sel["round"]

        if card_id not in cards:
            continue

        card = cards[card_id]

        base_weight, hesitation_multiplier, w = _selection_weight_factors(
            rnd, total_rounds, hesitation_bonus,
        )
        weighted_selections.append((w, card_id))
        selection_weighted_coords = {dim: card["coords"][dim] * w for dim in DIMS}
        selection_rows.append({
            "event_id": selection_ids[index],
            "card_id": card_id,
            "round": rnd,
            "coords": dict(card["coords"]),
            "base_weight": base_weight,
            "hesitation_multiplier": hesitation_multiplier,
            "weight": w,
            "weighted_coords": selection_weighted_coords,
        })

        # Accumulate weighted coordinates
        for dim in weighted_coords:
            weighted_coords[dim] += selection_weighted_coords[dim]

        total_weight += w
        total_base_weight += base_weight

        # Accumulate layer 2 data (also weighted)
        for trait in card["traits"]:
            trait_counter[trait] += w
        for motif in card["motifs"]:
            motif_counter[motif] += w
            motif_sums = motif_coord_sums.setdefault(
                motif,
                {"weight": 0.0, "coords": {dim: 0.0 for dim in DIMS}},
            )
            motif_sums["weight"] += w
            for dim in DIMS:
                motif_sums["coords"][dim] += card["coords"][dim] * w

        scene_list.append(card["scene"])

    # ── Compute averages ──
    if total_weight == 0:
        # Edge case: user selected nothing
        result = _empty_result(total_rounds, revision)
        result["attribution"] = build_attribution(
            total_rounds=total_rounds, total_weight=total_weight,
            total_base_weight=total_base_weight, rounds=round_rows,
            duration_normalization=duration_normalization, selections=selection_rows,
            revision_events=revision_rows, revision=revision,
            max_jp_nudge=REVISION_MAX_JP_NUDGE, actual_jp_delta=0.0,
            pre_revision=dict(result["coords_avg"]),
            unrounded_final=dict(result["coords_avg"]), final=dict(result["coords_avg"]),
        )
        return result

    coords_avg = {dim: v / total_weight for dim, v in weighted_coords.items()}
    pre_revision = dict(coords_avg)
    revision_jp_nudge = revision["strength"] * REVISION_MAX_JP_NUDGE
    coords_avg["J_P"] -= revision_jp_nudge

    # ── Derive MBTI type ──
    mbti_letters = {
        "E_I": ("E", "I"),
        "S_N": ("N", "S"),  # positive = N
        "T_F": ("T", "F"),  # positive = T
        "J_P": ("J", "P"),  # positive = J
    }

    mbti_type = ""
    confidence = {}
    for dim in ["E_I", "S_N", "T_F", "J_P"]:
        val = coords_avg[dim]
        pos_letter, neg_letter = mbti_letters[dim]
        letter = pos_letter if val >= 0 else neg_letter
        mbti_type += letter
        # Confidence = how far from the decision boundary (0)
        # Normalize: |val| / 10, clamp to [0, 1]
        confidence[dim] = min(abs(val) / 10.0, 1.0)

    # ── Determine data density mode ──
    if total_rounds <= 5:
        density = "sparse_mode"
    elif total_rounds <= 20:
        density = "dense_mode"
    else:
        density = "overflow_mode"

    # ── Rank traits and motifs ──
    if revision["total"] > 0:
        revision_weight = total_weight * (0.25 + revision["strength"] * 0.75)
        trait_counter["反复校准"] += revision_weight
        trait_counter["选择复核"] += revision_weight * 0.8
        if revision["signal"] in ("active", "high"):
            trait_counter["决策摇摆"] += revision_weight * 0.6

    top_traits = [t for t, _ in trait_counter.most_common(8)]
    top_motifs = [m for m, _ in motif_counter.most_common(8)]

    # ── Collect rationale snippets from most-weighted cards ──
    # Sort selections by their computed weight, pick top N rationales
    weighted_selections.sort(key=lambda item: item[0], reverse=True)
    rationale_snippets = []
    seen = set()
    for _, cid in weighted_selections[:6]:
        r = cards[cid]["rationale"]
        if r not in seen:
            rationale_snippets.append(r)
            seen.add(r)

    # ── Signature Signal: the single most specific, concrete item ──
    signature_signal = _pick_signature_signal(motif_counter, motif_coord_sums, coords_avg)

    final_coords = {k: round(v, 2) for k, v in coords_avg.items()}
    attribution = build_attribution(
        total_rounds=total_rounds, total_weight=total_weight,
        total_base_weight=total_base_weight, rounds=round_rows,
        duration_normalization=duration_normalization, selections=selection_rows,
        revision_events=revision_rows, revision=revision,
        max_jp_nudge=REVISION_MAX_JP_NUDGE, actual_jp_delta=-revision_jp_nudge,
        pre_revision=pre_revision, unrounded_final=coords_avg, final=final_coords,
    )
    return {
        "mbti_type": mbti_type,
        "confidence": confidence,
        "coords_avg": final_coords,
        "top_traits": top_traits,
        "top_motifs": top_motifs,
        "scenes": scene_list,
        "rationale_snippets": rationale_snippets,
        "signature_signal": signature_signal,
        "data_density": density,
        "total_selections": len(selections),
        "total_rounds": total_rounds,
        "choice_revision": revision,
        "attribution": attribution,
    }


def _build_choice_revision(deselection_events, total_rounds):
    return _summarize_choice_revision(_valid_choice_revisions(deselection_events), total_rounds)


def _valid_choice_revisions(deselection_events):
    valid_events = []
    if isinstance(deselection_events, list):
        for index, event in enumerate(deselection_events):
            if not isinstance(event, dict):
                continue
            try:
                rnd = int(event.get("round", 0))
            except (TypeError, ValueError):
                continue
            if rnd > 0:
                valid_events.append((index, {"id": str(event.get("id", "")), "round": rnd}))
    return valid_events


def _summarize_choice_revision(valid_events, total_rounds):

    total = len(valid_events)
    rounds_touched = len({event["round"] for _, event in valid_events})
    rate = total / max(int(total_rounds or 1), 1)
    strength = min(rate / 1.5, 1.0)

    if total == 0:
        signal = "none"
    elif strength < 0.25:
        signal = "light"
    elif strength < 0.6:
        signal = "active"
    else:
        signal = "high"

    return {
        "total": total,
        "rounds_touched": rounds_touched,
        "rate": round(rate, 2),
        "strength": round(strength, 2),
        "signal": signal,
    }


def _selection_weight(round_number, total_rounds, hesitation_bonus):
    return _selection_weight_factors(round_number, total_rounds, hesitation_bonus)[2]


def _selection_weight_factors(round_number, total_rounds, hesitation_bonus):
    round_decay = numeric.exp(round_number / max(total_rounds, 1))
    multiplier = hesitation_bonus.get(round_number, 1.0)
    return round_decay, multiplier, round_decay * multiplier


def _event_ids(kind, entries, provided):
    if provided is not None:
        if (not isinstance(provided, list) or len(provided) != len(entries)
                or any(not isinstance(item, str) or not item for item in provided)
                or len(set(provided)) != len(provided)):
            raise ValueError(f"{kind}_event_ids must uniquely identify each input item")
        return list(provided)
    ids = []
    for index, entry in enumerate(entries):
        canonical = json.dumps(entry, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
        ids.append(f"legacy.{kind}.{index + 1}.{digest}")
    return ids


def _cosine_similarity(a, b):
    mag_a = numeric.sqrt(sum(a.get(dim, 0) ** 2 for dim in DIMS))
    mag_b = numeric.sqrt(sum(b.get(dim, 0) ** 2 for dim in DIMS))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    dot = sum(a.get(dim, 0) * b.get(dim, 0) for dim in DIMS)
    return dot / (mag_a * mag_b)


def _pick_signature_signal(motif_counter, motif_coord_sums, coords_avg):
    """Select the single most iconic concrete motif that best represents this player.

    Algorithm:
      1. For each motif that appeared, compute a relevance score:
         relevance = frequency_weight × alignment_score
      2. frequency_weight: how often this motif appeared (weighted same as coords)
      3. alignment_score: how well the card(s) carrying this motif align with the
         player's final coords_avg (cosine similarity in 4D MBTI space)
      4. Return the top motif as the "signature signal" — the specific, concrete
         item that the LLM is encouraged to mention by name.
    """
    if not motif_counter or not motif_coord_sums:
        return None

    best_motif = None
    best_score = -1

    for motif, sums in motif_coord_sums.items():
        freq_weight = motif_counter.get(motif, 0)
        total_w = sums["weight"]
        if total_w == 0:
            continue

        motif_avg = {
            dim: sums["coords"][dim] / total_w
            for dim in DIMS
        }
        alignment = _cosine_similarity(motif_avg, coords_avg)

        # Final score: frequency × (1 + alignment) to favor both popular and aligned
        score = freq_weight * (1 + max(alignment, 0))

        if score > best_score:
            best_score = score
            best_motif = motif

    return best_motif


def _empty_result(total_rounds, revision=None):
    return {
        "mbti_type": "XXXX",
        "confidence": {"E_I": 0, "S_N": 0, "T_F": 0, "J_P": 0},
        "coords_avg": {"E_I": 0, "S_N": 0, "T_F": 0, "J_P": 0},
        "top_traits": [],
        "top_motifs": [],
        "scenes": [],
        "rationale_snippets": [],
        "signature_signal": None,
        "data_density": "sparse_mode",
        "total_selections": 0,
        "total_rounds": total_rounds,
        "choice_revision": revision or _build_choice_revision([], total_rounds),
    }

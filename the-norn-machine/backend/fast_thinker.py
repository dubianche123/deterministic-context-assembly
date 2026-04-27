"""
The Norn Machine — Step 1: Fast Thinker
Pure mathematical personality inference. Zero LLM involvement.

Algorithm:
  1. Weight = round_decay × hesitation_bonus
     - round_decay: later rounds have more weight (user's intuition sharpens)
     - hesitation_bonus: tiny nudge only; load time can pollute raw duration
  2. Weighted average of MBTI 4D coordinates → raw archetype vector
  3. Sign → letter, magnitude → confidence
  4. Aggregate traits & motifs by frequency, weighted by same scheme
  5. Add choice-revision signals from cards the user selected and then cancelled
"""
import json
import math
from pathlib import Path
from collections import Counter

# Load card metadata once at cold start
_CARDS = None

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


def fast_think(
    selections: list,
    total_rounds: int,
    round_durations: list = None,
    deselection_events: list = None,
) -> dict:
    """
    Args:
        selections: [{"id": "NORN_0042", "round": 1}, ...]
        total_rounds: how many rounds the user went through
        round_durations: [{"round": 1, "duration_ms": 8200}, ...]
                         duration from cards appearing to user clicking next/reveal
        deselection_events: [{"id": "NORN_0042", "round": 1}, ...]
                            selected cards the user later cancelled

    Returns:
        Structured analysis dict ready for template routing
    """
    cards = _load_cards()

    revision = _build_choice_revision(deselection_events, total_rounds)

    # ── Build duration lookup ──
    dur_map = {}
    if round_durations:
        for rd in round_durations:
            duration = max(0, min(int(rd["duration_ms"]), HESITATION_MAX_DURATION_MS))
            dur_map[rd["round"]] = duration

    # ── Compute hesitation percentiles ──
    # Normalize durations to [0, 1] range within the session
    # Then map to a small bonus multiplier: top hesitation → 1.12×, quickest → 1.0×
    hesitation_bonus = {}
    if dur_map and len(dur_map) > 1:
        durations = list(dur_map.values())
        d_min = min(durations)
        d_max = max(durations)
        d_range = d_max - d_min if d_max > d_min else 1
        for rnd, dur in dur_map.items():
            normalized = (dur - d_min) / d_range   # 0 = fastest, 1 = slowest
            hesitation_bonus[rnd] = 1.0 + normalized * (HESITATION_MAX_BONUS - 1.0)
    else:
        # Single round or no duration data → uniform bonus
        for rnd in range(1, total_rounds + 1):
            hesitation_bonus[rnd] = 1.0

    # ── Compute per-selection weights ──
    # round_decay: exponential growth so later rounds dominate
    # w(r) = e^(r / total_rounds) normalized
    weighted_coords = {"E_I": 0.0, "S_N": 0.0, "T_F": 0.0, "J_P": 0.0}
    total_weight = 0.0
    trait_counter = Counter()
    motif_counter = Counter()
    scene_list = []

    for sel in selections:
        card_id = sel["id"]
        rnd = sel["round"]

        if card_id not in cards:
            continue

        card = cards[card_id]

        # Weight = round_decay × hesitation_bonus
        round_decay = math.exp(rnd / max(total_rounds, 1))
        h_bonus = hesitation_bonus.get(rnd, 1.0)
        w = round_decay * h_bonus

        # Accumulate weighted coordinates
        for dim in weighted_coords:
            weighted_coords[dim] += card["coords"][dim] * w

        total_weight += w

        # Accumulate layer 2 data (also weighted)
        for trait in card["traits"]:
            trait_counter[trait] += w
        for motif in card["motifs"]:
            motif_counter[motif] += w

        scene_list.append(card["scene"])

    # ── Compute averages ──
    if total_weight == 0:
        # Edge case: user selected nothing
        return _empty_result(total_rounds, revision)

    coords_avg = {dim: v / total_weight for dim, v in weighted_coords.items()}
    coords_avg["J_P"] -= revision["strength"] * REVISION_MAX_JP_NUDGE

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
    sel_with_weight = []
    for sel in selections:
        cid = sel["id"]
        rnd = sel["round"]
        if cid in cards:
            w = math.exp(rnd / max(total_rounds, 1)) * hesitation_bonus.get(rnd, 1.0)
            sel_with_weight.append((w, cid))
    sel_with_weight.sort(reverse=True)
    rationale_snippets = []
    seen = set()
    for _, cid in sel_with_weight[:6]:
        r = cards[cid]["rationale"]
        if r not in seen:
            rationale_snippets.append(r)
            seen.add(r)

    # ── Signature Signal: the single most specific, concrete item ──
    signature_signal = _pick_signature_signal(
        selections, cards, coords_avg, motif_counter, hesitation_bonus, total_rounds
    )

    return {
        "mbti_type": mbti_type,
        "confidence": confidence,
        "coords_avg": {k: round(v, 2) for k, v in coords_avg.items()},
        "top_traits": top_traits,
        "top_motifs": top_motifs,
        "scenes": scene_list,
        "rationale_snippets": rationale_snippets,
        "signature_signal": signature_signal,
        "data_density": density,
        "total_selections": len(selections),
        "total_rounds": total_rounds,
        "choice_revision": revision,
    }


def _build_choice_revision(deselection_events, total_rounds):
    valid_events = []
    if isinstance(deselection_events, list):
        for event in deselection_events:
            if not isinstance(event, dict):
                continue
            try:
                rnd = int(event.get("round", 0))
            except (TypeError, ValueError):
                continue
            if rnd > 0:
                valid_events.append({"id": str(event.get("id", "")), "round": rnd})

    total = len(valid_events)
    rounds_touched = len({event["round"] for event in valid_events})
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


def _pick_signature_signal(
    selections, cards, coords_avg, motif_counter, hesitation_bonus, total_rounds
):
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
    if not selections or not motif_counter:
        return None

    # Build motif → list of card coords that contributed it
    motif_cards = {}  # motif → [(card_coords, weight)]
    for sel in selections:
        cid = sel["id"]
        rnd = sel["round"]
        if cid not in cards:
            continue
        card = cards[cid]
        w = math.exp(rnd / max(total_rounds, 1)) * hesitation_bonus.get(rnd, 1.0)
        for motif in card["motifs"]:
            motif_cards.setdefault(motif, []).append((card["coords"], w))

    # Compute alignment score for each motif
    dims = ["E_I", "S_N", "T_F", "J_P"]
    best_motif = None
    best_score = -1

    for motif, card_entries in motif_cards.items():
        freq_weight = motif_counter.get(motif, 0)

        # Weighted average coords of cards carrying this motif
        total_w = sum(w for _, w in card_entries)
        if total_w == 0:
            continue
        motif_avg = {}
        for dim in dims:
            motif_avg[dim] = sum(c[dim] * w for c, w in card_entries) / total_w

        # Cosine similarity between motif_avg and player coords_avg
        dot = sum(motif_avg[d] * coords_avg[d] for d in dims)
        mag_a = math.sqrt(sum(motif_avg[d] ** 2 for d in dims)) or 1
        mag_b = math.sqrt(sum(coords_avg[d] ** 2 for d in dims)) or 1
        alignment = dot / (mag_a * mag_b)

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

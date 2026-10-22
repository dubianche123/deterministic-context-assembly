"""Synthetic consented records for checking the measurement report.

Each simulated browser has its own preference weights over the 12 choice
features and chooses with the same sequential logit the model assumes.
`stability` mixes a visitor's weights between visits (1 keeps the same
person, 0 makes every visit a stranger), so the report's cross-visit numbers
can be checked against a known truth and the needed sample size estimated.
`stop_habit` gives each browser its own lasting stop tendency (the sd of a
per-person shift of the stop bias); 0 keeps the stop bias a fresh draw per
visit, shared by everyone.

By default people choose exactly as the scoring model assumes, so recovering
them shows only that planted structure can be learned back. The other settings
break that assumption on purpose, to stress-test model comparisons:
- `truth`: what people's taste is about. "features" (default) are the model's
  own 12 card features; "motif" likes recurring subjects (12 drawn from motifs on
  at least 3 cards); "pixel" is a direction in the 64 raw pixel statistics, which
  the model sees only through 8 components; "mixed" adds features and motifs.
- `process`: "logit" (default) picks in order of noisy utility as the model
  assumes; "threshold" picks every card whose noisy utility clears a personal
  bar, clicked in random order, then stops.
- `position_bias`: utility bonus for earlier display slots, falling linearly to
  0 at the last slot. `fatigue`: the stop bias grows by this much every 10 rounds.
- `probe`: a probe protocol (e.g. "new-material-v1") adds the pilot's new-image
  phase after each visit, as a norn.probe journal. New images carry no type or
  motifs, so only the pixel part of a taste reaches them: the 8 pixel weights for
  "features" and "mixed", the raw-pixel direction for "pixel", nothing for "motif".
Simulated records are labelled `deployment: "simulated"` and are never real data.
"""

import json
import math
from pathlib import Path
import random

from .core import engine_module

TRUTHS = ("features", "motif", "pixel", "mixed")
PROCESSES = ("logit", "threshold")
LIKED_MOTIFS = 12


def _journal(rounds):
    rows = []
    for number, (shown, picks) in enumerate(rounds, 1):
        rows.append({"type": "round_started", "round": number, "card_ids": shown})
        rows += [{"type": "card_selected", "round": number, "card_id": card} for card in picks]
        rows.append({"type": "round_completed", "round": number, "duration_ms": 1500 + 700 * len(picks)})
    rows.append({"type": "session_completed"})
    return {"schema_version": "norn.events.v1",
            "events": [{"event_id": f"evt-{n:06}", "sequence": n, **row} for n, row in enumerate(rows, 1)]}


def _card_spaces(catalog):
    """Motif sets and z-scored raw pixel statistics per card, for tastes outside the model's features."""
    cards = engine_module("fast_thinker")._load_cards()
    counts = {}
    for card in catalog.ids:
        for motif in set(cards[card]["motifs"]):
            counts[motif] = counts.get(motif, 0) + 1
    common = sorted(motif for motif, count in counts.items() if count >= 3)
    path = Path(engine_module("choice_model").__file__).parent / "config" / "preference_features.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    columns = [i for i, name in enumerate(document["feature_names"]) if name.startswith("pixel.")]
    raw = {card: [document["cards"][card][i] for i in columns] for card in catalog.ids}
    stats = []
    for k in range(len(columns)):
        values = [raw[card][k] for card in catalog.ids]
        mean = math.fsum(values) / len(values)
        sd = math.sqrt(math.fsum((value - mean) ** 2 for value in values) / len(values)) or 1.0
        stats.append((mean, sd))
    pixels = {card: [(value - mean) / sd for value, (mean, sd) in zip(raw[card], stats)] for card in catalog.ids}
    return {card: set(cards[card]["motifs"]) for card in catalog.ids}, common, pixels, stats


def _taste(rng, catalog, truth, weights, scale, spaces):
    """Card utility for one visit under the chosen kind of taste."""
    def features(card):
        return math.fsum(w * x for w, x in zip(weights, catalog.features[catalog.index[card]]))
    if truth == "features":
        return features
    motifs, common, pixels, _ = spaces
    if truth in ("motif", "mixed"):
        liked = set(rng.sample(common, LIKED_MOTIFS))
        def by_motif(card):
            return scale * len(motifs[card] & liked)
        return by_motif if truth == "motif" else (lambda card: features(card) + by_motif(card))
    direction = [rng.gauss(0, 1) for _ in range(len(next(iter(pixels.values()))))]
    norm = math.sqrt(math.fsum(value * value for value in direction))
    direction = [scale * value / norm * 2 for value in direction]  # unit direction, utility sd about 2 * scale

    def by_pixels(card):
        return math.fsum(w * x for w, x in zip(direction, pixels[card]))
    by_pixels.direction = direction
    return by_pixels


def _probe_journal(rng, deck, utility, bar, process):
    """The pilot's new-image phase: two whole scenes per round, both styles, random positions."""
    scenes = sorted({card["scene"] for card in deck["cards"].values()})
    rng.shuffle(scenes)
    per = deck["design"]["scenes_per_round"]
    rows, number = [], 0
    for start in range(0, len(scenes), per):
        number += 1
        shown = [card for card in sorted(deck["cards"]) if deck["cards"][card]["scene"] in scenes[start:start + per]]
        rng.shuffle(shown)
        values = {card: utility(card) for card in shown}
        if process == "threshold":
            picks = _choose_threshold(rng, values, bar, shown)
        else:
            picks, _ = _choose(rng, None, None, bar, shown, values.get)
        rows.append({"type": "round_started", "round": number, "card_ids": shown})
        rows.append({"type": "round_ready", "round": number, "ready_offset_ms": 0, "unloaded_card_ids": [],
                     "layout": None})
        rows += [{"type": "card_selected", "round": number, "card_id": card, "t_ms": 800 * (k + 1), "input": None,
                  "layout": None} for k, card in enumerate(picks)]
        rows.append({"type": "round_completed", "round": number, "duration_ms": 800 * (len(picks) + 1),
                     "hidden_ms": 0, "seen_card_ids": None, "hover": None})
    rows.append({"type": "session_completed"})
    return {"schema_version": "norn.probe.v1", "protocol": deck["protocol"], "catalog_version": deck["catalog_version"],
            "events": [{"event_id": f"probe-{n:04}", "sequence": n, **row} for n, row in enumerate(rows, 1)]}


def _choose_threshold(rng, utility, bar, shown):
    """Every card whose noisy utility clears the bar, clicked in random order."""
    noisy = {card: utility[card] - math.log(-math.log(rng.random())) for card in shown}
    picks = [card for card in shown if noisy[card] > bar]
    rng.shuffle(picks)
    return picks


def _choose(rng, catalog, weights, bias, shown, value=None):
    utility = {card: (value(card) if value else
                      math.fsum(w * x for w, x in zip(weights, catalog.features[catalog.index[card]])))
               for card in shown}
    alive, picks = list(shown), []
    while True:
        options = [utility[card] for card in alive] + [bias]
        top = max(options)
        point = rng.random() * math.fsum(math.exp(value - top) for value in options)
        for index, value in enumerate(options):
            point -= math.exp(value - top)
            if point <= 0:
                break
        if index == len(alive):
            return picks, utility
        picks.append(alive.pop(index))


def simulate_records(visitors=40, *, visits=(1, 3), rounds=(6, 14), stability=1.0, scale=1.5,
                     stop_habit=0.0, truth="features", process="logit", position_bias=0.0, fatigue=0.0,
                     probe=None, arms=None, rating_effect=0.0, seed=0):
    """Return export-shaped records; `arms` = (experiment, (control, variant)) adds an A/B split
    where the variant shifts the latent rating by `rating_effect`."""
    if truth not in TRUTHS or process not in PROCESSES:
        raise ValueError(f"truth must be one of {TRUTHS} and process one of {PROCESSES}")
    model = engine_module("choice_model")
    catalog = model.load_catalog()
    spaces = _card_spaces(catalog) if truth != "features" or probe else None
    if probe:
        from .probe import load_probe_catalog
        new_deck = load_probe_catalog(probe)
        new_pixels = {card: [(value - mean) / sd for value, (mean, sd) in zip(row["pixel64"], spaces[3])]
                      for card, row in new_deck["cards"].items()}
    rng = random.Random(seed)
    records = []
    for person in range(visitors):
        visitor = f"{person:032x}"
        base = [rng.gauss(0, scale) for _ in range(12)]
        habit = rng.gauss(0, stop_habit) if stop_habit else 0.0  # no extra draw: older seeds replay unchanged
        arm = None
        if arms:
            arm = arms[1][rng.randrange(len(arms[1]))]
        for visit in range(1, rng.randint(*visits) + 1):
            weights = [stability * w + math.sqrt(1 - stability ** 2) * rng.gauss(0, scale) for w in base]
            bias = rng.gauss(0.5, 0.3) + habit
            # Default settings draw nothing extra, so earlier seeds replay unchanged.
            value = None if truth == "features" else _taste(rng, catalog, truth, weights, scale, spaces)
            played, chosen = [], {}
            deck = rng.sample(catalog.ids, len(catalog.ids))  # like the app: no card twice in a visit
            for number in range(rng.randint(*rounds)):
                shown = [deck.pop() for _ in range(rng.randint(4, 6))]
                stop = bias + fatigue * number / 10
                base_value = value
                if position_bias:
                    slots = {card: position_bias * (1 - slot / (len(shown) - 1)) for slot, card in enumerate(shown)}
                    own = value or (lambda card: math.fsum(
                        w * x for w, x in zip(weights, catalog.features[catalog.index[card]])))
                    base_value = lambda card, own=own, slots=slots: own(card) + slots[card]  # noqa: E731
                if process == "threshold":
                    own = base_value or (lambda card: math.fsum(
                        w * x for w, x in zip(weights, catalog.features[catalog.index[card]])))
                    utility = {card: own(card) for card in shown}
                    picks = _choose_threshold(rng, utility, stop, shown)
                else:
                    picks, utility = _choose(rng, catalog, weights, stop, shown, base_value)
                played.append((shown, picks))
                chosen.update({card: utility[card] for card in picks})
            if probe:
                if truth == "pixel":
                    direction = value.direction
                    reach = lambda card: math.fsum(w * x for w, x in zip(direction, new_pixels[card]))  # noqa: E731
                elif truth == "motif":
                    reach = lambda card: 0.0  # noqa: E731 - new images have no motifs
                else:
                    reach = lambda card: math.fsum(  # noqa: E731 - only the pixel weights reach new images
                        w * model.PIXEL_SCALE * x for w, x in zip(weights[4:], new_deck["cards"][card]["pixel8"]))
                probe_journal = _probe_journal(rng, new_deck, reach, bias, process)
            lift = rating_effect if arms and arm == arms[1][-1] else 0.0
            post = {"reading_rating": min(5, max(1, round(rng.gauss(3.6 + lift, 0.9))))}
            if chosen:
                favourite = max(chosen, key=chosen.get)
                post["closest_card_id"] = favourite if rng.random() < 0.6 else rng.choice(sorted(chosen))
            records.append({
                "schema_version": "norn.record.v2", "record_id": f"{len(records):032x}",
                "visitor_id": visitor, "visit": visit, "deployment": "simulated",
                "created_day": f"2026-11-{min(28, visit * 3):02}",
                "experiments": {arms[0]: arm} if arms else {},
                "journal": _journal(played),
                "annotations": {"post_session": post,
                                "engagement": {"dialogue_turns": rng.choice((0, 0, 1, 2, 3)), "max_turns": 3}},
                **({"probe": probe_journal} if probe else {}),
            })
    return records

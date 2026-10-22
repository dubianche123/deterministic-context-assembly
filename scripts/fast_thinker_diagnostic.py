#!/usr/bin/env python3
"""Reproducible offline diagnosis; no provider, credential or production changes.

See docs/fast-thinker-diagnostic-protocol.md for the frozen design. This uses
the shipped predictors as-is, and deliberately includes failure controls.
"""

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from norn.benchmark import default_journal
from norn.core import engine_module
from norn.events import content_hash, replay_events
from norn.fast_compare import _steps, _summary, prepare_experiment
from norn.fast_thinkers import LocalPredictor, STOP, prepare_local
from norn.simulate import _card_spaces, _choose, _choose_threshold, _journal, _taste

SEEDS = (11, 29, 47)
VISITORS_PER_SEED = 40
PREFIXES = (4, 8, 16, 24, 32)
CELLS = ("null_threshold", "position_only", "pixel_threshold", "motif_threshold",
         "pixel_reversal", "features_logit", "features_threshold")
MODES = ("first", "full")
NAMES = ("heuristic", "ml", "stop_only", "uniform")
METRICS = ("all_nll", "cards_nll", "within_type_nll", "stop_binary_nll")
PROTOCOL = ROOT / "docs/fast-thinker-diagnostic-protocol.md"


def mean(values):
    return math.fsum(values) / len(values) if values else None


def prefix_of(journal, count):
    closes = [i for i, event in enumerate(journal["events"]) if event["type"] == "round_completed"]
    return {"schema_version": journal["schema_version"],
            "events": deepcopy(journal["events"][:closes[count - 1] + 1])}


def predictors(prefix, rows, catalog):
    result = prepare_local(prefix, rows)
    result["uniform"] = LocalPredictor(dict.fromkeys(catalog.ids, 0), 0, {}, 0)
    return result


def score(rows, fitted, catalog, *, first_only):
    decisions = _steps(rows, first_only=first_only)
    if not decisions:
        return {name: {**dict.fromkeys((*METRICS, "top1_accuracy", "brier")),
                       "decision_count": 0, "cards_count": 0, "within_type_count": 0}
                for name in fitted}
    result = {}
    for name, predictor in fitted.items():
        binary = []
        for decision in decisions:
            probabilities = predictor.predict(decision["candidate_ids"])
            decision["predictions"][name] = {"status": "ok", "probabilities": probabilities}
            # Sum cards directly, rather than 1-p_stop, avoiding cancellation.
            mass = (probabilities[STOP] if decision["target"] == STOP else
                    math.fsum(probabilities[c] for c in decision["candidate_ids"]))
            binary.append(-math.log(max(mass, 1e-12)))
        summary = _summary(decisions, name, catalog)
        result[name] = {key: summary[key] for key in
                        (*METRICS[:-1], "decision_count", "cards_count", "within_type_count",
                         "top1_accuracy", "brier")}
        result[name]["stop_binary_nll"] = mean(binary)
        # This decomposition has different denominators, so retain counts.
        total = result[name]["decision_count"]
        card_count = result[name]["cards_count"]
        if total:
            reconstructed = result[name]["stop_binary_nll"] + (
                card_count / total * (result[name]["cards_nll"] or 0))
            if abs(reconstructed - result[name]["all_nll"]) > 1e-10:
                raise AssertionError("STOP / conditional-card loss does not reconstruct all loss")
    return result


def distribution(rows, catalog):
    shown = [card for row in rows for card in row["shown"]]
    picked = [card for row in rows for card in row["picks"]]
    rejected = [card for row in rows for card in row["shown"] if card not in row["picks"]]

    def features(cards):
        return [mean([catalog.features[catalog.index[c]][k] for c in cards])
                for k in range(12)]

    twins = [(row["picks"][0], row["shown"][2]) for row in rows if row["picks"]]
    return {"rounds": [row["round"] for row in rows],
            "selected_type_counts": dict(Counter(catalog.types[catalog.index[c]] for c in picked)),
            "shown_type_counts": dict(Counter(catalog.types[catalog.index[c]] for c in shown)),
            "rejected_type_counts": dict(Counter(catalog.types[catalog.index[c]] for c in rejected)),
            "selected_feature_mean": features(picked), "shown_feature_mean": features(shown),
            "selected_minus_option2_feature_mean": [mean([
                catalog.features[catalog.index[a]][k] - catalog.features[catalog.index[b]][k]
                for a, b in twins]) for k in range(12)],
            "unique_shown_ids": sorted(set(shown)),
            "first_slot_target_count": sum(bool(r["picks"]) and r["picks"][0] == r["shown"][0] for r in rows),
            "same_axis_option0_option2_count": sum(
                catalog.features[catalog.index[r["shown"][0]]][:4] ==
                catalog.features[catalog.index[r["shown"][2]]][:4] for r in rows)}


def numerical_audit(rows, fitted, catalog):
    model = engine_module("choice_model")
    prepared = model.Prepared(rows, catalog)
    theta, _, iterations = model.fit(prepared)
    gradient, hessian = model._evaluate(theta, prepared, len(rows))
    h = 1e-5

    def posterior(t):
        loss, _, count = model._log_loss(t, prepared)
        return -count * loss - 0.5 * math.fsum(p * x * x for p, x in zip(model.PRIOR, t))

    gradient_errors, hessian_errors = [], []
    for column in range(len(theta)):
        plus, minus = theta[:], theta[:]
        plus[column] += h
        minus[column] -= h
        gradient_errors.append(abs((posterior(plus) - posterior(minus)) / (2 * h) - gradient[column]))
        gp, _ = model._evaluate(plus, prepared, len(rows))
        gm, _ = model._evaluate(minus, prepared, len(rows))
        hessian_errors.extend(abs((gp[r] - gm[r]) / (2 * h) + hessian[r][column])
                              for r in range(len(theta)))
    engine_loss, _, count = model._log_loss(theta, prepared)
    direct_loss = score(rows, fitted, catalog, first_only=False)["ml"]["all_nll"]
    # The fixture never has a forced stop; thus both denominators coincide.
    return {"iterations": iterations, "theta": theta, "engine_decisions": count,
            "max_abs_gradient": max(map(abs, gradient)), "finite_difference_step": h,
            "max_gradient_error": max(gradient_errors), "max_negative_hessian_error": max(hessian_errors),
            "initial_log_posterior": posterior([0.0] * len(theta)),
            "fitted_log_posterior": posterior(theta),
            "log_posterior_gain": posterior(theta) - posterior([0.0] * len(theta)),
            "engine_train_all_nll": engine_loss, "direct_train_all_nll": direct_loss,
            "adapter_minus_engine_nll": direct_loss - engine_loss}


def fixture_audit(catalog):
    journal = default_journal()
    prefix, train, _, _ = prepare_experiment(journal, train_rounds=8)
    model = engine_module("choice_model")
    rows = model.observations(journal, catalog)
    fitted = predictors(prefix, train, catalog)
    groups = {"train_1_8": rows[:8], "test_9_16": rows[8:16],
              "test_17_24": rows[16:], "test_9_24": rows[8:]}
    result = {"journal_hash": content_hash(journal), "prefix_hash": content_hash(prefix),
              "training_test_card_overlap": sorted(set(c for r in train for c in r["shown"]) &
                                                     set(c for r in rows[8:] for c in r["shown"])),
              "groups": {}, "numerical_audit": numerical_audit(train, fitted, catalog)}
    for label, group in groups.items():
        full = _steps(group)
        stop_rows = [step for step in full if step["target"] == STOP]
        stop_nll = {name: mean([-math.log(p.predict(d["candidate_ids"])[STOP]) for d in stop_rows])
                    for name, p in fitted.items()}
        odds = [math.log(fitted["ml"].predict(r["shown"])[r["picks"][0]] /
                         fitted["ml"].predict(r["shown"])[r["shown"][2]]) for r in group]
        result["groups"][label] = {"distribution": distribution(group, catalog),
                                    "first": score(group, fitted, catalog, first_only=True),
                                    "full": score(group, fitted, catalog, first_only=False),
                                    "subsequent_stop_nll": stop_nll,
                                    "ml_twin_mean_log_odds": mean(odds),
                                    "ml_twin_correct_count": sum(o > 0 for o in odds)}
    first = result["groups"]["test_9_24"]["first"]
    delta = lambda key: first["ml"][key] - first["heuristic"][key]
    result["first_loss_gap_identity_not_causal"] = {
        "stop_mass": delta("all_nll") - delta("cards_nll"),
        "type_mass_given_card": delta("cards_nll") - delta("within_type_nll"),
        "within_type": delta("within_type_nll"), "all": delta("all_nll")}
    permuted = deepcopy(journal)
    for event in permuted["events"]:
        if event["type"] == "round_started" and event["round"] > 8:
            event["card_ids"].reverse()
    pp, pt, pd, _ = prepare_experiment(permuted, train_rounds=8)
    original = _steps(rows[8:], first_only=True)
    max_delta = max(abs(p.predict(a["candidate_ids"])[card] - p.predict(b["candidate_ids"])[card])
                    for p in fitted.values() for a, b in zip(original, pd) for card in a["candidate_ids"])
    if pp != prefix or pt != train or [d["target"] for d in original] != [d["target"] for d in pd]:
        raise AssertionError("Reordering unexpectedly changed the prefix or labels")
    if max_delta > 1e-12:
        raise AssertionError("Content-only predictors unexpectedly depend on position")
    result["display_order_invariance"] = {"permuted_journal_hash": content_hash(permuted),
                                           "same_prefix_and_targets": True,
                                           "max_abs_probability_delta": max_delta}
    pp, pt, _, _ = prepare_experiment(journal, train_rounds=19)
    pf = predictors(pp, pt, catalog)
    result["default_cutoff_sensitivity_different_test_set"] = {
        "training_rounds": 19, "test_rounds": list(range(20, 25)),
        "first": score(rows[19:], pf, catalog, first_only=True),
        "full": score(rows[19:], pf, catalog, first_only=False)}
    return result


def stream(seed, person, purpose):
    digest = hashlib.sha256(f"norn-diagnostic-v1:{seed}:{person}:{purpose}".encode()).digest()
    return random.Random(int.from_bytes(digest, "big"))


def exposure(seed, person, catalog):
    rng = stream(seed, person, "exposure")
    deck = rng.sample(catalog.ids, len(catalog.ids))
    return [[deck.pop() for _ in range(rng.randint(4, 6))] for _ in range(40)]


def simulated_visit(cell, seed, person, catalog, spaces, shown_rounds):
    preference = stream(seed, person, "preference")
    weights = [preference.gauss(0, 1) for _ in range(12)]
    bar = stream(seed, person, "stop").gauss(0.5, 0.3)
    truth = "pixel" if cell in ("pixel_threshold", "pixel_reversal") else (
        "motif" if cell == "motif_threshold" else "features")
    value = _taste(preference, catalog, truth, weights, 1.5 if truth == "motif" else 1, spaces)
    if cell in ("null_threshold", "position_only"):
        value = lambda card: 0.0
    played, utilities = [], []
    for number, shown in enumerate(shown_rounds, 1):
        rng = stream(seed, person, f"response:{number}")
        sign = -1 if cell == "pixel_reversal" and number > 32 else 1
        utility = {card: sign * value(card) for card in shown}
        utilities.extend(utility.values())
        if cell == "position_only":
            picks = [shown[0]]
        elif cell == "features_logit":
            picks, _ = _choose(rng, catalog, weights, bar, shown, utility.get)
        else:
            picks = _choose_threshold(rng, utility, bar, shown)
        played.append((list(shown), picks))
    journal = _journal(played)
    for event in journal["events"]:
        if event["type"] == "round_completed":
            event["duration_ms"] = 3000
    stats = {"mean_utility": mean(utilities),
             "utility_sd": math.sqrt(mean([(u - mean(utilities)) ** 2 for u in utilities])),
             "mean_kept_per_round": mean([len(picks) for _, picks in played]),
             "empty_round_fraction": sum(not picks for _, picks in played) / 40,
             "first_slot_target_fraction": sum(bool(picks) and picks[0] == shown[0] for shown, picks in played) / 40,
             "stop_bar": bar}
    return journal, stats


def bootstrap(values):
    if not values:
        return {"mean": None, "ci95": None, "visitors": 0}
    rng = random.Random(20261021)
    draws = sorted(mean(rng.choices(values, k=len(values))) for _ in range(2000))
    return {"mean": mean(values), "ci95": [draws[49], draws[1949]], "visitors": len(values)}


def aggregate(visitors):
    output = {}
    for prefix in PREFIXES:
        modes = {}
        for mode in MODES:
            samples = [v["prefixes"][str(prefix)][mode] for v in visitors]
            metrics = {}
            for metric in METRICS:
                models = {}
                for name in NAMES:
                    valid = [s[name][metric] for s in samples if s[name][metric] is not None]
                    models[name] = {"mean": mean(valid), "visitors": len(valid)}
                paired = {}
                for baseline in ("heuristic", "stop_only"):
                    differences = [s["ml"][metric] - s[baseline][metric] for s in samples
                                   if s["ml"][metric] is not None and s[baseline][metric] is not None]
                    paired["ml_minus_" + baseline] = bootstrap(differences)
                metrics[metric] = {"models": models, "paired": paired}
            modes[mode] = metrics
        output[str(prefix)] = modes
    return output


def run():
    model, numeric = engine_module("choice_model"), engine_module("numeric")
    catalog = model.load_catalog()
    source_paths = [Path(__file__), PROTOCOL, ROOT / "norn/fast_thinkers.py", ROOT / "norn/fast_compare.py",
                    ROOT / "norn/simulate.py", ROOT / "norn/benchmark.py", ROOT / "norn/events.py",
                    ROOT / "norn/core.py", Path(model.__file__),
                    Path(model.__file__).parent / "numeric.py", Path(model.__file__).parent / "fast_thinker.py",
                    Path(model.__file__).parent / "ledger.py"]
    source_paths += sorted((Path(model.__file__).parent / "config").glob("*features*.json"))
    source_paths += [Path(model.__file__).parent / "config/cards_metadata.json"]
    report = {"schema_version": "norn.fast-thinker-diagnostic.v1", "deployment": "simulated",
              "api_calls": 0, "seed_list": SEEDS, "visitors_per_seed": VISITORS_PER_SEED,
              "prefixes": PREFIXES, "test_rounds": list(range(33, 41)), "cells": {},
              "numerical_contract": numeric.PORTABLE_CONTRACT, "model_version": model.MODEL_VERSION,
              "feature_version": catalog.feature_version,
              "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in source_paths},
              "bootstrap": {"seed": 20261021, "draws": 2000, "unit": "simulated visitor",
                            "intervals": "descriptive, pointwise, uncorrected; not human evidence"}}
    with numeric.use(numeric.PORTABLE_CONTRACT):
        report["fixture"] = fixture_audit(catalog)
        spaces = _card_spaces(catalog)
        pixel_prefixes = {}
        for cell in CELLS:
            visitors = []
            for seed in SEEDS:
                for person in range(VISITORS_PER_SEED):
                    shown = exposure(seed, person, catalog)
                    journal, stats = simulated_visit(cell, seed, person, catalog, spaces, shown)
                    replay_events(journal)
                    first32 = content_hash(prefix_of(journal, 32))
                    if cell == "pixel_threshold":
                        pixel_prefixes[seed, person] = first32
                    if cell == "pixel_reversal" and first32 != pixel_prefixes[seed, person]:
                        raise AssertionError("Pixel reversal changed pre-reversal facts")
                    rows = model.observations(journal, catalog)
                    visitor = {"id": f"{seed}:{person}", "journal_hash": content_hash(journal),
                               "first32_hash": first32, "generation": stats, "prefixes": {}}
                    for count in PREFIXES:
                        prefix = prefix_of(journal, count)
                        fitted = predictors(prefix, rows[:count], catalog)
                        scores = {}
                        for mode in MODES:
                            first = mode == "first"
                            scores[mode] = score(rows[32:], fitted, catalog, first_only=first)
                            scores["train_" + mode] = score(rows[:count], fitted, catalog, first_only=first)
                        visitor["prefixes"][str(count)] = scores
                    visitors.append(visitor)
            report["cells"][cell] = {"visitors": visitors, "aggregate": aggregate(visitors)}
            print(f"Completed {cell}: {len(visitors)} simulated visitors", file=sys.stderr, flush=True)
    report["pixel_reversal_first32_equal"] = True
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New JSON destination; never overwrites")
    args = parser.parse_args(argv)
    # Reserve first, so an invalid destination cannot waste a simulation run.
    with args.output.open("x", encoding="utf-8") as destination:
        json.dump(run(), destination, ensure_ascii=False, allow_nan=False, indent=2)
        destination.write("\n")


if __name__ == "__main__":
    main()

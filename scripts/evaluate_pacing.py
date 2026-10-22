#!/usr/bin/env python3
"""Describe consented game exports and fit an offline observational candidate.

No provider, archive service, research checkout or live policy is contacted.
Only aggregate metrics and an optional candidate policy leave this process.
"""

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from norn.experience import validate_archive_cohort  # noqa: E402
from norn.finale import validate_result  # noqa: E402
from norn.pacing import validate_trace  # noqa: E402
from norn.product import validate_product  # noqa: E402
from norn.research_inspection import validated  # noqa: E402
from norn.core import engine_module  # noqa: E402

REPORT_VERSION = "norn.pacing-evaluation.v1"
CANDIDATE_VERSION = "norn.pacing-candidate.v1"
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 100_000
DEFAULTS = {"round_cost": .005, "test_fraction": .25, "split_seed": "norn.pacing-evaluation.v1",
            "min_train_groups": 20, "min_test_groups": 8, "min_groups_per_round": 5,
            "shrinkage_groups": 5.0, "utility_tolerance": .01}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def load_records(path):
    path = Path(path)
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("input_too_large")
    raw = path.read_bytes()
    def invalid_constant(_):
        raise ValueError("nonfinite_json")
    def parse(text):
        return json.loads(text, parse_constant=invalid_constant)
    text = raw.decode("utf-8")
    try:
        value = parse(text)
    except json.JSONDecodeError:
        value = [parse(line) for line in text.splitlines() if line.strip()]
    if type(value) is dict:
        value = value["records"] if "records" in value else [value]
    if type(value) is not list or len(value) > MAX_RECORDS:
        raise ValueError("input_requires_bounded_records")
    return value, hashlib.sha256(raw).hexdigest()


def _bounded_id(value):
    return type(value) is str and 1 <= len(value) <= 128 and all(ord(c) >= 33 for c in value)


def _deduplicate(records, exclusions):
    """Identical evaluation facts count once; conflicting snapshots fail closed."""
    kept, conflicts = {}, set()
    fields = ("schema_version", "record_id", "visitor_id", "participant_id", "product", "journal",
              "pacing", "finale", "deployment", "source_deployment")
    for row in records:
        if type(row) is not dict or not _bounded_id(row.get("record_id")):
            exclusions["invalid_record_identity"] += 1
            continue
        identity = row["record_id"]
        signature = canonical({k: row[k] for k in fields if k in row})
        if identity in conflicts:
            exclusions["additional_conflicting_duplicate"] += 1
        elif identity not in kept:
            kept[identity] = (signature, row)
        elif kept[identity][0] == signature:
            exclusions["identical_duplicate"] += 1
        else:
            del kept[identity]
            conflicts.add(identity)
            exclusions["conflicting_record_id"] += 1
    return [row for _, row in kept.values()]


def split_for(group, seed, test_fraction):
    digest = hashlib.sha256((seed + "\0" + group).encode("utf-8")).digest()
    return "test" if int.from_bytes(digest[:8], "big") / 2**64 < test_fraction else "train"


def _groups(records, exclusions):
    mapping, conflicts = {}, set()
    for row in records:
        visitor = row.get("visitor_id")
        if not _bounded_id(visitor):
            continue
        participant = row.get("participant_id")
        if participant is not None:
            if not _bounded_id(participant):
                conflicts.add(visitor)
            elif visitor in mapping and mapping[visitor] != participant:
                conflicts.add(visitor)
            else:
                mapping[visitor] = participant
    for row in records:
        visitor = row.get("visitor_id")
        if not _bounded_id(visitor):
            exclusions["missing_visitor_group"] += 1
            continue
        if visitor in conflicts:
            exclusions["conflicting_participant_mapping"] += 1
            continue
        yield row, ("participant:" + mapping[visitor] if visitor in mapping else "visitor:" + visitor)


def _visits(records, exclusions):
    research = engine_module("research_deck")
    decks = {}
    for row, group in _groups(records, exclusions):
        if row.get("schema_version") not in ("norn.partial.v1", "norn.record.v2"):
            exclusions["unsupported_record_schema"] += 1
            continue
        if "product" not in row:
            exclusions["legacy_or_other_product"] += 1
            continue
        try:
            validate_product(row["product"], row.get("journal"))
            validate_archive_cohort(row)
        except (ValueError, TypeError, KeyError):
            exclusions["invalid_product_or_cohort"] += 1
            continue
        journal = row.get("journal")
        try:
            if type(journal) is not dict or type(journal.get("events")) is not list:
                raise ValueError("invalid_journal")
            name = research.deck_of(journal)
            if name not in decks:
                decks[name] = research.load_deck(name)
            replay = validated(journal, decks[name], require_complete=False)
        except (ValueError, TypeError, KeyError, IndexError):
            exclusions["invalid_journal_or_deck"] += 1
            continue
        if "pacing" not in row:
            exclusions["missing_pacing"] += 1
            continue
        try:
            pacing = validate_trace(row["pacing"], journal)
        except (ValueError, TypeError, KeyError, IndexError):
            exclusions["unreconstructible_pacing"] += 1
            continue
        finale, state = None, "missing"
        if "finale" in row:
            try:
                finale = validate_result(row["finale"], journal, decks[name])
                state = "valid"
            except (ValueError, TypeError, KeyError, IndexError):
                state = "invalid"
                exclusions["invalid_finale"] += 1
        completed = sum(e["type"] == "round_completed" for e in replay["journal"]["events"])
        started = sum(e["type"] == "round_started" for e in replay["journal"]["events"])
        yield {"group": group, "rounds": completed, "started_rounds": started,
               "partial": row["schema_version"] == "norn.partial.v1", "pacing": pacing,
               "finale": finale, "finale_state": state}


def _lane_values(finale, lane):
    if lane == "jev":
        opponent = finale["jev"]
        if opponent["status"] != "ok":
            return None
        probabilities, guess = opponent["probabilities"], opponent["predicted_card_id"]
    else:
        probabilities = finale["baseline_probabilities" if lane == "baseline" else "probabilities"]
        guess = (finale["candidate_ids"][max(range(4), key=lambda i: probabilities[i])]
                 if lane == "baseline" else finale["predicted_card_id"])
    probability = probabilities[finale["candidate_ids"].index(finale["chosen_card_id"])]
    return {"nll": -math.log(probability) if probability > 0 else None,
            "hit": guess == finale["chosen_card_id"]}


def _summary(values):
    n = len(values)
    infinite = sum(v["nll"] is None for v in values)
    return {"n": n, "mean_nll": (sum(v["nll"] for v in values) / n if n and not infinite else None),
            "nll_status": "infinite" if infinite else "finite" if n else "missing",
            "infinite_nll_count": infinite,
            "hits": sum(v["hit"] for v in values),
            "hit_rate": sum(v["hit"] for v in values) / n if n else None}


def _gain(finale, lane="local"):
    base, candidate = _lane_values(finale, "baseline"), _lane_values(finale, lane)
    if candidate is None or base["nll"] is None or candidate["nll"] is None:
        return None
    return base["nll"] - candidate["nll"]


def _gain_summary(finales, lane="local"):
    values = [v for finale in finales if (v := _gain(finale, lane)) is not None]
    pairs = [(_lane_values(f, "baseline"), value) for f in finales
             if (value := _lane_values(f, lane)) is not None]
    return {"paired_n": len(pairs), "finite_paired_n": len(values),
            "nonfinite_paired_n": len(pairs) - len(values),
            "mean_nll_gain": sum(values) / len(values) if values else None,
            "hit_rate_gain": sum(int(v["hit"]) - int(b["hit"]) for b, v in pairs) / len(pairs) if pairs else None,
            "positive_means": "candidate has lower NLL than baseline on the same recorded finale"}


def describe(visits):
    rounds = []
    for number in sorted({visit["rounds"] for visit in visits}):
        rows = [visit for visit in visits if visit["rounds"] == number]
        finales = [visit["finale"] for visit in rows if visit["finale_state"] == "valid"]
        covered = [finale for finale in finales if finale["jev"]["status"] == "ok"]
        missing = sum(visit["finale_state"] == "missing" for visit in rows)
        invalid = sum(visit["finale_state"] == "invalid" for visit in rows)
        rounds.append({"exploration_rounds": number, "visits": len(rows),
                       "groups": len({visit["group"] for visit in rows}),
                       "valid_finales": len(finales), "no_finale_at_export": missing,
                       "no_finale_fraction": missing / len(rows), "invalid_finales": invalid,
                       "partial_without_finale": sum(v["partial"] and v["finale_state"] == "missing" for v in rows),
                       "all_valid_finales": {lane: _summary([_lane_values(f, lane) for f in finales])
                                             for lane in ("baseline", "local")},
                       "local_gain_over_baseline": _gain_summary(finales),
                       "jev_status_counts": dict(Counter(f["jev"]["status"] for f in finales)),
                       "jev_common_coverage": {
                           "n": len(covered),
                           "lanes": {lane: _summary([_lane_values(f, lane) for f in covered])
                                     for lane in ("baseline", "local", "jev")},
                           "local_gain_over_baseline": _gain_summary(covered),
                           "jev_gain_over_baseline": _gain_summary(covered, "jev")}})
    decisions = defaultdict(list)
    for visit in visits:
        for decision in visit["pacing"]["decisions"]:
            number = decision["round"]
            subsequent = ("continued" if visit["started_rounds"] > number else
                          "finale_at_this_round" if visit["finale_state"] == "valid" and visit["rounds"] == number else
                          "unknown_at_export")
            decisions[number].append((decision, subsequent))
    offers = []
    for number, rows in sorted(decisions.items()):
        offered = [(d, behavior) for d, behavior in rows if d["action"] == "offer_finale"]
        offers.append({"round": number, "decisions": len(rows),
                       "offer_count": len(offered), "limit_count": sum(d["action"] == "limit" for d, _ in rows),
                       "mean_offer_propensity": sum(d["offer_probability"] for d, _ in rows) / len(rows),
                       "propensity_counts": dict(Counter(str(d["offer_probability"]) for d, _ in rows)),
                       "after_offer": dict(Counter(behavior for _, behavior in offered)),
                       "after_other_decision": dict(Counter(behavior for d, behavior in rows if d["action"] != "offer_finale"))})
    return {"by_actual_exploration_rounds": rounds, "decision_behavior": offers}


def _group_gains(visits):
    values = defaultdict(lambda: defaultdict(list))
    for visit in visits:
        if visit["finale_state"] == "valid" and (gain := _gain(visit["finale"])) is not None:
            values[visit["rounds"]][visit["group"]].append(gain)
    return {number: {group: sum(gains) / len(gains) for group, gains in groups.items()}
            for number, groups in values.items()}


def candidate(visits, options):
    train = [v for v in visits if v["split"] == "train"]
    test = [v for v in visits if v["split"] == "test"]
    train_gains, test_gains = _group_gains(train), _group_gains(test)
    train_groups = {g for rows in train_gains.values() for g in rows}
    test_groups = {g for rows in test_gains.values() for g in rows}
    reasons, fitted = [], []
    nonfinite = sum(v["finale_state"] == "valid" and _gain(v["finale"]) is None for v in visits)
    if nonfinite:
        reasons.append("nonfinite_paired_finales_need_a_predefined_training_rule")
    if len(train_groups) < options["min_train_groups"]:
        reasons.append("insufficient_training_groups_with_scored_finales")
    if len(test_groups) < options["min_test_groups"]:
        reasons.append("insufficient_test_groups_with_scored_finales")
    for number, groups in sorted(train_gains.items()):
        if len(groups) >= options["min_groups_per_round"]:
            smoothed = sum(groups.values()) / (len(groups) + options["shrinkage_groups"])
            fitted.append({"round": number, "groups": len(groups), "smoothed_gain_nats": smoothed,
                           "utility": smoothed - options["round_cost"] * number})
    if len(fitted) < 2:
        reasons.append("insufficient_training_round_coverage")
    result = {"schema_version": CANDIDATE_VERSION, "trained": False, "candidate_only": True,
              "active_policy_unchanged": "norn.pacing.v1", "automatic_deployment": False,
              "model_family": "group_balanced_shrunk_observational_round_lookup",
              "round_cost": options["round_cost"], "round_cost_meaning": "product utility weight in NLL-nats per round; not research truth",
              "training_groups": len(train_groups), "test_groups": len(test_groups),
              "nonfinite_paired_finales": nonfinite,
              "training_rounds": fitted, "recommended_interval": None,
              "validation": None, "refusal_reasons": reasons}
    if reasons:
        return result
    best = max(fitted, key=lambda row: (row["utility"], -row["round"]))
    close = {row["round"] for row in fitted if row["utility"] >= best["utility"] - options["utility_tolerance"]}
    lower = upper = best["round"]
    while lower - 1 in close:
        lower -= 1
    while upper + 1 in close:
        upper += 1
    result["recommended_interval"] = [lower, upper]
    # Pick on training data only. Equal-weight groups within each exact round,
    # then equal-weight the training-selected rounds. No search over test data.
    observed = []
    for number in range(lower, upper + 1):
        groups = test_gains.get(number, {})
        if len(groups) < options["min_groups_per_round"]:
            reasons.append("insufficient_test_coverage_at_recommended_rounds")
            continue
        gain = sum(groups.values()) / len(groups)
        observed.append({"round": number, "groups": len(groups), "mean_gain_nats": gain,
                         "mean_utility": gain - options["round_cost"] * number})
    result["validation"] = {"scope": "independent groups observed at the same selected rounds; no causal policy effect",
                            "rounds": observed,
                            "mean_round_utility": sum(v["mean_utility"] for v in observed) / len(observed) if observed else None}
    if not reasons and result["validation"]["mean_round_utility"] <= 0:
        reasons.append("heldout_product_utility_not_positive")
    result["trained"] = not reasons
    return result


def evaluate(records, **overrides):
    if set(overrides) - set(DEFAULTS):
        raise ValueError("unknown_evaluation_option")
    options = {**DEFAULTS, **overrides}
    if (type(options["test_fraction"]) not in (int, float)
            or not math.isfinite(options["test_fraction"]) or not 0 < options["test_fraction"] < 1
            or not _bounded_id(options["split_seed"])
            or any(type(options[k]) is not int or options[k] < 1
                   for k in ("min_train_groups", "min_test_groups", "min_groups_per_round"))
            or any(type(options[k]) not in (int, float) or not math.isfinite(options[k]) or options[k] < 0
                   for k in ("round_cost", "shrinkage_groups", "utility_tolerance"))):
        raise ValueError("invalid_evaluation_options")
    if type(records) is not list or len(records) > MAX_RECORDS:
        raise ValueError("invalid_records")
    exclusions = Counter()
    unique = _deduplicate(records, exclusions)
    visits = list(_visits(unique, exclusions))
    for visit in visits:
        visit["split"] = split_for(visit["group"], options["split_seed"], options["test_fraction"])
    grouped = {split: {v["group"] for v in visits if v["split"] == split} for split in ("train", "test")}
    assert not grouped["train"] & grouped["test"]
    total = len(visits)
    missing = sum(v["finale_state"] == "missing" for v in visits)
    report = {"schema_version": REPORT_VERSION, "model_calls": 0, "parameters": options,
              "scope": "descriptive observational analysis and offline candidate preparation; no learned-round causal claim",
              "counts": {"input_rows": len(records), "unique_nonconflicting_records": len(unique),
                         "valid_product_visits": total,
                         "valid_finales": sum(v["finale_state"] == "valid" for v in visits),
                         "no_finale_at_export": missing,
                         "no_finale_fraction": missing / total if total else None,
                         "invalid_finales": sum(v["finale_state"] == "invalid" for v in visits)},
              "excluded": dict(sorted(exclusions.items())),
              "group_split": {"unit": "participant mapping when supplied, otherwise anonymous browser visitor",
                              "assignment": "SHA256(seed + NUL + group), before looking at outcomes",
                              "train_groups": len(grouped["train"]), "test_groups": len(grouped["test"]),
                              "same_group_crosses_split": False},
              "hit_rules": {"baseline": "offline first argmax in recorded candidate order; not a recorded live bet",
                            "local": "recorded precommitted prediction", "jev": "recorded settled prediction, ok only"},
              "limitations": [
                  "Visitors choose when to stop; different-round groups are not exchangeable treatments.",
                  "No finale at export is missing/censored, not an incorrect prediction or necessarily permanent dropout.",
                  "Decision follow-up does not prove the offer was displayed, noticed, or caused stopping.",
                  "Recorded offer propensities enable future preregistered randomization analysis, not automatic correction for self-selected stopping.",
                  "Exact pacing reconstruction requires a compatible native numeric runtime; rejected traces are counted explicitly.",
                  "Anonymous visitor grouping cannot link the same human across devices without an explicit participant mapping.",
                  "No claim that any fixed number of rounds is sufficient to learn a person's preference."],
              **describe(visits), "candidate_policy": candidate(visits, options)}
    canonical(report)  # finite JSON and no raw record content leaves this API
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policy-output", type=Path)
    for name in ("round_cost", "test_fraction", "shrinkage_groups", "utility_tolerance"):
        parser.add_argument("--" + name.replace("_", "-"), type=float, default=DEFAULTS[name])
    for name in ("min_train_groups", "min_test_groups", "min_groups_per_round"):
        parser.add_argument("--" + name.replace("_", "-"), type=int, default=DEFAULTS[name])
    parser.add_argument("--split-seed", default=DEFAULTS["split_seed"])
    args = parser.parse_args(argv)
    try:
        destinations = [args.output] + ([args.policy_output] if args.policy_output else [])
        if len({p.resolve() for p in destinations + [args.input]}) != len(destinations) + 1:
            raise ValueError("input_and_outputs_must_be_distinct")
        rows, digest = load_records(args.input)
        options = {k: getattr(args, k) for k in DEFAULTS}
        report = evaluate(rows, **options)
        report["input_sha256"] = digest
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        if args.policy_output:
            policy = {**deepcopy(report["candidate_policy"]), "source_report_input_sha256": digest,
                      "evaluation_parameters": options}
            args.policy_output.write_text(json.dumps(policy, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        print(json.dumps({"valid_product_visits": report["counts"]["valid_product_visits"],
                          "valid_finales": report["counts"]["valid_finales"],
                          "trained": report["candidate_policy"]["trained"], "model_calls": 0}))
        return 0
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, RecursionError):
        print("Pacing evaluation failed: invalid input, options, runtime, or output path; no raw records were printed.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

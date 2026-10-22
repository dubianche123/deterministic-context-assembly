#!/usr/bin/env python3
"""Add the new policy to the exact saved diagnostic inputs, without retuning."""

from copy import deepcopy
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from norn.benchmark import default_journal
from norn.core import engine_module
from norn.events import content_hash
from norn.fast_compare import prepare_experiment
from norn.fast_thinkers import prepare_policy
from scripts import fast_thinker_diagnostic as original

REFERENCE = ROOT / "benchmarks/results/fast-thinker-diagnostic-20261021.json.gz"
PROTOCOL = ROOT / "docs/choice-policy-protocol.md"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def aggregate(visitors, reference):
    result = deepcopy(reference["aggregate"])
    for prefix in original.PREFIXES:
        key = str(prefix)
        result[key]["guard"] = {
            "active": sum(v["prefixes"][key]["guard"]["status"] == "active" for v in visitors),
            "fallback": sum(v["prefixes"][key]["guard"]["status"] == "baseline" for v in visitors),
            "visitors": len(visitors)}
        for mode in original.MODES:
            for metric in original.METRICS:
                cell = result[key][mode][metric]
                values = [v["prefixes"][key][mode][metric] for v in visitors
                          if v["prefixes"][key][mode][metric] is not None]
                cell["models"]["ml_policy"] = {"mean": original.mean(values), "visitors": len(values)}
                for baseline in ("ml", "heuristic", "stop_only"):
                    differences = []
                    for visitor, old in zip(visitors, reference["visitors"]):
                        assert visitor["id"] == old["id"]
                        new = visitor["prefixes"][key][mode][metric]
                        prior = old["prefixes"][key][mode][baseline][metric]
                        if new is not None and prior is not None:
                            differences.append(new - prior)
                    cell["paired"]["policy_minus_" + baseline] = original.bootstrap(differences)
    return result


def run():
    reference = json.loads(gzip.decompress(REFERENCE.read_bytes()))
    model, policy, numeric = (engine_module(name) for name in ("choice_model", "choice_policy", "numeric"))
    catalog = model.load_catalog()
    # These bytes determine original labels/exposures/split, not the new model.
    frozen_sources = ["scripts/fast_thinker_diagnostic.py", "norn/simulate.py", "norn/benchmark.py",
                      "the-norn-machine/backend/choice_model.py", "the-norn-machine/backend/numeric.py"]
    frozen_sources += [name for name in reference["source_sha256"] if "/config/" in name]
    for source in frozen_sources:
        if sha(ROOT / source) != reference["source_sha256"][source]:
            raise ValueError(f"Frozen reference source changed: {source}")
    sources = [Path(__file__), PROTOCOL, Path(policy.__file__), ROOT / "norn/fast_thinkers.py",
               ROOT / "norn/fast_compare.py"] + [ROOT / p for p in frozen_sources]
    report = {"schema_version": "norn.choice-policy-validation.v1", "model_version": policy.MODEL_VERSION,
              "reference_artifact": str(REFERENCE.relative_to(ROOT)), "reference_sha256": sha(REFERENCE),
              "protocol_sha256": sha(PROTOCOL), "source_sha256": {str(p.relative_to(ROOT)): sha(p) for p in sources},
              "feature_version": catalog.feature_version, "numerical_contract": numeric.PORTABLE_CONTRACT,
              "python": platform.python_version(), "api_calls": 0, "cells": {},
              "seeds": original.SEEDS, "visitors_per_seed": original.VISITORS_PER_SEED,
              "prefixes": original.PREFIXES, "test_rounds": reference["test_rounds"],
              "scope": "Previously inspected outer holdout; development regression, not independent human validation.",
              "invariants": {"changed_fixtures": False, "changed_splits": False, "changed_metrics": False,
                             "retuned_after_results": False, "deployed": False}}
    with numeric.use(numeric.PORTABLE_CONTRACT):
        journal = default_journal()
        prefix, train, _, _ = prepare_experiment(journal, train_rounds=8)
        assert content_hash(journal) == reference["fixture"]["journal_hash"]
        fitted = {"ml_policy": prepare_policy(prefix, train)}
        report["fixture"] = {"journal_hash": content_hash(journal), "original": reference["fixture"],
                             "guard": fitted["ml_policy"].metadata["guard"],
                             "first": original.score(model.observations(journal)[8:], fitted, catalog, first_only=True),
                             "full": original.score(model.observations(journal)[8:], fitted, catalog, first_only=False)}
        spaces = original._card_spaces(catalog)
        for name in original.CELLS:
            visitors = []
            for old in reference["cells"][name]["visitors"]:
                seed, person = map(int, old["id"].split(":"))
                shown = original.exposure(seed, person, catalog)
                journal, generation = original.simulated_visit(name, seed, person, catalog, spaces, shown)
                assert content_hash(journal) == old["journal_hash"], (name, old["id"])
                assert generation == old["generation"]
                rows = model.observations(journal, catalog)
                states = policy.path(rows[:32], catalog)
                visitor = {"id": old["id"], "journal_hash": old["journal_hash"], "prefixes": {}}
                for count in original.PREFIXES:
                    state = states[count]
                    prefix = original.prefix_of(journal, count)
                    predictor = {"ml_policy": prepare_policy(prefix, rows[:count], state=state)}
                    cell = {"guard": state["guard"], "training_hash": state["training_hash"],
                            "candidate_theta": state["theta"], "effective_theta": state["effective_theta"]}
                    for mode in original.MODES:
                        first = mode == "first"
                        cell[mode] = original.score(rows[32:], predictor, catalog, first_only=first)["ml_policy"]
                        cell["train_" + mode] = original.score(rows[:count], predictor, catalog, first_only=first)["ml_policy"]
                    visitor["prefixes"][str(count)] = cell
                visitors.append(visitor)
            report["cells"][name] = {"visitors": visitors, "aggregate": aggregate(visitors, reference["cells"][name])}
            print(f"Verified identical inputs and scored {name}: {len(visitors)} visitors", file=sys.stderr, flush=True)
    report["invariants"]["all_840_journal_hashes_match"] = True
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New .json.gz output; never overwrites")
    args = parser.parse_args()
    with args.output.open("xb") as destination:
        raw = (json.dumps(run(), ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode()
        destination.write(gzip.compress(raw, mtime=0))


if __name__ == "__main__":
    main()

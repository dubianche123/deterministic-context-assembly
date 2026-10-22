"""Can `norn records evaluate` tell card-feature models apart, and does it refuse when it should?

Runs the held-out model comparison on simulated visitors whose taste is
deliberately not always the scoring model's own: no taste at all, taste in
the model's 12 features, in recurring motifs, in the 64 raw pixel statistics,
or mixed; chosen by the model's logit rule or by a threshold rule in random
click order; with and without display-position bias and fatigue.

    python3 scripts/model_evaluation_sim.py --output benchmarks/results/model-evaluation-simulated-v1.json

This checks the instrument, not people: a verdict here says which model a
simulated population favours, and whether the decision rule refuses to switch
on noise. Needs numpy. Synthetic data only.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from norn.evaluate import DECISION_RULE, MODELS, build_evaluation  # noqa: E402
from norn.simulate import simulate_records  # noqa: E402

CELLS = (
    ("no taste", {"truth": "features", "scale": 0.0}),
    ("features, weak", {"truth": "features", "scale": 0.5}),
    ("features", {"truth": "features", "scale": 1.0}),
    ("features, threshold rule", {"truth": "features", "scale": 1.0, "process": "threshold"}),
    ("features, position bias + fatigue", {"truth": "features", "scale": 1.0, "position_bias": 1.0, "fatigue": 1.0}),
    ("motifs", {"truth": "motif", "scale": 1.5}),
    ("motifs, threshold rule", {"truth": "motif", "scale": 1.5, "process": "threshold"}),
    ("raw pixels", {"truth": "pixel", "scale": 1.0}),
    ("features + motifs", {"truth": "mixed", "scale": 1.0}),
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--visitors", type=int, default=200, help="Single-visit simulated visitors per cell")
    parser.add_argument("--rounds", type=int, nargs=2, default=(8, 16), metavar=("MIN", "MAX"))
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--draws", type=int, default=500)
    parser.add_argument("--output", type=Path, help="Write-once JSON result")
    args = parser.parse_args(argv)
    cells = []
    for name, settings in CELLS:
        records = simulate_records(args.visitors, visits=(1, 1), rounds=tuple(args.rounds), seed=args.seed, **settings)
        result = build_evaluation(records, seed=args.seed, draws=args.draws)
        held = result["held_out"]["models"]
        cells.append({
            "cell": name, "simulation": settings, "decision": result["decision"],
            "all": {model: held[model]["all"]["per_choice"] for model in MODELS},
            "all_minus_hybrid": {model: held[model]["all"].get("minus_hybrid") for model in MODELS},
            "all_minus_stop_only": {model: held[model]["all"].get("minus_stop_only") for model in MODELS},
            "within_type": {model: held[model]["within_type"]["per_choice"] for model in MODELS},
            "top1": {model: held[model]["top1"]["per_choice"] for model in MODELS},
            "learning_curve_all": {k: {model: row["all"][model]["value"] for model in MODELS}
                                   for k, row in result["learning_curve"].items()},
        })
        print(name, result["decision"], file=sys.stderr, flush=True)
    document = {
        "schema_version": "norn.model-evaluation-simulated.v1", "data": "simulated only",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "visitors_per_cell": args.visitors, "rounds": list(args.rounds), "seed": args.seed,
        "bootstrap_draws": args.draws, "decision_rule": DECISION_RULE, "cells": cells,
        "reading": "nats per held-out choice, lower is better; top1 is a share. Checks the evaluation "
                   "instrument on known simulated tastes; it measures nothing about real people.",
    }
    text = json.dumps(document, ensure_ascii=False, indent=1) + "\n"
    if args.output:
        with open(args.output, "x", encoding="utf-8") as handle:  # earlier results are never replaced
            handle.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

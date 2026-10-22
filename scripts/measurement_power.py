"""How many returning visitors / rated readings before the report can tell?

Simulates visitors with known preference strength and visit-to-visit
stability, runs `norn.report.build_report` repeatedly, and records how often
the 95% interval excludes "no difference". Synthetic data only.

    python3 scripts/measurement_power.py --output benchmarks/results/measurement-power-simulated-v2.json

Every simulated browser also keeps its own stop habit (`--stop-habit`), and a
null grid with no picture preference at all counts false detections.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from norn.report import build_report  # noqa: E402
from norn.simulate import simulate_records  # noqa: E402


def detects_link(report):
    """Per test: own-visit beats stranger-visit; axis signs agree above chance;
    the report's verdict (beats the stranger and the visitor's stop habit alone)."""
    cross = report["validity"]["cross_visit"]
    prediction = cross.get("personal_minus_stranger", {}).get("ci95")
    agreement = cross.get("axis_sign_agreement", {}).get("ci95")
    return {"prediction": bool(prediction and prediction[1] < 0),
            "sign_agreement": bool(agreement and agreement[0] > cross.get("axis_sign_agreement_chance", 1)),
            "verdict": bool(cross.get("stable_preference_detected"))}


LINK_TESTS = ("prediction", "sign_agreement", "verdict")


def detects_lift(report):
    arm = report["groups"]["arms"].get("variant", {})
    interval = arm.get("minus_control", {}).get("rating", {}).get("ci95")
    return bool(interval and interval[0] > 0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repeats", type=int, default=12)
    parser.add_argument("--draws", type=int, default=300)
    parser.add_argument("--stop-habit", type=float, default=0.5,
                        help="sd of each browser's lasting stop-bias shift")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    link, lift = [], []
    # scale 0: no picture preference, only the stop habit; every detection there is false.
    grid = [(0.0, 1.0, visitors) for visitors in (20, 40, 80)] + [
        (scale, stability, visitors) for scale in (0.5, 1.0) for stability in (0.5, 0.8)
        for visitors in (10, 20, 40, 80)]
    for scale, stability, visitors in grid:
        runs = [detects_link(build_report(
            simulate_records(visitors, visits=(2, 2), stability=stability, scale=scale,
                             stop_habit=args.stop_habit, seed=seed),
            draws=args.draws, seed=seed)) for seed in range(args.repeats)]
        link.append({"preference_scale": scale, "stability": stability, "returning_visitors": visitors,
                     **{f"detected_{test}": sum(run[test] for run in runs) / args.repeats for test in LINK_TESTS}})
        print("link", link[-1], file=sys.stderr)
    for effect in (0.3, 0.5):
        for visitors in (60, 120, 240, 480):
            hits = sum(detects_lift(build_report(
                simulate_records(visitors, visits=(1, 1), rounds=(4, 8), seed=seed,
                                 arms=("trial", ("control", "variant")), rating_effect=effect),
                by="experiments.trial", control="control", draws=args.draws, seed=seed))
                for seed in range(args.repeats))
            lift.append({"rating_effect": effect, "rated_visitors_total": visitors, "detected": hits / args.repeats})
            print("lift", lift[-1], file=sys.stderr)
    result = {"schema_version": "norn.measurement-power.v2", "data": "simulated only",
              "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "repeats": args.repeats, "bootstrap_draws": args.draws, "stop_habit": args.stop_habit,
              "stranger": "another returning browser's earlier picture weights, closest in number of choices, "
                          "with the stop bias refitted on the visitor's own earlier visit",
              "cross_visit_link": link, "ab_rating_lift": lift,
              "reading": ("detected = share of repeats whose 95% interval excludes no difference; verdict = "
                          "personal beats both the stranger and the visitor's own stop-only model; "
                          "preference_scale is the sd of simulated feature weights (0 = none: false detections), "
                          "rating sd 0.9.")}
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        with open(args.output, "x", encoding="utf-8") as handle:  # earlier results are never replaced
            handle.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

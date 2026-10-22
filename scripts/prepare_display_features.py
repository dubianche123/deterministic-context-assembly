#!/usr/bin/env python3
"""Write the browser's copy of the scoring-v5 card features for the mixed display.

The browser ranks cards with the same twelve features the engine scores
(engine display_policy). This copies them exactly from choice_features.json,
already scaled, so both sides read identical numbers:

    python3 scripts/prepare_display_features.py [--check]
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from norn.core import engine_module  # noqa: E402

OUTPUT = ROOT / "the-norn-machine" / "frontend" / "data" / "display-features-v1.json"
SCHEMA_VERSION = "norn.display-features.v1"


def document():
    catalog = engine_module("choice_model").load_catalog()
    return {"schema_version": SCHEMA_VERSION, "feature_version": catalog.feature_version,
            "feature_names": list(engine_module("choice_model").FEATURE_NAMES),
            "cards": {card: list(catalog.features[i]) for i, card in enumerate(catalog.ids)}}


def render():
    return json.dumps(document(), sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the shipped file differs")
    args = parser.parse_args()
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != render():
            sys.exit(f"{OUTPUT.relative_to(ROOT)} is stale; rerun scripts/prepare_display_features.py")
        return
    OUTPUT.write_text(render(), encoding="utf-8")
    print(OUTPUT.relative_to(ROOT))


if __name__ == "__main__":
    main()

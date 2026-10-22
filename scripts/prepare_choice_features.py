#!/usr/bin/env python3
"""Precompute the 12 card features of the v4 choice model; NumPy only here.

Each card gets its four hand-authored type coordinates divided by 8 and the
first eight principal components of its 64 measured pixel statistics (taken
from preference_features.json), each scaled to unit standard deviation. The
runtime model reads this file with the standard library only.

    python3 scripts/prepare_choice_features.py            # rewrite the catalog
    python3 scripts/prepare_choice_features.py --check    # verify it is current
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "the-norn-machine" / "backend" / "config"
DEFAULT_OUTPUT = CONFIG / "choice_features.json"
SCHEMA_VERSION = "norn.choice-features.v1"
GENERATOR_VERSION = "norn.choice-features.generator.v1"
AXES = ("E_I", "S_N", "T_F", "J_P")
AXIS_SCALE = 8
COMPONENTS = 8


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_hash(value):
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _label(name):
    sys.path.insert(0, str(CONFIG.parent))
    try:
        from preference_model import feature_label
    finally:
        sys.path.pop(0)
    return feature_label(name)


def build_catalog(metadata_path=CONFIG / "cards_metadata.json",
                  features_path=CONFIG / "preference_features.json"):
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("Choice feature generation needs NumPy: python -m pip install numpy") from exc
    metadata = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
    source = json.loads(Path(features_path).read_text(encoding="utf-8"))
    ids = sorted(metadata)
    if ids != sorted(source["cards"]):
        raise ValueError("Card metadata and pixel features cover different cards")
    for card in ids:
        coords = metadata[card]["coords"]
        if any(type(coords[axis]) is not int or abs(coords[axis]) > AXIS_SCALE for axis in AXES):
            raise ValueError("Type coordinates must be integers within ±8")
    names = source["feature_names"]
    pixel = [index for index, name in enumerate(names) if name.startswith("pixel.")]
    raw = np.array([[source["cards"][card][index] for index in pixel] for card in ids])
    # The same arithmetic as prototypes/xray-v2/build_trajectory.py.
    standardized = (raw - raw.mean(0)) / (raw.std(0) + 1e-12)
    _, singular, vt = np.linalg.svd(standardized, full_matrices=False)
    scores = standardized @ vt[:COMPONENTS].T
    scores /= scores.std(0)
    explained = singular ** 2 / float((singular ** 2).sum())
    components = []
    for k in range(COMPONENTS):
        loadings = vt[k]
        order = sorted(range(len(pixel)), key=lambda index: (-abs(loadings[index]), index))[:3]
        components.append({
            "name": f"pixel.pc{k + 1}",
            "explained_variance_ratio": float(explained[k]),
            "top_loadings": [{"feature": names[pixel[index]], "label": _label(names[pixel[index]]),
                              "loading": float(loadings[index])} for index in order],
        })
    catalog = {
        "schema_version": SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "axes": list(AXES),
        "axis_scale": AXIS_SCALE,
        "pixel_source": {"feature_version": source["feature_version"], "pixel_feature_count": len(pixel),
                         "method": "z-score 64 pixel statistics, SVD, first 8 components, unit standard deviation"},
        "components": components,
        "cards": {card: {"type": metadata[card]["type"],
                         "axes": [metadata[card]["coords"][axis] / AXIS_SCALE for axis in AXES],
                         "pixel": [float(value) for value in scores[index]]}
                  for index, card in enumerate(ids)},
    }
    catalog["feature_version"] = content_hash(catalog)
    return catalog


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="Fail if the stored catalog differs")
    args = parser.parse_args(argv)
    text = canonical_json(build_catalog()) + "\n"
    if args.check:
        current = args.output.read_text(encoding="utf-8") if args.output.exists() else ""
        if current != text:
            print(f"{args.output} is stale; rerun without --check", file=sys.stderr)
            return 1
        print(f"{args.output} is current")
        return 0
    args.output.write_text(text, encoding="utf-8")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

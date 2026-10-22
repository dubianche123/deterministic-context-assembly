#!/usr/bin/env python3
"""Build or verify an isolated, genuinely new-image holdout pack.

The only fitted transform comes from the original 256-card catalogs. This
command measures new image pixels and applies that frozen transform; it never
assigns type/axis labels, gathers choices, calls a model, or edits the live deck.

    python scripts/prepare_generalization_pack.py
    python scripts/prepare_generalization_pack.py --check

Building needs Pillow and NumPy. Checking a frozen pack needs Pillow only.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from norn import feature_projection as projection  # noqa: E402
from scripts.prepare_preference_features import pixel_feature_names, pixel_features  # noqa: E402


DEFAULT_PACK = ROOT / "benchmarks/generalization/v1"
CONFIG = ROOT / "the-norn-machine/backend/config"
ORIGINAL_ASSETS = ROOT / "the-norn-machine/frontend/assets/cards"
SCHEMA_VERSION = "norn.generalization.pack.v1"
FROZEN_PROTOCOL_SHA256 = "sha256:a0f54261398da8e43577743a3bc14be302dcb5674501f285ccb6b46059f89740"
IDS = tuple(f"HOLDOUT_{number:04d}" for number in range(1, 17))
STYLES = frozenset(("illustration", "photograph"))
SCENES = ("courtyard", "market", "workshop", "reading", "coast", "transit", "meal", "garden")
MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_hash(value):
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_hash(path):
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def text_hash(value):
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path):
    with Path(path).open(encoding="utf-8") as stream:
        value = json.load(stream, object_pairs_hook=_unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON number")))
    if type(value) is not dict:
        raise ValueError(f"{Path(path).name} must contain an object")
    return value


def _relative_asset(pack, row):
    name = row.get("asset")
    if name != f"assets/{row['id']}.png":
        raise ValueError("Assets must use their pack-local HOLDOUT ID filenames")
    path = pack / name
    if not path.resolve().is_relative_to(pack.resolve()) or path.is_symlink():
        raise ValueError("An asset must stay inside its pack")
    if not path.is_file():
        raise ValueError(f"Missing holdout asset: {name}")
    size = path.stat().st_size
    if not 0 < size <= MAX_IMAGE_BYTES:
        raise ValueError(f"Invalid holdout image size: {name}")
    return path


def validate_design(prompts):
    if prompts.get("schema_version") != "norn.generalization.prompts.v1":
        raise ValueError("Unsupported holdout prompt set")
    if prompts.get("generation_tool") != "built-in image_gen" or prompts.get("no_reference_images") is not True:
        raise ValueError("This pack requires independent built-in image generation")
    rows = prompts.get("rows")
    if type(rows) is not list or [row.get("id") for row in rows] != list(IDS):
        raise ValueError("The frozen design requires all 16 ordered HOLDOUT IDs")
    cells, batches = defaultdict(list), defaultdict(list)
    for index, row in enumerate(rows):
        if (row.get("scene_id") != SCENES[index // 2]
                or row.get("style") != ("illustration" if index % 2 == 0 else "photograph")
                or row.get("batch") != f"batch_{index // 4 + 1}"):
            raise ValueError("Holdout scene/style/batch assignment differs from the frozen design")
        for key in ("scene_en", "scene_zh", "prompt"):
            if type(row.get(key)) is not str or not row[key].strip():
                raise ValueError(f"Missing prompt field: {key}")
        if row.get("asset") != f"assets/{row['id']}.png":
            raise ValueError("Invalid holdout asset path")
        if any(key in row for key in ("axes", "type", "mbti_type", "motifs", "traits")):
            raise ValueError("Generation design cannot assign model labels")
        cells[row["scene_id"]].append(row["style"])
        batches[row["batch"]].append(row)
    if any(set(styles) != STYLES for styles in cells.values()):
        raise ValueError("Every scene must cross both styles")
    if any(Counter(row["style"] for row in batch) != {"illustration": 2, "photograph": 2}
           for batch in batches.values()):
        raise ValueError("Generation batches must balance the styles")
    return rows


def _receipts(document, rows):
    if (document.get("schema_version") != "norn.generalization.generation.v1"
            or document.get("tool") != "built-in image_gen"):
        raise ValueError("Unsupported generation receipts")
    receipts = document.get("rows")
    if type(receipts) is not list or len(receipts) != len(rows):
        raise ValueError("Every image requires a generation receipt")
    by_id = {}
    for receipt in receipts:
        if type(receipt) is not dict or receipt.get("id") in by_id:
            raise ValueError("Invalid or repeated generation receipt")
        by_id[receipt.get("id")] = receipt
    if set(by_id) != set(IDS):
        raise ValueError("Generation receipts cover different assets")
    for row in rows:
        receipt = by_id[row["id"]]
        if (receipt.get("asset") != row["asset"]
                or receipt.get("prompt_sha256") != text_hash(row["prompt"])
                or receipt.get("source") != "builtin_image_gen"
                or not isinstance(receipt.get("generated_at"), str)
                or not receipt["generated_at"].strip()
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(receipt.get("sha256")))):
            raise ValueError(f"Generation receipt does not match its prompt: {row['id']}")
    return by_id


def _source_identity():
    paths = {"preference_features": CONFIG / "preference_features.json",
             "choice_features": CONFIG / "choice_features.json",
             "card_metadata": CONFIG / "cards_metadata.json",
             "live_manifest": ROOT / "the-norn-machine/frontend/data/cards.json"}
    source = read_json(paths["preference_features"])
    choice = read_json(paths["choice_features"])
    for document in (source, choice):
        if document.get("feature_version") != content_hash({key: value for key, value in document.items()
                                                            if key != "feature_version"}):
            raise ValueError("Original feature catalog hash mismatch")
    expected = list(projection.EXPECTED_IDS)
    if sorted(source.get("cards", {})) != expected or sorted(choice.get("cards", {})) != expected:
        raise ValueError("Original fitting deck must remain the fixed 256 cards")
    assets = {card: file_hash(ORIGINAL_ASSETS / f"{card}.webp") for card in expected}
    if content_hash(assets) != source.get("source_assets_hash"):
        raise ValueError("Original source image bytes differ from their feature catalog")
    identity = {"feature_version": source["feature_version"],
                "choice_feature_version": choice["feature_version"],
                "source_assets_hash": source["source_assets_hash"],
                "files": {name: file_hash(path) for name, path in paths.items()},
                "extractor_sha256": file_hash(ROOT / "scripts/prepare_preference_features.py"),
                "projection_implementation_sha256": file_hash(Path(projection.__file__))}
    return identity, set(assets.values())


def _projection_matches(frozen, identity):
    projection.verify_projection(frozen)
    source = frozen["source"]
    if any(source[key] != identity[key] for key in
           ("feature_version", "choice_feature_version", "source_assets_hash")):
        raise ValueError("Frozen projection belongs to another source deck")
    # A self-consistent hash is not proof that the saved transform actually
    # reproduces its declared source. Reconstruct the original coordinates
    # even during --check, using only saved vectors and standard-library math.
    features = read_json(CONFIG / "preference_features.json")
    choice = read_json(CONFIG / "choice_features.json")
    # The source rows are already normalized, so reconstructing their PCA
    # coordinates cannot validate the separate raw-pixel normalization path.
    # Bind that entire configuration to the pinned original catalog as well.
    normalization = features["normalization"]
    expected_normalization = {
        "method": normalization["method"],
        "means": normalization["means"][:64],
        "scales": normalization["scales"][:64],
        "groups": normalization["groups"][:6],
        "semantic_normalization_energy": math.fsum(group["weight"] for group in normalization["groups"][6:]),
        **normalization["rounding"],
    }
    if canonical_json(frozen["normalization"]) != canonical_json(expected_normalization):
        raise ValueError("Frozen normalization differs from the original source normalization")
    for card in projection.EXPECTED_IDS:
        actual = projection.project_normalized_pixels(features["cards"][card][:64], frozen)
        expected = choice["cards"][card]["pixel"]
        if not _finite(actual, 8) or any(abs(a - b) > 1e-8 for a, b in zip(actual, expected)):
            raise ValueError("Frozen projection does not reconstruct the original choice coordinates")


def _image_info(path):
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Measuring holdout images needs Pillow: python -m pip install '.[assets]'") from exc
    with Image.open(path) as image:
        width, height = image.size
        if min(width, height) < 32 or width * height > MAX_IMAGE_PIXELS or image.format != "PNG":
            raise ValueError(f"Expected a bounded PNG image: {path.name}")
        info = {"width": width, "height": height, "format": image.format, "bytes": path.stat().st_size}
        image.verify()
    return info


def _finite(values, count):
    return type(values) is list and len(values) == count and all(
        type(value) in (int, float) and math.isfinite(value) for value in values)


def _manifest(pack, frozen):
    prompts, protocol = read_json(pack / "prompts.json"), read_json(pack / "protocol.json")
    if (protocol.get("schema_version") != "norn.generalization.protocol.v1"
            or file_hash(pack / "protocol.json") != FROZEN_PROTOCOL_SHA256):
        raise ValueError("Unsupported generalization protocol")
    if (protocol.get("generation_prompts", {}).get("file_sha256")
            != file_hash(pack / "prompts.json").removeprefix("sha256:")):
        raise ValueError("Prompt set differs from the frozen protocol")
    for source in protocol["frozen_training_sources"]:
        path = ROOT / source["path"]
        if not path.resolve().is_relative_to(ROOT) or file_hash(path).removeprefix("sha256:") != source["file_sha256"]:
            raise ValueError("A training source differs from the frozen protocol")
    rows = validate_design(prompts)
    receipts = _receipts(read_json(pack / "generation.json"), rows)
    expected_assets = {row["asset"] for row in rows}
    observed_assets = {path.relative_to(pack).as_posix() for path in (pack / "assets").rglob("*") if path.is_file()}
    if observed_assets != expected_assets:
        raise ValueError("Asset directory must contain exactly the 16 declared PNG images")
    identity, original_hashes = _source_identity()
    _projection_matches(frozen, identity)
    measured, seen_hashes = [], set()
    for row in rows:
        path = _relative_asset(pack, row)
        digest = file_hash(path)
        if digest != receipts[row["id"]]["sha256"]:
            raise ValueError(f"Image bytes differ from the generation receipt: {row['id']}")
        if digest in seen_hashes or digest in original_hashes:
            raise ValueError("Holdout assets must not duplicate another holdout or original image")
        seen_hashes.add(digest)
        info = _image_info(path)
        raw = pixel_features(path)
        projected = projection.project_pixels(raw, frozen)
        if not _finite(raw, 64) or not _finite(projected, 8):
            raise ValueError("Image extraction produced an invalid feature vector")
        measured.append({key: row[key] for key in ("id", "asset", "scene_id", "scene_en", "scene_zh", "style", "batch")}
                        | {"sha256": digest, "image": info,
                           "prompt_sha256": text_hash(row["prompt"]),
                           "features": {"pixel64": raw, "pixel8": projected,
                                        "axes": None, "type": None, "motifs": None}})
    # Detect source changes by another task while the pack was measured. Never
    # repair or rewrite a source catalog on this path.
    if _source_identity()[0] != identity:
        raise ValueError("The source deck changed during pack construction")
    result = {
        "schema_version": SCHEMA_VERSION,
        "purpose": "unblinded new-image engineering holdout; no participant choices or predictive result",
        "predictive_validation": "pending_human_choices",
        "image_count": len(measured),
        "inputs": {name: {"path": name + ".json", "sha256": file_hash(pack / (name + ".json"))}
                   for name in ("protocol", "prompts", "generation")},
        "projection": {"path": "frozen_projection.json", "version": frozen["projection_version"]},
        "source_deck": identity,
        "feature_contract": {"pixel64_names": pixel_feature_names(),
                             "pixel8_names": [f"pixel.pc{index}" for index in range(1, 9)],
                             "pixel8_scale": "unit original-deck standard deviation; unscaled by runtime 0.5",
                             "fit_scope": "original 256 images only; normalization and PCA never refit on holdout",
                             "numeric_check_absolute_tolerance": {"pixel64": 1e-12, "pixel8": 1e-8}},
        "candidate_support": {
            "uniform": {"status": "features_available", "requires": "a separately frozen choice protocol"},
            "stop_only": {"status": "features_available", "requires": "a visitor's frozen training fit"},
            "pixel_only": {"status": "features_available", "requires": "a compatible source-trained predictor"},
            **{name: {"status": "unsupported", "reason": "independent annotations absent; never fill with zero/default labels"}
               for name in ("axes_only", "type16", "hybrid_axes_pixels", "motif91")}},
        "rows": measured,
        "limits": ["Generated images are not independent natural-image population samples",
                   "Byte uniqueness and content hashes do not prove semantic novelty or authorship",
                   "No human predictive validity, personality validity, or e-commerce result is established"]}
    result["pack_version"] = content_hash(result)
    return result


def _check_manifest(saved, measured):
    if (saved.get("schema_version") != SCHEMA_VERSION
            or saved.get("pack_version") != content_hash({key: value for key, value in saved.items() if key != "pack_version"})):
        raise ValueError("Holdout manifest content hash mismatch")
    # Native pixel transcendental operations may differ in their final bits.
    # Metadata, provenance and projection identities still compare exactly.
    stripped = json.loads(canonical_json(saved))
    expected = json.loads(canonical_json(measured))
    for left, right in zip(stripped.get("rows", []), expected["rows"]):
        for key, count, tolerance in (("pixel64", 64, 1e-12), ("pixel8", 8, 1e-8)):
            actual = left.get("features", {}).get(key)
            if not _finite(actual, count) or any(abs(a - b) > tolerance for a, b in zip(actual, right["features"][key])):
                raise ValueError(f"Stored {key} does not reconstruct for {right['id']}")
            left["features"][key] = right["features"][key]
    stripped.pop("pack_version", None)
    expected.pop("pack_version", None)
    if stripped != expected:
        raise ValueError("Holdout manifest differs from its sources")
    return True


def prepare(pack_dir=DEFAULT_PACK, *, check=False):
    pack = Path(pack_dir).resolve()
    manifest_path, frozen_path = pack / "manifest.json", pack / "frozen_projection.json"
    if check:
        frozen, saved = read_json(frozen_path), read_json(manifest_path)
        measured = _manifest(pack, frozen)
        _check_manifest(saved, measured)
        return saved
    if manifest_path.exists() or frozen_path.exists():
        raise ValueError("Pack outputs already exist; use --check or a new versioned pack directory")
    frozen = projection.fit_projection()
    measured = _manifest(pack, frozen)
    # Exclusive creation protects a frozen pack from accidental replacement.
    for path, document in ((frozen_path, frozen), (manifest_path, measured)):
        with path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2) + "\n")
    return measured


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pack-dir", type=Path, default=DEFAULT_PACK)
    parser.add_argument("--check", action="store_true", help="Verify an existing pack without fitting or overwriting")
    args = parser.parse_args(argv)
    try:
        manifest = prepare(args.pack_dir, check=args.check)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Generalization pack failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"verified": True, "check_only": args.check, "images": manifest["image_count"],
                      "pack_version": manifest["pack_version"], "model_calls": 0,
                      "predictive_validation": manifest["predictive_validation"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

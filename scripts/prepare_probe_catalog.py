"""Derive the shippable new-image probe catalog from the frozen generalization pack.

The pack (benchmarks/generalization/v1/) stays outside the live deck. The pilot's
new-image phase needs only each image's id, scene, style and frozen pixel
features, so this writes two small files bound to the pack and projection hashes:

  the-norn-machine/backend/config/probe_new_material_v1.json   (engine: validation, evaluation)
  the-norn-machine/frontend/data/probe-new-material-v1.json    (page: ids, scenes, image URLs)

    python3 scripts/prepare_probe_catalog.py           # write (refuses to replace different content)
    python3 scripts/prepare_probe_catalog.py --check   # confirm both files match the pack

The page expects images at assets/probe/<id>.webp; converting the pack's PNGs is
a separate step that needs Pillow. Standard library only.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.prepare_generalization_pack import (  # noqa: E402
    IDS, SCENES, SCHEMA_VERSION as PACK_SCHEMA_VERSION, read_json,
)

PACK = ROOT / "benchmarks" / "generalization" / "v1"
ENGINE = ROOT / "the-norn-machine" / "backend" / "config" / "probe_new_material_v1.json"
PAGE = ROOT / "the-norn-machine" / "frontend" / "data" / "probe-new-material-v1.json"
PROTOCOL = "new-material-v1"
ROUNDS = 4
SCENES_PER_ROUND = 2


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value):
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def read_manifest(pack=None):
    """Validate the pack's declared identity/design using only the standard library.

    This is a provenance admission check, not a replacement for the full
    generalization-pack checker, which remeasures PNGs with Pillow.
    """
    path = Path(PACK if pack is None else pack) / "manifest.json"
    manifest_bytes = path.read_bytes()
    manifest = read_json(path)
    if path.read_bytes() != manifest_bytes:
        raise ValueError("Pack manifest changed while it was read")
    if (manifest.get("schema_version") != PACK_SCHEMA_VERSION
            or manifest.get("pack_version") != _hash({key: value for key, value in manifest.items()
                                                       if key != "pack_version"})):
        raise ValueError("Frozen pack schema or content hash does not match")
    rows = manifest.get("rows")
    if (type(rows) is not list or len(rows) != len(IDS)
            or manifest.get("image_count") != len(IDS)
            or any(type(row) is not dict for row in rows)
            or [row.get("id") for row in rows] != list(IDS)):
        raise ValueError("The pilot needs all 16 unique, ordered HOLDOUT IDs")
    projection = manifest.get("projection")
    if (type(projection) is not dict or projection.get("path") != "frozen_projection.json"
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(projection.get("version")))):
        raise ValueError("The pack needs a frozen projection identity")
    for index, row in enumerate(rows):
        if (row.get("scene_id") != SCENES[index // 2]
                or row.get("style") != ("illustration" if index % 2 == 0 else "photograph")
                or row.get("batch") != f"batch_{index // 4 + 1}"
                or row.get("asset") != f"assets/{row['id']}.png"):
            raise ValueError("The pilot scene/style design differs from the frozen 8 by 2 design")
        if any(key in row for key in ("axes", "type", "motifs", "traits", "mbti_type")):
            raise ValueError("The pack must not carry type or motif labels")
        features = row.get("features")
        if (type(features) is not dict or set(features) != {"pixel64", "pixel8", "axes", "type", "motifs"}
                or any(features[key] is not None for key in ("axes", "type", "motifs"))):
            raise ValueError("The pack must carry pixels and explicit missing annotations only")
        for key, size in (("pixel64", 64), ("pixel8", 8)):
            vector = features[key]
            if (type(vector) is not list or len(vector) != size
                    or any(type(value) not in (int, float) or not math.isfinite(value) for value in vector)):
                raise ValueError(f"The pack needs {size} finite {key} features")
        image = row.get("image")
        if (type(image) is not dict or image.get("format") != "PNG"
                or any(type(image.get(key)) is not int or image[key] <= 0 for key in ("width", "height", "bytes"))
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(row.get("sha256")))):
            raise ValueError("The pack needs source PNG dimensions and byte hashes")
    return manifest, manifest_bytes


def build():
    manifest, manifest_bytes = read_manifest()
    rows = manifest["rows"]
    styles = sorted({row["style"] for row in rows})
    engine = {
        "schema_version": "norn.probe-catalog.v1", "protocol": PROTOCOL,
        "pack_version": manifest["pack_version"], "projection_version": manifest["projection"]["version"],
        "manifest_sha256": "sha256:" + hashlib.sha256(manifest_bytes).hexdigest(),
        "design": {"rounds": ROUNDS, "scenes_per_round": SCENES_PER_ROUND, "styles": styles,
                   "rule": "each round shows two scenes, both styles of each, in random positions; "
                           "scene order and pairing are random per visit; no image repeats"},
        "pixel_scale_note": "pixel8 is unscaled; candidates apply the runtime pixel scale (0.5) once",
        "cards": {row["id"]: {"scene": row["scene_id"], "style": row["style"],
                              "pixel8": row["features"]["pixel8"], "pixel64": row["features"]["pixel64"]}
                  for row in rows},
    }
    engine["catalog_version"] = _hash(engine)
    page = {"schema_version": "norn.probe-page.v1", "protocol": PROTOCOL,
            "catalog_version": engine["catalog_version"], "rounds": ROUNDS, "scenes_per_round": SCENES_PER_ROUND,
            "cards": [{"id": row["id"], "scene": row["scene_id"], "url": f"./assets/probe/{row['id']}.webp"}
                      for row in rows]}
    return engine, page


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="Verify instead of writing")
    args = parser.parse_args(argv)
    engine, page = build()
    status = 0
    for path, document in ((ENGINE, engine), (PAGE, page)):
        text = json.dumps(document, ensure_ascii=False, indent=1) + "\n"
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if args.check:
            ok = current == text
            print(f"{path.relative_to(ROOT)}: {'current' if ok else 'stale or missing'}")
            status |= not ok
        elif current is None:
            path.write_text(text, encoding="utf-8")
            print(f"wrote {path.relative_to(ROOT)}")
        elif current != text:
            print(f"{path.relative_to(ROOT)} exists with different content; remove it deliberately first",
                  file=sys.stderr)
            status = 1
        else:
            print(f"{path.relative_to(ROOT)}: unchanged")
    return status


if __name__ == "__main__":
    raise SystemExit(main())

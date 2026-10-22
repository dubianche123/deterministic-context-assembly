"""Convert the frozen new-image pack into the page's probe images (assets/probe/*.webp).

Each source PNG is checked against the pack manifest's SHA-256 before use, then
scaled to the live deck's 384-pixel width (aspect kept; the page crops to the
card frame like every card) and written as WebP:

    python3 scripts/prepare_probe_assets.py           # write missing images; never replaces different ones
    python3 scripts/prepare_probe_assets.py --check   # exact source-derived bytes, dimensions and no extra files

Needs Pillow (`pip install '.[assets]'`). The pixel features used by the model
were measured on the source PNGs by the frozen projection, not on these files.
"""

import argparse
import hashlib
import io
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.prepare_probe_catalog import read_manifest  # noqa: E402

PACK = ROOT / "benchmarks" / "generalization" / "v1"
TARGET = ROOT / "the-norn-machine" / "frontend" / "assets" / "probe"
WIDTH = 384
QUALITY = 80


def _encode(source):
    from PIL import Image, ImageOps
    with Image.open(source) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
    height = round(image.height * WIDTH / image.width)
    output = io.BytesIO()
    image.resize((WIDTH, height), Image.LANCZOS).save(output, "WEBP", quality=QUALITY, method=6)
    return output.getvalue(), height


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="Verify instead of writing")
    args = parser.parse_args(argv)
    from PIL import Image
    manifest, _ = read_manifest(PACK)
    if not args.check:
        TARGET.mkdir(parents=True, exist_ok=True)
    expected = {f"{row['id']}.webp" for row in manifest["rows"]}
    observed = {path.relative_to(TARGET).as_posix() for path in TARGET.rglob("*")
                if path.is_file() or path.is_symlink()}
    if observed - expected:
        print("Probe asset directory contains undeclared files", file=sys.stderr)
        return 1
    problems = 0
    for row in manifest["rows"]:
        source = PACK / row["asset"]
        if "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest() != row["sha256"]:
            print(f"{row['id']}: source differs from the frozen manifest", file=sys.stderr)
            problems += 1
            continue
        target = TARGET / f"{row['id']}.webp"
        expected_height = round(row["image"]["height"] * WIDTH / row["image"]["width"])
        encoded, height = _encode(source)
        if height != expected_height:
            print(f"{row['id']}: source dimensions differ from the manifest", file=sys.stderr)
            problems += 1
            continue
        if args.check:
            try:
                if target.is_symlink() or target.read_bytes() != encoded:
                    raise ValueError("Stored bytes do not match the source conversion")
                with Image.open(target) as image:
                    image.load()
                    ok = image.format == "WEBP" and image.size == (WIDTH, expected_height)
            except (OSError, ValueError):
                ok = False
            if not ok:
                print(f"{row['id']}: missing, unreadable, wrong size or different source-derived bytes", file=sys.stderr)
                problems += 1
            continue
        if target.exists():
            if target.read_bytes() != encoded:
                print(f"{target.relative_to(ROOT)} exists with different bytes; remove it deliberately first",
                      file=sys.stderr)
                problems += 1
            continue
        target.write_bytes(encoded)
    if not problems:
        total = sum(path.stat().st_size for path in TARGET.glob("*.webp"))
        print(f"{len(list(TARGET.glob('*.webp')))} probe images, {total / 1024:.0f} KiB"
              + (" (checked)" if args.check else ""))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build the shipped card thumbnails from original PNGs, without AWS access.

Pillow is only needed when rebuilding assets. Normal application use and the
offline asset tests require no image libraries. Archives are read sequentially;
members are never extracted to disk.
"""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "the-norn-machine" / "frontend"
CARD_ID = re.compile(r"NORN_\d{4}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source-dir", type=Path, help="Directory of NORN_0001.png, etc.")
    source.add_argument("--archive", type=Path, help="Original .tar.gz containing images_final/*.png")
    parser.add_argument("--manifest", type=Path, default=FRONTEND / "data" / "cards.json")
    parser.add_argument("--output-dir", type=Path, default=FRONTEND / "assets" / "cards")
    parser.add_argument("--size", type=int, default=480, help="Longest image side, in pixels")
    parser.add_argument("--quality", type=int, default=82)
    args = parser.parse_args()
    if args.size < 1 or not 1 <= args.quality <= 100:
        parser.error("--size must be positive and --quality must be between 1 and 100")

    try:
        from PIL import Image, ImageOps
    except ImportError:
        parser.error("Rebuilding assets needs Pillow: python -m pip install Pillow")

    cards = json.loads(args.manifest.read_text(encoding="utf-8"))
    ids = {card["id"] for card in cards}
    if len(ids) != len(cards) or not all(CARD_ID.fullmatch(card_id) for card_id in ids):
        parser.error("Manifest must contain unique NORN_0001-style card IDs")
    frontend_root = args.manifest.resolve().parent.parent
    try:
        relative_output = args.output_dir.resolve().relative_to(frontend_root).as_posix()
    except ValueError:
        parser.error("--output-dir must be inside the frontend containing the manifest")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    converted: set[str] = set()

    def convert(card_id: str, png_bytes: bytes) -> None:
        if card_id in converted:
            raise ValueError(f"Duplicate original card in archive: {card_id}")
        with Image.open(io.BytesIO(png_bytes)) as original:
            image = ImageOps.exif_transpose(original).convert("RGB")
            image.thumbnail((args.size, args.size), Image.Resampling.LANCZOS)
            target = args.output_dir / f"{card_id}.webp"
            temporary = target.with_suffix(".webp.tmp")
            image.save(temporary, format="WEBP", quality=args.quality, method=6)
            temporary.replace(target)
        converted.add(card_id)

    if args.source_dir:
        for card_id in sorted(ids):
            convert(card_id, (args.source_dir / f"{card_id}.png").read_bytes())
    else:
        with tarfile.open(args.archive, mode="r|gz") as archive:
            for member in archive:
                path = PurePosixPath(member.name)
                if not member.isfile() or path.parent.name != "images_final":
                    continue
                if path.suffix.lower() != ".png" or path.stem not in ids:
                    continue
                extracted = archive.extractfile(member)
                if extracted is not None:
                    with extracted:
                        convert(path.stem, extracted.read())
                    if converted == ids:
                        break
    missing = ids - converted
    if missing:
        raise SystemExit(f"Missing {len(missing)} original PNGs: {', '.join(sorted(missing))}")

    # Change the manifest only after every required original has been converted.
    for card in cards:
        card["url"] = f"./{relative_output}/{card['id']}.webp"
    temporary_manifest = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=args.manifest.parent,
                                         prefix=".cards-", suffix=".json.tmp", delete=False) as handle:
            temporary_manifest = Path(handle.name)
            handle.write(json.dumps(cards, ensure_ascii=False, indent=2) + "\n")
        temporary_manifest.chmod(args.manifest.stat().st_mode & 0o777)
        temporary_manifest.replace(args.manifest)
    finally:
        if temporary_manifest is not None:
            temporary_manifest.unlink(missing_ok=True)
    total = sum(path.stat().st_size for path in args.output_dir.glob("*.webp"))
    print(f"Converted {len(converted)} original cards ({total / 1024 / 1024:.2f} MiB).")
    print(f"Updated {args.manifest}")


if __name__ == "__main__":
    main()

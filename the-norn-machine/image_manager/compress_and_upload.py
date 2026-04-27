#!/usr/bin/env python3
"""
Norn Machine — Image Compression Pipeline
Downloads all card PNGs, resizes + converts to JPEG, uploads to S3 /images/thumb/
Updates frontend/data/cards.json to use thumbnail URLs.

Target: 480px wide, JPEG q=82 → ~50-90KB per image (from ~2MB)
"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from io import BytesIO

try:
    from PIL import Image
except ImportError:
    print("❌ Pillow not found. Run: pip install Pillow")
    sys.exit(1)

try:
    import boto3
except ImportError:
    print("❌ boto3 not found. Run: pip install boto3")
    sys.exit(1)

# ── Config ──────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent
CARDS_JSON = PROJECT_DIR / "frontend" / "data" / "cards.json"

IMAGE_BUCKET = "leo-norn-machine-test-picture-726725835094-ap-northeast-1-an"
REGION = "ap-northeast-1"
THUMB_PREFIX = "images/thumb/"
SOURCE_S3_PREFIX = "images"   # original PNGs live at s3://IMAGE_BUCKET/images/NORN_XXXX.png

# Target dimensions: max width 480px, maintain aspect ratio
TARGET_WIDTH = 480
JPEG_QUALITY = 82

CACHE_DIR = Path("/tmp/norn_originals")
CACHE_DIR.mkdir(exist_ok=True)

WORKERS = 8  # concurrent S3 operations


def download_image(s3, card_id: str) -> tuple[str, bytes | None]:
    """Download original PNG from S3 directly (avoids SSL issues). Returns (card_id, raw_bytes)."""
    cache_path = CACHE_DIR / f"{card_id}.png"
    if cache_path.exists():
        return card_id, cache_path.read_bytes()

    key = f"{SOURCE_S3_PREFIX}/{card_id}.png"
    try:
        resp = s3.get_object(Bucket=IMAGE_BUCKET, Key=key)
        data = resp["Body"].read()
        cache_path.write_bytes(data)
        return card_id, data
    except Exception as e:
        print(f"  ❌ S3 read failed {card_id}: {e}")
        return card_id, None


def compress_image(raw_bytes: bytes) -> bytes:
    """Resize to TARGET_WIDTH, convert to JPEG. Returns compressed bytes."""
    img = Image.open(BytesIO(raw_bytes)).convert("RGB")
    w, h = img.size
    new_h = int(h * TARGET_WIDTH / w)
    img = img.resize((TARGET_WIDTH, new_h), Image.LANCZOS)

    out = BytesIO()
    img.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
    return out.getvalue()


def upload_to_s3(s3, card_id: str, jpeg_bytes: bytes) -> str:
    """Upload compressed image to S3. Returns public HTTPS URL."""
    key = f"{THUMB_PREFIX}{card_id}.jpg"
    s3.put_object(
        Bucket=IMAGE_BUCKET,
        Key=key,
        Body=jpeg_bytes,
        ContentType="image/jpeg",
        CacheControl="max-age=31536000",  # 1 year — images don't change
        Metadata={"source": f"{card_id}.png", "compressed": "480px-q82"},
    )
    return f"https://d21h5hm5jxmc5.cloudfront.net/{key}"


def process_card(s3, card_id: str, force: bool = False) -> tuple[str, str | None, int, int]:
    """Download → compress → upload. Returns (card_id, thumb_url, orig_size, thumb_size)."""
    # Download
    _, raw = download_image(s3, card_id)
    if raw is None:
        return card_id, None, 0, 0

    orig_size = len(raw)

    # Compress
    try:
        jpeg = compress_image(raw)
    except Exception as e:
        print(f"  ❌ Compress failed {card_id}: {e}")
        return card_id, None, orig_size, 0

    thumb_size = len(jpeg)

    # Upload
    try:
        url = upload_to_s3(s3, card_id, jpeg)
    except Exception as e:
        print(f"  ❌ Upload failed {card_id}: {e}")
        return card_id, None, orig_size, thumb_size

    return card_id, url, orig_size, thumb_size


def main():
    print("\n  ✦ Norn Machine — Image Compression Pipeline")
    print("  " + "═" * 50)

    # Load cards
    with open(CARDS_JSON, encoding="utf-8") as f:
        cards = json.load(f)

    card_ids = [c["id"] for c in cards]
    print(f"  Cards to process: {len(card_ids)}")
    print(f"  Target size:      {TARGET_WIDTH}px wide, JPEG q={JPEG_QUALITY}")
    print(f"  S3 destination:   s3://{IMAGE_BUCKET}/{THUMB_PREFIX}")
    print()

    s3 = boto3.client("s3", region_name=REGION)

    results: dict[str, str] = {}
    total_orig = 0
    total_thumb = 0
    errors = []

    print(f"  Processing {len(card_ids)} cards with {WORKERS} workers...")
    print()

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {
            executor.submit(process_card, s3, cid): cid
            for cid in card_ids
        }
        done = 0
        for future in as_completed(futures):
            cid, url, orig, thumb = future.result()
            done += 1
            if url:
                results[cid] = url
                total_orig += orig
                total_thumb += thumb
                ratio = orig / max(thumb, 1)
                print(f"  [{done:3d}/{len(card_ids)}] ✅ {cid}: "
                      f"{orig//1024}KB → {thumb//1024}KB ({ratio:.1f}x)")
            else:
                errors.append(cid)
                print(f"  [{done:3d}/{len(card_ids)}] ❌ {cid}: FAILED")

    print()
    print("  " + "═" * 50)
    print(f"  ✅ Success: {len(results)}/{len(card_ids)}")
    if errors:
        print(f"  ❌ Failed:  {errors}")
    print(f"  Total original:   {total_orig / 1024 / 1024:.1f} MB")
    print(f"  Total compressed: {total_thumb / 1024:.0f} KB ({total_thumb / 1024 / 1024:.1f} MB)")
    if total_thumb > 0:
        print(f"  Overall ratio:    {total_orig / total_thumb:.1f}x compression")

    if not results:
        print("\n❌ No images processed. Aborting cards.json update.")
        return

    # Update cards.json
    updated = 0
    for card in cards:
        cid = card["id"]
        if cid in results:
            card["url"] = results[cid]
            updated += 1

    backup_path = CARDS_JSON.with_suffix(".json.bak")
    import shutil
    shutil.copy(CARDS_JSON, backup_path)
    print(f"\n  📋 Backup saved: {backup_path}")

    with open(CARDS_JSON, "w", encoding="utf-8") as f:
        json.dump(cards, f, ensure_ascii=False, indent=2)

    print(f"  ✅ cards.json updated: {updated} URLs → thumbnail CDN")
    print(f"\n  🌐 Thumbnails available at:")
    print(f"     https://d21h5hm5jxmc5.cloudfront.net/{THUMB_PREFIX}NORN_0001.jpg")
    print()
    print("  Next: run frontend/deploy_to_s3.sh to sync updated cards.json")


if __name__ == "__main__":
    main()

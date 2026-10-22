#!/usr/bin/env python3
"""Precompute inspectable card features; Pillow is needed only for generation.

These are measured pixels and existing trait/motif annotations, not a learned
image encoder or evidence of personality accuracy. Preference training learns
weights separately. No personality coordinates or card identity enter vectors.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ASSETS = ROOT / "the-norn-machine" / "frontend" / "assets" / "cards"
DEFAULT_METADATA = ROOT / "the-norn-machine" / "backend" / "config" / "cards_metadata.json"
DEFAULT_OUTPUT = ROOT / "the-norn-machine" / "backend" / "config" / "preference_features.json"
SCHEMA_VERSION = "norn.features.v1"
GENERATOR_VERSION = "norn.preference-features.v1"
PIXEL_FEATURE_COUNT = 64
IMAGE_SIZE = 64
EXPECTED_IDS = frozenset(f"NORN_{index:04d}" for index in range(1, 257))


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_hash(value):
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Metadata contains duplicate JSON object keys")
        result[key] = value
    return result


def used_metadata(metadata):
    """The only annotations used: normalized trait and motif sets."""
    if not isinstance(metadata, dict) or not metadata:
        raise ValueError("Metadata must contain a nonempty card map")
    selected = {}
    for card_id in sorted(metadata):
        if not isinstance(card_id, str) or not re.fullmatch(r"NORN_[0-9]{4}", card_id):
            raise ValueError("Metadata has an invalid card ID")
        row = metadata[card_id]
        if not isinstance(row, dict):
            raise ValueError("Card metadata must be an object")
        selected[card_id] = {}
        for field in ("traits", "motifs"):
            words = row.get(field)
            if (not isinstance(words, list) or not words
                    or any(not isinstance(word, str) or not word.strip() or len(word) > 200 for word in words)):
                raise ValueError("Each card requires nonempty trait and motif string arrays")
            selected[card_id][field] = sorted({word.strip() for word in words})
    return selected


def pixel_feature_names():
    names = [f"pixel.rgb.{channel}.bin_{index}" for channel in ("red", "green", "blue") for index in range(4)]
    names += [f"pixel.saturation.bin_{index}" for index in range(6)]
    names += [f"pixel.luma.bin_{index}" for index in range(6)]
    names += [f"pixel.luma.grid_{row}_{col}" for row in range(4) for col in range(4)]
    names += [f"pixel.saturation.grid_{row}_{col}" for row in range(4) for col in range(4)]
    names += [f"pixel.edge.orientation_{index}" for index in range(8)]
    return names


def pixel_features(path):
    """Extract exactly 64 interpretable values from a decoded 64x64 image.

    Luma is a Rec.709-weighted sRGB brightness proxy, not linear-light physical
    luminance. Sobel edge orientations are unsigned and magnitude normalized.
    """
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise RuntimeError("Feature regeneration needs Pillow: python -m pip install '.[assets]'") from exc
    with Image.open(path) as original:
        image = ImageOps.exif_transpose(original).convert("RGB").resize(
            (IMAGE_SIZE, IMAGE_SIZE), Image.Resampling.BOX)
        pixels = list(image.get_flattened_data() if hasattr(image, "get_flattened_data") else image.getdata())
    count = len(pixels)
    rgb = [0.0] * 12
    sat_hist, luma_hist = [0.0] * 6, [0.0] * 6
    luma_grid, sat_grid = [0.0] * 16, [0.0] * 16
    lumas = []
    for index, (red, green, blue) in enumerate(pixels):
        for channel, value in enumerate((red, green, blue)):
            rgb[channel * 4 + min(3, value * 4 // 256)] += 1
        maximum, minimum = max(red, green, blue), min(red, green, blue)
        saturation = (maximum - minimum) / maximum if maximum else 0.0
        luma = (2126 * red + 7152 * green + 722 * blue) / (10000 * 255)
        lumas.append(luma)
        sat_hist[min(5, int(saturation * 6))] += 1
        luma_hist[min(5, int(luma * 6))] += 1
        row, col = divmod(index, IMAGE_SIZE)
        cell = (row // 16) * 4 + col // 16
        luma_grid[cell] += luma
        sat_grid[cell] += saturation
    edges = [0.0] * 8
    for row in range(1, IMAGE_SIZE - 1):
        for col in range(1, IMAGE_SIZE - 1):
            center = row * IMAGE_SIZE + col
            tl, top, tr = (lumas[center - IMAGE_SIZE + offset] for offset in (-1, 0, 1))
            left, right = lumas[center - 1], lumas[center + 1]
            bl, bottom, br = (lumas[center + IMAGE_SIZE + offset] for offset in (-1, 0, 1))
            horizontal = (tr - tl) + 2 * (right - left) + (br - bl)
            vertical = (bl - tl) + 2 * (bottom - top) + (br - tr)
            magnitude = math.hypot(horizontal, vertical)
            if magnitude > 1e-12:
                orientation = math.atan2(vertical, horizontal) % math.pi
                edges[min(7, int(orientation * 8 / math.pi))] += magnitude
    edge_total = math.fsum(edges)
    return ([value / count for value in rgb + sat_hist + luma_hist]
            + [value / 256 for value in luma_grid + sat_grid]
            + [value / edge_total if edge_total else 0.0 for value in edges])


def feature_groups(trait_count, motif_count):
    boundaries = (("rgb_histogram", 0, 12), ("saturation_histogram", 12, 18),
                  ("luma_histogram", 18, 24), ("luma_grid", 24, 40),
                  ("saturation_grid", 40, 56), ("edge_orientation", 56, 64))
    groups = [{"name": name, "start": start, "end": end, "weight": 1 / 12}
              for name, start, end in boundaries]
    groups += [{"name": "traits", "start": 64, "end": 64 + trait_count, "weight": 0.25},
               {"name": "motifs", "start": 64 + trait_count,
                "end": 64 + trait_count + motif_count, "weight": 0.25}]
    return groups


def normalize_vectors(raw_cards, groups):
    """Fit pixel-only catalog stats and preserve sparse semantic zeros."""
    rows = list(raw_cards.values())
    width = len(rows[0])
    if any(len(row) != width for row in rows):
        raise ValueError("Feature vectors have inconsistent widths")
    means, scales = [0.0] * width, [1.0] * width
    for col in range(PIXEL_FEATURE_COUNT):
        mean = math.fsum(row[col] for row in rows) / len(rows)
        scale = math.sqrt(math.fsum((row[col] - mean) ** 2 for row in rows) / len(rows))
        means[col] = round(mean, 14)
        scales[col] = round(scale, 14) if scale > 1e-12 else 1.0
    result, group_norms, final_norms = {}, {}, {}
    for card_id, row in raw_cards.items():
        normalized = [(value - means[index]) / scales[index] for index, value in enumerate(row)]
        norms = []
        for group in groups:
            start, end = group["start"], group["end"]
            norm = round(math.sqrt(math.fsum(value * value for value in normalized[start:end])), 14)
            norms.append(norm)
            multiplier = math.sqrt(group["weight"]) / norm if norm else 0.0
            normalized[start:end] = [value * multiplier for value in normalized[start:end]]
        norm = round(math.sqrt(math.fsum(value * value for value in normalized)), 14)
        if not norm:
            raise ValueError("Cannot normalize an empty feature vector")
        vector = [round(value / norm, 12) if value else 0.0 for value in normalized]
        if not all(math.isfinite(value) for value in vector):
            raise ValueError("Feature generation produced nonfinite values")
        result[card_id], group_norms[card_id], final_norms[card_id] = vector, norms, norm
    return result, {"method": "pixel64_population_zscore_then_group_l2_weighted_then_global_l2; semantic_binary_uncentered",
                    "continuous_end": PIXEL_FEATURE_COUNT, "means": means, "scales": scales,
                    "groups": groups, "card_group_norms": group_norms,
                    "card_final_norms": final_norms,
                    "zero_scale_policy": "Population standard deviation <=1e-12 uses scale 1",
                    "rounding": {"stats_decimal_places": 14, "vector_decimal_places": 12}}


def pca_projection(cards, iterations=48):
    """Fixed feature-similarity coordinates; never used to train or rank.

    Center the 979-D vectors implicitly during covariance multiplication, so
    sparse semantic zeros remain sparse in the actual training catalog.
    """
    ids = sorted(cards)
    width, count = len(cards[ids[0]]), len(ids)
    sparse = [[(index, value) for index, value in enumerate(cards[card_id]) if value]
              for card_id in ids]
    means = [math.fsum(cards[card_id][index] for card_id in ids) / count for index in range(width)]
    variances = [max(0.0, math.fsum(cards[card_id][index] ** 2 for card_id in ids) / count - means[index] ** 2)
                 for index in range(width)]
    total_variance = math.fsum(variances)
    components, scores, eigenvalues = [], [], []

    def multiply(vector):
        center = math.fsum(value * mean for value, mean in zip(vector, means))
        products = [math.fsum(vector[index] * value for index, value in row) - center for row in sparse]
        average = math.fsum(products) / count
        result = [-mean * average for mean in means]
        for row, product in zip(sparse, products):
            for index, value in row:
                result[index] += value * product / count
        return result

    def orthogonalize(vector):
        for previous in components:
            coefficient = math.fsum(a * b for a, b in zip(vector, previous))
            vector = [value - coefficient * previous[index] for index, value in enumerate(vector)]
        norm = math.sqrt(math.fsum(value * value for value in vector))
        return [value / norm for value in vector] if norm > 1e-14 else None

    for axis in range(2):
        residual = [max(0.0, variance - math.fsum(eigenvalues[item] * components[item][index] ** 2
                                                 for item in range(len(components))))
                    for index, variance in enumerate(variances)]
        vector = None
        for seed in sorted(range(width), key=lambda index: (-residual[index], index)):
            initial = [0.0] * width
            initial[seed] = 1.0
            candidate = orthogonalize(initial)
            if candidate is not None and any(abs(value) > 1e-14 for value in multiply(candidate)):
                vector = candidate
                break
        if vector is None or total_variance <= 1e-14:
            components.append([0.0] * width)
            eigenvalues.append(0.0)
            scores.append([0.0] * count)
            continue
        for _ in range(iterations):
            next_vector = orthogonalize(multiply(vector))
            if next_vector is None:
                break
            vector = next_vector
        pivot = min(range(width), key=lambda index: (-abs(vector[index]), index))
        if vector[pivot] < 0:
            vector = [-value for value in vector]
        center = math.fsum(value * mean for value, mean in zip(vector, means))
        values = [math.fsum(vector[index] * value for index, value in row) - center for row in sparse]
        components.append(vector)
        scores.append(values)
        eigenvalues.append(math.fsum(value * value for value in values) / count)
    order = sorted(range(2), key=lambda index: (-eigenvalues[index], index))
    components = [components[index] for index in order]
    scores = [scores[index] for index in order]
    eigenvalues = [eigenvalues[index] for index in order]
    scale = max((abs(value) for values in scores for value in values), default=0.0) or 1.0
    return {"method": "pca2", "iterations": iterations,
            "initialization": "Largest residual-variance feature axis; deterministic index tie-break",
            "component_sign": "Largest absolute feature loading positive; index tie-break",
            "feature_means": [round(value, 14) for value in means],
            "components": [[round(value, 14) for value in vector] for vector in components],
            "total_variance": round(total_variance, 14),
            "explained_variance_ratio": [round(value / total_variance, 12) if total_variance else 0.0 for value in eigenvalues],
            "coordinate_scale": round(scale, 14), "coordinate_extent": [-1.0, 1.0],
            "points": {card_id: [round(scores[axis][index] / scale, 12) for axis in range(2)]
                       for index, card_id in enumerate(ids)},
            "purpose": "Display-only descriptor similarity. Not preference scores, personality axes, or a learned image encoder."}


def build_catalog(assets_dir=DEFAULT_ASSETS, metadata_path=DEFAULT_METADATA, *, require_256=True):
    metadata = json.loads(Path(metadata_path).read_text(encoding="utf-8"), object_pairs_hook=_no_duplicates)
    used = used_metadata(metadata)
    if require_256 and set(used) != EXPECTED_IDS:
        raise ValueError("The production feature catalog requires exactly NORN_0001 through NORN_0256")
    traits = sorted({word for row in used.values() for word in row["traits"]})
    motifs = sorted({word for row in used.values() for word in row["motifs"]})
    names = pixel_feature_names() + ["trait." + word for word in traits] + ["motif." + word for word in motifs]
    raw_cards, asset_hashes = {}, {}
    for card_id, row in used.items():
        path = Path(assets_dir) / (card_id + ".webp")
        asset_hashes[card_id] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        values = pixel_features(path)
        values += [float(word in row["traits"]) for word in traits]
        values += [float(word in row["motifs"]) for word in motifs]
        raw_cards[card_id] = values
    cards, normalization = normalize_vectors(raw_cards, feature_groups(len(traits), len(motifs)))
    catalog = {"schema_version": SCHEMA_VERSION, "generator_version": GENERATOR_VERSION,
               "feature_names": names, "cards": cards,
               "source_assets_hash": content_hash(asset_hashes),
               "source_metadata_hash": content_hash(used),
               "normalization": normalization,
               "extraction": {"image_size": [64, 64], "resize": "Pillow BOX after EXIF transpose and RGB conversion",
                              "pixel_feature_count": PIXEL_FEATURE_COUNT,
                              "semantic_features": "Sorted unique traits and motifs; binary, never centered",
                              "luma": "Rec.709-weighted sRGB brightness proxy; no linear-light transform",
                              "edges": "3x3 Sobel, unsigned angle [0,pi), 8 magnitude-normalized bins; magnitude <=1e-12 ignored",
                              "encoder": "Hand-defined inspectable features; not a learned image encoder",
                              "excluded_metadata": ["coords", "type", "group", "rationale", "scene", "card_id_as_feature"]}}
    catalog["projection"] = pca_projection(cards)
    catalog["feature_version"] = content_hash(catalog)
    return catalog


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets-dir", type=Path, default=DEFAULT_ASSETS)
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        catalog = build_catalog(args.assets_dir, args.metadata)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_name(args.output.name + ".tmp")
        try:
            temporary.write_text(canonical_json(catalog) + "\n", encoding="utf-8")
            temporary.replace(args.output)
        finally:
            temporary.unlink(missing_ok=True)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"Feature generation failed: {exc}\n")
    print(f"Prepared {len(catalog['cards'])} cards, {len(catalog['feature_names'])} features: {catalog['feature_version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

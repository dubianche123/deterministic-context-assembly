"""Frozen out-of-sample pixel projection for the original Norn card deck.

The v4 catalog stores eight PCA coordinates but not a transform for a new
image. This companion artifact recovers and freezes that transform using only
the original 256 source vectors. Fitting needs NumPy; loading and projecting
an already measured 64-value pixel vector use only the standard library.

The returned eight coordinates have the same scale as choice_features.json.
They are not multiplied by the runtime's PIXEL_SCALE=0.5. No four-axis values,
types, motifs, or preferences are inferred for an unseen image.
"""

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re


SCHEMA_VERSION = "norn.pixel-projection.v1"
METHOD = "original_deck_pixel64_group_normalization_then_frozen_pca8"
NORMALIZATION_METHOD = "pixel64_population_zscore_then_group_l2_weighted_then_global_l2; semantic_binary_uncentered"
EXPECTED_IDS = tuple(f"NORN_{number:04d}" for number in range(1, 257))
PIXEL_COUNT = 64
COMPONENT_COUNT = 8
RECONSTRUCTION_TOLERANCE = 1e-8
ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "the-norn-machine" / "backend" / "config"
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_BOUNDS = ((0, 12), (12, 18), (18, 24), (24, 40), (40, 56), (56, 64))
_OUTPUT = {"feature_names": [f"pixel.pc{index + 1}" for index in range(COMPONENT_COUNT)],
           "component_scale": "unit source-deck standard deviation; not multiplied by runtime PIXEL_SCALE",
           "axes": "unsupported_without_independent_metadata"}


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_hash(value):
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _vector(values, size, label, *, positive=False):
    if (type(values) is not list or len(values) != size
            or not all(_finite(value) and (not positive or value > 0) for value in values)):
        raise ValueError(f"Invalid {label}")
    return values


def _document(value, default):
    if value is None:
        value = default
    if isinstance(value, (str, Path)):
        value = json.loads(Path(value).read_text(encoding="utf-8"))
    if type(value) is not dict:
        raise ValueError("A feature source must be a JSON object")
    return deepcopy(value)


def _check_hash(document, field):
    saved = document.get(field)
    if (type(saved) is not str or not _HASH.fullmatch(saved)
            or saved != content_hash({key: value for key, value in document.items() if key != field})):
        raise ValueError(f"Invalid {field}")


def _pixel_names():
    return ([f"pixel.rgb.{channel}.bin_{index}" for channel in ("red", "green", "blue") for index in range(4)]
            + [f"pixel.saturation.bin_{index}" for index in range(6)]
            + [f"pixel.luma.bin_{index}" for index in range(6)]
            + [f"pixel.luma.grid_{row}_{col}" for row in range(4) for col in range(4)]
            + [f"pixel.saturation.grid_{row}_{col}" for row in range(4) for col in range(4)]
            + [f"pixel.edge.orientation_{index}" for index in range(8)])


def _groups(groups):
    if type(groups) is not list or len(groups) != len(_BOUNDS):
        raise ValueError("Invalid pixel normalization groups")
    for group, (start, end) in zip(groups, _BOUNDS):
        if (type(group) is not dict or group.get("start") != start or group.get("end") != end
                or not _finite(group.get("weight")) or group["weight"] != 1 / 12):
            raise ValueError("Unsupported pixel normalization groups")


def verify_projection(projection):
    """Check content identity and shape, not authorship or predictive validity."""
    if type(projection) is not dict or projection.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported pixel projection")
    _check_hash(projection, "projection_version")
    if projection.get("method") != METHOD:
        raise ValueError("Unsupported pixel projection method")
    if projection.get("output") != _OUTPUT:
        raise ValueError("Unsupported pixel projection output contract")
    source = projection.get("source")
    if (type(source) is not dict or source.get("fit_card_ids") != list(EXPECTED_IDS)
            or source.get("pixel_feature_names") != _pixel_names()
            or any(type(source.get(key)) is not str or not _HASH.fullmatch(source[key])
                   for key in ("feature_version", "source_assets_hash", "choice_feature_version"))):
        raise ValueError("Invalid projection source identity")
    normalization = projection.get("normalization")
    if (type(normalization) is not dict or normalization.get("method") != NORMALIZATION_METHOD
            or normalization.get("semantic_normalization_energy") != 0.5
            or normalization.get("stats_decimal_places") != 14
            or normalization.get("vector_decimal_places") != 12):
        raise ValueError("Unsupported source normalization")
    _vector(normalization.get("means"), PIXEL_COUNT, "source pixel means")
    _vector(normalization.get("scales"), PIXEL_COUNT, "source pixel scales", positive=True)
    _groups(normalization.get("groups"))
    pca = projection.get("pca")
    if type(pca) is not dict:
        raise ValueError("Missing frozen PCA")
    _vector(pca.get("means"), PIXEL_COUNT, "PCA means")
    _vector(pca.get("scales"), PIXEL_COUNT, "PCA scales", positive=True)
    _vector(pca.get("score_scales"), COMPONENT_COUNT, "PCA score scales", positive=True)
    explained = _vector(pca.get("explained_variance_ratio"), COMPONENT_COUNT, "PCA explained variance")
    if any(value < 0 or value > 1 for value in explained) or math.fsum(explained) > 1 + 1e-9:
        raise ValueError("Invalid PCA explained variance")
    loadings = pca.get("loadings")
    if type(loadings) is not list or len(loadings) != COMPONENT_COUNT:
        raise ValueError("Invalid PCA loadings")
    for row in loadings:
        _vector(row, PIXEL_COUNT, "PCA loading")
    for index, row in enumerate(loadings):
        for previous in range(index + 1):
            dot = math.fsum(a * b for a, b in zip(row, loadings[previous]))
            if abs(dot - float(index == previous)) > 1e-8:
                raise ValueError("PCA loadings are not orthonormal")
    check = projection.get("compatibility")
    if (type(check) is not dict or check.get("source_card_count") != len(EXPECTED_IDS)
            or check.get("absolute_tolerance") != RECONSTRUCTION_TOLERANCE
            or not _finite(check.get("max_absolute_error"))
            or not 0 <= check["max_absolute_error"] <= RECONSTRUCTION_TOLERANCE):
        raise ValueError("Projection does not reproduce the source choice catalog")
    return True


def _project_normalized(values, projection):
    pca = projection["pca"]
    standardized = [(value - mean) / scale for value, mean, scale
                    in zip(values, pca["means"], pca["scales"])]
    return [math.fsum(value * loading for value, loading in zip(standardized, row)) / scale
            for row, scale in zip(pca["loadings"], pca["score_scales"])]


def project_normalized_pixels(values, projection):
    """Project an original-format, group-normalized pixel64 vector to PCA8."""
    verify_projection(projection)
    _vector(values, PIXEL_COUNT, "normalized pixel vector")
    result = _project_normalized(values, projection)
    if not all(math.isfinite(value) for value in result):
        raise ValueError("Pixel projection produced nonfinite coordinates")
    return result


def normalize_pixels(values, projection):
    """Apply frozen source normalization to raw measured pixels, without labels.

    The old catalog's two nonempty semantic groups each have L2 energy 0.25.
    Their combined *constant* 0.5 is retained only to reproduce the pixel scale
    before PCA. It neither assigns a semantic feature nor assumes a type for a
    new image. Source rounding can differ at a final last decimal from the old
    full-vector implementation; compatibility is checked to 1e-8 after PCA.
    """
    verify_projection(projection)
    _vector(values, PIXEL_COUNT, "raw pixel vector")
    if any(value < 0 or value > 1 for value in values):
        raise ValueError("Raw measured pixel features must lie in [0, 1]")
    config = projection["normalization"]
    result = [(value - mean) / scale for value, mean, scale
              in zip(values, config["means"], config["scales"])]
    for group in config["groups"]:
        start, end = group["start"], group["end"]
        norm = round(math.sqrt(math.fsum(value * value for value in result[start:end])), 14)
        multiplier = math.sqrt(group["weight"]) / norm if norm else 0.0
        result[start:end] = [value * multiplier for value in result[start:end]]
    norm = round(math.sqrt(math.fsum(value * value for value in result) + 0.5), 14)
    return [round(value / norm, 12) if value else 0.0 for value in result]


def project_pixels(values, projection):
    """Project a new image's raw pixel64 values using a previously frozen fit.

    Returns eight unscaled pixel PCs only. A hybrid consumer needs separately
    supplied, justified four-axis metadata; this function never invents it.
    """
    result = _project_normalized(normalize_pixels(values, projection), projection)
    if not all(math.isfinite(value) for value in result):
        raise ValueError("Pixel projection produced nonfinite coordinates")
    return result


def fit_projection(source_features=None, choice_features=None):
    """Freeze the original-deck transform, refusing additional fitting cards.

    Holdout images and choices are not accepted by this API. The source must be
    the hash-linked original 256-card feature/choice catalogs. Fitting recovers
    the full PCA vectors and aligns their signs to the existing choice catalog.
    The saved transform must reproduce every existing eight-component vector.
    """
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("Freezing a pixel projection needs NumPy; applying it does not") from exc
    source = _document(source_features, CONFIG / "preference_features.json")
    choice = _document(choice_features, CONFIG / "choice_features.json")
    for document in (source, choice):
        _check_hash(document, "feature_version")
    if (source.get("schema_version") != "norn.features.v1"
            or source.get("generator_version") != "norn.preference-features.v1"
            or choice.get("schema_version") != "norn.choice-features.v1"
            or choice.get("generator_version") != "norn.choice-features.generator.v1"
            or choice.get("pixel_source", {}).get("feature_version") != source["feature_version"]
            or sorted(source.get("cards", {})) != list(EXPECTED_IDS)
            or sorted(choice.get("cards", {})) != list(EXPECTED_IDS)
            or source.get("feature_names", [])[:PIXEL_COUNT] != _pixel_names()):
        raise ValueError("Projection requires the hash-linked original 256-card catalogs")
    normalization = source.get("normalization", {})
    if (normalization.get("method") != NORMALIZATION_METHOD
            or normalization.get("continuous_end") != PIXEL_COUNT
            or normalization.get("rounding") != {"stats_decimal_places": 14, "vector_decimal_places": 12}
            or len(normalization.get("groups", [])) != 8
            or any(group.get("weight") != 0.25 for group in normalization["groups"][6:])):
        raise ValueError("Unsupported original pixel normalization")
    _groups(normalization["groups"][:6])
    width = len(source["feature_names"])
    for card in EXPECTED_IDS:
        row = _vector(source["cards"][card], width, "source card vector")
        for group in normalization["groups"][6:]:
            if not any(row[group["start"]:group["end"]]):
                raise ValueError("Source semantic groups must have nonzero normalization energy")
        _vector(choice["cards"][card].get("pixel"), COMPONENT_COUNT, "target pixel components")
    raw = np.asarray([source["cards"][card][:PIXEL_COUNT] for card in EXPECTED_IDS], dtype=float)
    means, scales = raw.mean(0), raw.std(0) + 1e-12
    standardized = (raw - means) / scales
    _, singular, vt = np.linalg.svd(standardized, full_matrices=False)
    loadings = vt[:COMPONENT_COUNT].copy()
    names = source["feature_names"][:PIXEL_COUNT]
    for index, component in enumerate(choice.get("components", [])):
        if index >= COMPONENT_COUNT or component.get("name") != f"pixel.pc{index + 1}":
            raise ValueError("Invalid target PCA component identity")
        anchors = component.get("top_loadings", [])
        if not anchors:
            raise ValueError("Target PCA needs source loading anchors")
        anchor = anchors[0]
        position = names.index(anchor["feature"])
        if loadings[index, position] * anchor["loading"] < 0:
            loadings[index] *= -1
    if len(choice.get("components", [])) != COMPONENT_COUNT:
        raise ValueError("Target catalog needs eight PCA components")
    scores = standardized @ loadings.T
    score_scales = scores.std(0)
    if np.any(score_scales <= 0) or not np.all(np.isfinite(score_scales)):
        raise ValueError("Source has degenerate PCA components")
    explained = singular ** 2 / float((singular ** 2).sum())
    projection = {
        "schema_version": SCHEMA_VERSION,
        "method": METHOD,
        "source": {"feature_version": source["feature_version"],
                   "source_assets_hash": source["source_assets_hash"],
                   "choice_feature_version": choice["feature_version"],
                   "fit_card_ids": list(EXPECTED_IDS), "pixel_feature_names": names},
        "normalization": {"method": NORMALIZATION_METHOD,
                          "means": normalization["means"][:PIXEL_COUNT],
                          "scales": normalization["scales"][:PIXEL_COUNT],
                          "groups": deepcopy(normalization["groups"][:6]),
                          "semantic_normalization_energy": 0.5,
                          "stats_decimal_places": 14, "vector_decimal_places": 12},
        "pca": {"means": means.tolist(), "scales": scales.tolist(), "loadings": loadings.tolist(),
                "score_scales": score_scales.tolist(),
                "explained_variance_ratio": explained[:COMPONENT_COUNT].tolist()},
        "compatibility": {"source_card_count": len(EXPECTED_IDS),
                          "absolute_tolerance": RECONSTRUCTION_TOLERANCE,
                          "max_absolute_error": 0.0},
        "output": deepcopy(_OUTPUT),
        "scope": "Fit original deck only; no holdout images, choices, type assignment, or predictive validation",
    }
    error = max(abs(value - expected) for card in EXPECTED_IDS
                for value, expected in zip(_project_normalized(source["cards"][card][:PIXEL_COUNT], projection),
                                           choice["cards"][card]["pixel"]))
    projection["compatibility"]["max_absolute_error"] = error
    projection["projection_version"] = content_hash(projection)
    verify_projection(projection)
    return projection

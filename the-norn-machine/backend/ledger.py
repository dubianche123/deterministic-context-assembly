"""Inspectable accounting for the authoritative Fast Thinker calculation.

The builder receives the intermediates used by the scorer. It does not score
cards again. Contributions are algorithmic allocations, not psychological
causes, and are kept at full precision until the final coordinate is rounded.
"""

import math

if __package__:
    from . import numeric
else:
    import numeric


ATTRIBUTION_SCHEMA_VERSION = "norn.attribution.v1"
ALGORITHM_VERSION = "norn.scoring.v1"
PORTABLE_ATTRIBUTION_SCHEMA_VERSION = "norn.attribution.v2"
PORTABLE_ALGORITHM_VERSION = "norn.scoring.v3"
DIMS = ("E_I", "S_N", "T_F", "J_P")
ABSOLUTE_TOLERANCE = 1e-12


def build_attribution(*, total_rounds, total_weight, total_base_weight,
                      rounds, duration_normalization, selections,
                      revision_events, revision, max_jp_nudge, actual_jp_delta,
                      pre_revision, unrounded_final, final):
    """Describe already-computed values without changing the scoring path."""
    selection_rows = []
    for source in selections:
        row = dict(source)
        row["normalized_weight"] = row["weight"] / total_weight
        row["contribution"] = {
            dim: row["weighted_coords"][dim] / total_weight for dim in DIMS
        }
        row["base_contribution"] = {
            dim: row["coords"][dim] * row["base_weight"] / total_base_weight
            for dim in DIMS
        }
        row["hesitation_effect"] = {
            dim: row["contribution"][dim] - row["base_contribution"][dim]
            for dim in DIMS
        }
        selection_rows.append(row)

    applied = total_weight != 0
    # This is the actual revision adjustment used by Fast Thinker, including
    # its existing two-decimal strength. Empty readings intentionally stay 0.
    total_jp_delta = actual_jp_delta
    revision_rows = []
    allocated = []
    for index, event in enumerate(revision_events):
        share = total_jp_delta / len(revision_events)
        # Keep equal shares, with any float allocation remainder on the last
        # event. This is session-level accounting, not per-event causality.
        if index == len(revision_events) - 1:
            share = total_jp_delta - math.fsum(allocated)
        allocated.append(share)
        revision_rows.append({
            **event,
            "delta": {dim: share if dim == "J_P" else 0.0 for dim in DIMS},
        })

    dimensions = {}
    for dim in DIMS:
        contributions = [row["contribution"][dim] for row in selection_rows]
        revisions = [row["delta"][dim] for row in revision_rows]
        selection_sum = math.fsum(contributions)
        revision_sum = math.fsum(revisions)
        # Preserve the historical sequential float accumulator, including its
        # tiny difference from summing normalized contributions with fsum.
        summation_delta = unrounded_final[dim] - math.fsum(contributions + revisions)
        dimensions[dim] = {
            "selection_sum": selection_sum,
            "pre_revision": pre_revision[dim],
            "revision_delta": revision_sum,
            "summation_delta": summation_delta,
            "unrounded_final": unrounded_final[dim],
            "rounding_delta": final[dim] - unrounded_final[dim],
            "final": final[dim],
        }

    portable = numeric.current_contract() == numeric.PORTABLE_CONTRACT
    return {
        "schema_version": PORTABLE_ATTRIBUTION_SCHEMA_VERSION if portable else ATTRIBUTION_SCHEMA_VERSION,
        "algorithm_version": PORTABLE_ALGORITHM_VERSION if portable else ALGORITHM_VERSION,
        **({"numerical_contract": numeric.PORTABLE_CONTRACT} if portable else {}),
        "total_rounds": total_rounds,
        "total_weight": total_weight,
        "total_base_weight": total_base_weight,
        "weight_summation_delta": total_weight - math.fsum(row["weight"] for row in selection_rows),
        "base_weight_summation_delta": total_base_weight - math.fsum(row["base_weight"] for row in selection_rows),
        "rounds": rounds,
        "duration_normalization": duration_normalization,
        "selections": selection_rows,
        "revision": {
            "total": revision["total"],
            "strength": revision["strength"],
            "max_jp_nudge": max_jp_nudge,
            "total_jp_delta": total_jp_delta,
            "applied": applied,
            "ignored_reason": None if applied else "no_valid_selections",
            "allocation": "equal_share_of_session_nudge",
            "allocation_note": "Algorithmic accounting, not psychological causality; "
                               "the last event carries any floating-point remainder.",
            "events": revision_rows,
        },
        "dimensions": dimensions,
        "reconstruction": {
            "formula": "fsum(selection contributions, revision deltas, summation_delta, rounding_delta)",
            "absolute_tolerance": ABSOLUTE_TOLERANCE,
            "summation_delta_note": "Reconciles the original sequential floating-point accumulator.",
            "rounding_delta_note": "Only the final coordinate is rounded to two decimal places.",
        },
    }


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"Invalid attribution number: {label}")
    return value


def _close(actual, expected, label):
    _number(actual, label)
    _number(expected, label)
    if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=ABSOLUTE_TOLERANCE):
        raise ValueError(f"Attribution mismatch: {label}")


def _parts(attribution):
    if not isinstance(attribution, dict):
        raise ValueError("Attribution must be an object")
    schema = attribution.get("schema_version")
    if schema not in (ATTRIBUTION_SCHEMA_VERSION, PORTABLE_ATTRIBUTION_SCHEMA_VERSION):
        raise ValueError("Unsupported attribution schema")
    expected_algorithm = PORTABLE_ALGORITHM_VERSION if schema == PORTABLE_ATTRIBUTION_SCHEMA_VERSION else ALGORITHM_VERSION
    if attribution.get("algorithm_version") != expected_algorithm:
        raise ValueError("Unsupported scoring algorithm")
    if schema == PORTABLE_ATTRIBUTION_SCHEMA_VERSION and attribution.get("numerical_contract") != numeric.PORTABLE_CONTRACT:
        raise ValueError("Unsupported attribution numerical contract")
    selections = attribution["selections"]
    revisions = attribution["revision"]["events"]
    dimensions = attribution["dimensions"]
    if not isinstance(selections, list) or not isinstance(revisions, list):
        raise ValueError("Attribution entries must be lists")
    if not isinstance(dimensions, dict) or set(dimensions) != set(DIMS):
        raise ValueError("Attribution must contain all four dimensions")
    return selections, revisions, dimensions


def reconstruct_coords(attribution):
    """Reconstruct all coordinates from entries, never from stored final values.

    Malformed or nonfinite values raise ValueError. Use verify_attribution when
    the redundant accounting fields and expected result must also be checked.
    """
    try:
        selections, revisions, dimensions = _parts(attribution)
        result = {}
        for dim in DIMS:
            terms = [_number(row["contribution"][dim], "selection contribution")
                     for row in selections]
            terms.extend(_number(row["delta"][dim], "revision delta") for row in revisions)
            terms.extend(_number(dimensions[dim][key], key)
                         for key in ("summation_delta", "rounding_delta"))
            result[dim] = math.fsum(terms)
        return result
    except (KeyError, TypeError, OverflowError) as exc:
        raise ValueError("Malformed attribution") from exc


def verify_attribution(attribution, expected_coords):
    """Validate contribution arithmetic and reconstruction, or raise ValueError.

    The comparison uses a fixed 1e-12 absolute tolerance. It cannot authenticate
    an external document; profile hashing and metadata provenance are separate.
    """
    try:
        selections, revisions, dimensions = _parts(attribution)
        reconstructed = reconstruct_coords(attribution)
        if not isinstance(expected_coords, dict) or set(expected_coords) != set(DIMS):
            raise ValueError("Expected coordinates must contain all four dimensions")
        total_weight = _number(attribution["total_weight"], "total_weight")
        total_base = _number(attribution["total_base_weight"], "total_base_weight")
        if total_weight < 0 or total_base < 0 or (selections and (total_weight == 0 or total_base == 0)):
            raise ValueError("Invalid attribution total weight")
        for total, key, residual_key in (
            (total_weight, "weight", "weight_summation_delta"),
            (total_base, "base_weight", "base_weight_summation_delta"),
        ):
            values = [_number(row[key], key) for row in selections]
            residual = _number(attribution[residual_key], residual_key)
            _close(residual, total - math.fsum(values), residual_key)
            if abs(residual) > len(values) * math.ulp(max(abs(total), 1.0)):
                raise ValueError("Attribution weight residual exceeds float accumulation error")
            _close(total, math.fsum(values + [residual]), key + " reconstructed total")
        event_ids = set()
        for row in selections + revisions:
            event_id = row["event_id"]
            if not isinstance(event_id, str) or not event_id or event_id in event_ids:
                raise ValueError("Attribution event IDs must be nonempty and unique")
            event_ids.add(event_id)
        for row in selections:
            weight = _number(row["weight"], "weight")
            base = _number(row["base_weight"], "base_weight")
            multiplier = _number(row["hesitation_multiplier"], "hesitation_multiplier")
            if weight <= 0 or base <= 0 or multiplier < 1:
                raise ValueError("Invalid attribution selection weight")
            _close(weight, base * multiplier, "selection weight factors")
            _close(row["normalized_weight"], weight / total_weight, "normalized_weight")
            for dim in DIMS:
                coordinate = _number(row["coords"][dim], "coordinate")
                _close(row["weighted_coords"][dim], coordinate * weight, "weighted_coords")
                _close(row["contribution"][dim], row["weighted_coords"][dim] / total_weight, "contribution")
                _close(row["base_contribution"][dim], coordinate * base / total_base, "base_contribution")
                _close(row["hesitation_effect"][dim],
                       row["contribution"][dim] - row["base_contribution"][dim], "hesitation_effect")
        revision = attribution["revision"]
        if len(revisions) != revision["total"]:
            raise ValueError("Attribution revision count mismatch")
        if revision["applied"] is not bool(selections):
            raise ValueError("Attribution revision application mismatch")
        total_delta = _number(revision["total_jp_delta"], "total_jp_delta")
        allocated = []
        for index, row in enumerate(revisions):
            share = total_delta / len(revisions)
            if index == len(revisions) - 1:
                share = total_delta - math.fsum(allocated)
            allocated.append(share)
            for dim in DIMS:
                _close(row["delta"][dim], share if dim == "J_P" else 0, "revision event allocation")
        for dim in DIMS:
            info = dimensions[dim]
            selection_sum = math.fsum(row["contribution"][dim] for row in selections)
            revision_sum = math.fsum(row["delta"][dim] for row in revisions)
            _close(info["selection_sum"], selection_sum, "selection_sum")
            _close(info["revision_delta"], revision_sum, "revision_delta")
            _close(revision_sum, revision["total_jp_delta"] if dim == "J_P" else 0, "revision total")
            _close(info["pre_revision"], selection_sum, "pre_revision")
            _close(info["unrounded_final"], info["pre_revision"] + revision_sum, "unrounded_final")
            _close(info["summation_delta"],
                   info["unrounded_final"] - math.fsum([selection_sum, revision_sum]), "summation_delta")
            _close(info["rounding_delta"], info["final"] - info["unrounded_final"], "rounding_delta")
            _close(info["final"], round(info["unrounded_final"], 2), "final rounding")
            _close(reconstructed[dim], info["final"], "reconstructed final")
            _close(reconstructed[dim], expected_coords[dim], "expected coordinate")
        return True
    except (KeyError, TypeError, OverflowError) as exc:
        raise ValueError("Malformed attribution") from exc

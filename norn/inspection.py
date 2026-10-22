"""Small graph transport; full inspection and replay documents stay lossless."""

from copy import deepcopy


COMPACT_INSPECTION_VERSION = "norn.inspection.compact.v1"


def _pick(value, names):
    return {name: deepcopy(value[name]) for name in names if name in value}


def _usage(value):
    # Vendor raw payloads can dwarf the chart. Keep normalized observations,
    # including explicit zero/unknown markers, for the visible cache summary.
    return {name: deepcopy(item) for name, item in (value or {}).items()
            if name != "raw"}


def compact_inspection(result):
    """Project an inspection for charts without altering any canonical facts.

    Omitted fields are deliberately absent, never presented as complete empty
    journals, model parameters or ledgers. A full request remains available for
    technical disclosures, and export is always the original replay document.
    """
    profile = result.get("profile", {})
    analysis = profile.get("analysis", {})
    preference = result.get("preference") or analysis.get("preference") or {}
    summary = _pick(preference, (
        "status", "trained", "observation_count", "pair_count", "possible_pair_count",
        "omitted_pair_count", "source_count", "duplicates_ignored", "include_open",
        "provisional", "feature_count", "model_version", "score_semantics"))
    summary["top_features"] = deepcopy(preference.get("top_features", [])[:5])
    summary["recommendations"] = [_pick(row, ("card_id", "score", "score_kind"))
                                  for row in preference.get("recommendations", [])[:3]]
    validation = preference.get("validation") or {}
    summary["validation"] = _pick(validation, (
        "status", "reason", "method", "pairwise_accuracy", "log_loss", "random_baseline",
        "beats_random", "train_observation_count", "holdout_observation_count",
        "train_pair_count", "holdout_pair_count", "purged_observation_count",
        "train_independent_group_count", "holdout_independent_group_count"))
    if "guard" in preference:
        summary["guard"] = _pick(preference["guard"], (
            "status", "reason", "policy_version", "log_evalue", "log_threshold",
            "evidence_rounds", "evidence_card_choices", "weight_precision", "training_rounds"))
        summary["effective"] = _pick(preference.get("effective") or {}, ("active", "uncertainty"))
        summary["validation"].update(_pick(validation, (
            "stop_only_baseline", "beats_stop_only", "beats_baselines", "holdout_choice_count")))
    slim_profile = _pick(profile, (
        "schema_version", "source", "provisional", "algorithm_version", "metadata_version",
        "events_hash", "profile_hash", "feature_version"))
    slim_profile["analysis"] = _pick(analysis, (
        "total_rounds", "total_selections", "data_density", "analysis_kind"))
    slim_profile["analysis"]["choice_revision"] = _pick(analysis.get("choice_revision") or {}, ("total",))
    compact = _pick(result, (
        "profile_hash", "complete", "coords_avg", "preference_map", "mode", "route_reason",
        "limits", "turn_count", "max_turns", "verified"))
    compact.update(transport=COMPACT_INSPECTION_VERSION, profile=slim_profile, preference=summary)
    compact["metrics"] = _pick(result.get("metrics") or {}, (
        "raw_bytes", "prompt_bytes", "estimated_input_tokens", "raw_estimated_input_tokens",
        "token_estimate_method", "estimate_scope", "render_requests", "provider_calls"))
    metrics_usage = (result.get("metrics") or {}).get("usage")
    compact["metrics"]["usage"] = _usage(metrics_usage) if metrics_usage is not None else None
    compact["calls"] = []
    for call in result.get("calls") or []:
        row = _pick(call, (
            "task", "provider", "model", "simulated", "status", "output_status",
            "elapsed_ms", "provider_calls"))
        row["usage"] = _usage(call.get("usage"))
        compact["calls"].append(row)
    return compact

"""Model-free replay entry shared by the CLI and local HTTP API."""

from copy import deepcopy

from .events import SUPPORTED_EVENTS_SCHEMA_VERSIONS
from .profile import export_replay, replay_bundle


def replay_document(document):
    """Accept a complete event journal or verify an exported replay bundle."""
    if not isinstance(document, dict):
        raise ValueError("Replay input must be a JSON object")
    from .core import engine_module
    research = engine_module("research_deck")
    if document.get("schema_version") == "norn.research-replay.v1":
        from .research_inspection import verify_bundle
        return verify_bundle(document)
    if (document.get("schema_version") in SUPPORTED_EVENTS_SCHEMA_VERSIONS
            and isinstance(document.get("events"), list) and research.deck_of(document)):
        from .research_inspection import build_bundle
        return build_bundle(document, research.load_deck(research.deck_of(document)))
    if document.get("schema_version") in SUPPORTED_EVENTS_SCHEMA_VERSIONS:
        return export_replay(document)
    profile = replay_bundle(document)
    # replay_bundle already verifies all source and derived fields. Avoid a
    # second complete fit merely to wrap the same verified content.
    bundle = {"schema_version": document["schema_version"],
              "journal": deepcopy(document.get("journal")), "profile": profile}
    if "post_session" in document:
        bundle["post_session"] = deepcopy(document["post_session"])
    return bundle


def replay_summary(bundle):
    """A small report; full event facts and the ledger stay in the bundle."""
    profile = bundle["profile"]
    analysis = profile["analysis"]
    from .core import engine_module
    summary = {
        "verified": True,
        "profile_version": profile["schema_version"],
        "algorithm_version": profile["algorithm_version"],
        "metadata_version": profile["metadata_version"],
        "profile_hash": profile["profile_hash"],
        "events_hash": profile.get("events_hash"),
        "source": profile["source"],
        "event_count": len((bundle.get("journal") or {}).get("events", [])),
        "total_rounds": analysis["total_rounds"],
        "total_selections": analysis["total_selections"],
        "mbti_type": analysis.get("mbti_type"),
        "coords_avg": analysis.get("coords_avg"),
        "reconstructed_coords": (engine_module("ledger").reconstruct_coords(analysis["attribution"])
                                 if "dimensions" in analysis["attribution"] else None),
        "provider_calls": 0,
        "preference": {key: analysis.get("preference", {}).get(key) for key in (
            "model_version", "feature_version", "observation_count", "pair_count", "validation")},
        "four_axes_scope": ("historical v1 reference; not learned personality accuracy" if "coords_avg" in analysis
                            else "scoring v4/v5 compute no four-axis heuristic"),
    }
    if profile.get("deck"):
        summary.update(deck=profile["deck"], layer=analysis["preference"]["status"],
                       contribution_ledger_hash=(bundle.get("contribution_ledger") or {}).get("ledger_hash"),
                       four_axes_scope="v6 uses reference utility and eight content components; no personality axes")
    return summary

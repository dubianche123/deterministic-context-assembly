"""Versioned deterministic profiles and independently verifiable replay bundles.

Hashes describe reproducible content, not authorship or an authenticity proof.
No model text, provider accounting, random session ID or clock enters a hash.
"""

from copy import deepcopy
import hmac

from .core import engine_module
from .events import content_hash, canonical_json, replay_events, validate_post_session, validate_snapshot


PROFILE_SCHEMA_VERSION = "norn.profile.v5"
PREVIEW_SCHEMA_VERSION = "norn.preview.v5"
REPLAY_SCHEMA_VERSION = "norn.replay.v1"
ALGORITHM_VERSION = "norn.scoring.v5"
SEQUENTIAL_ALGORITHM_VERSION = "norn.scoring.v4"
SEQUENTIAL_PROFILE_SCHEMA_VERSION = "norn.profile.v4"
PORTABLE_LEARNED_ALGORITHM_VERSION = "norn.scoring.v3"
PORTABLE_LEARNED_PROFILE_SCHEMA_VERSION = "norn.profile.v3"
NATIVE_LEARNED_ALGORITHM_VERSION = "norn.scoring.v2"
NATIVE_LEARNED_PROFILE_SCHEMA_VERSION = "norn.profile.v2"
LEGACY_ALGORITHM_VERSION = "norn.scoring.v1"
LEGACY_PROFILE_SCHEMA_VERSION = "norn.profile.v1"
_PROFILE_VERSIONS = {ALGORITHM_VERSION: PROFILE_SCHEMA_VERSION,
                     SEQUENTIAL_ALGORITHM_VERSION: SEQUENTIAL_PROFILE_SCHEMA_VERSION,
                     PORTABLE_LEARNED_ALGORITHM_VERSION: PORTABLE_LEARNED_PROFILE_SCHEMA_VERSION,
                     NATIVE_LEARNED_ALGORITHM_VERSION: NATIVE_LEARNED_PROFILE_SCHEMA_VERSION,
                     LEGACY_ALGORITHM_VERSION: LEGACY_PROFILE_SCHEMA_VERSION}
_PORTABLE_VERSIONS = (ALGORITHM_VERSION, SEQUENTIAL_ALGORITHM_VERSION, PORTABLE_LEARNED_ALGORITHM_VERSION)
# Scoring v4 keeps only source-event evidence under "attribution": no weights,
# so nothing like the v1–v3 coordinate ledger is reconstructed from it.
EVIDENCE_SCHEMA_VERSION = "norn.attribution.v3"


def metadata_version():
    """Hash the actual scoring metadata used by this runtime."""
    return content_hash(engine_module("fast_thinker")._load_cards())


def choice_analysis(scoring_input, event_ids, journal, *, include_open=False, algorithm_version=ALGORITHM_VERSION):
    """Counts and source-event evidence, with an explicitly versioned learner.

    The v1–v3 four-axis heuristic is not computed: no hesitation multiplier,
    round decay or withdrawal nudge. Durations stay in the journal as data.
    """
    if algorithm_version not in (ALGORITHM_VERSION, SEQUENTIAL_ALGORITHM_VERSION):
        raise ValueError("Unsupported choice scoring algorithm version")
    fast = engine_module("fast_thinker")
    selections, revisions = scoring_input["selections"], scoring_input["deselection_events"]
    selection_ids = fast._event_ids("selection", selections, event_ids.get("selection_event_ids"))
    revision_ids = fast._event_ids("revision", revisions, event_ids.get("revision_event_ids"))
    if set(selection_ids) & set(revision_ids):
        raise ValueError("Selection and revision event IDs must be globally unique")
    rounds = scoring_input["total_rounds"]
    evidence = {"schema_version": EVIDENCE_SCHEMA_VERSION, "algorithm_version": algorithm_version,
                "selections": [{"event_id": event_id, "card_id": row["id"], "round": row["round"]}
                               for event_id, row in zip(selection_ids, selections)],
                "revision": {"total": len(revisions),
                             "events": [{"event_id": event_id, "card_id": row["id"], "round": row["round"]}
                                        for event_id, row in zip(revision_ids, revisions)]}}
    model = engine_module("choice_model")
    rows = model.observations(journal, include_open=include_open) if journal is not None else []
    scorer = engine_module("choice_policy") if algorithm_version == ALGORITHM_VERSION else model
    return {"analysis_kind": "learned_preference", "total_rounds": rounds, "total_selections": len(selections),
            # Prose length routing only; not a scoring input.
            "data_density": "sparse_mode" if rounds <= 5 else "dense_mode" if rounds <= 20 else "overflow_mode",
            "choice_revision": {"total": len(revisions), "rounds_touched": len({row["round"] for row in revisions})},
            "attribution": evidence,
            "preference": scorer.summarize(rows, selected=[row["id"] for row in selections], include_open=include_open)}


def attach_preference(analysis, journals, *, include_open=False):
    """Scoring v2/v3 learned projection: the 979-feature pairwise ranker."""
    learned = engine_module("fast_thinker").fast_think_preferences(journals, include_open=include_open)
    analysis["preference"] = learned["summary"]
    selected = engine_module("preference_model").rank(
        learned["model"], card_ids=[row["card_id"] for row in analysis["attribution"]["selections"]], top_k=8)
    analysis["preference"]["selected_evidence"] = [
        {"card_id": row["card_id"], "score": row["score"]} for row in selected]
    analysis["analysis_kind"] = "learned_preference"
    analysis["legacy_projection"] = {"algorithm_version": analysis["attribution"]["algorithm_version"],
                                     "purpose": "historical reference; not the preference model or personality accuracy"}
    return learned["model"]


def _finish_profile(source, scoring_input, *, algorithm_version=ALGORITHM_VERSION, **provenance):
    if algorithm_version not in _PROFILE_VERSIONS:
        raise ValueError("Unsupported scoring algorithm version")
    numeric = engine_module("numeric")
    contract = numeric.PORTABLE_CONTRACT if algorithm_version in _PORTABLE_VERSIONS else numeric.NATIVE_CONTRACT
    with numeric.use(contract):
        return _finish_profile_scoped(source, scoring_input, algorithm_version=algorithm_version, **provenance)


def _finish_profile_scoped(source, scoring_input, *, algorithm_version, **provenance):
    event_ids = provenance.pop("_event_ids", {})
    journal = provenance.pop("_journal", None)
    if algorithm_version in (ALGORITHM_VERSION, SEQUENTIAL_ALGORITHM_VERSION):
        analysis = choice_analysis(deepcopy(scoring_input), deepcopy(event_ids), journal,
                                   algorithm_version=algorithm_version)
        profile = {"schema_version": _PROFILE_VERSIONS[algorithm_version], "algorithm_version": algorithm_version,
                   "metadata_version": metadata_version(), "source": source, **provenance, "analysis": analysis,
                   "feature_version": analysis["preference"]["feature_version"],
                   "numerical_contract": engine_module("numeric").PORTABLE_CONTRACT}
        profile["profile_hash"] = content_hash(profile)
        return deepcopy(profile)
    fast = engine_module("fast_thinker")
    analysis = fast.fast_think(**deepcopy(scoring_input), **deepcopy(event_ids))
    attribution = analysis.get("attribution")
    ledger = engine_module("ledger")
    expected_attribution = (ledger.PORTABLE_ALGORITHM_VERSION if algorithm_version in _PORTABLE_VERSIONS
                            else ledger.ALGORITHM_VERSION)
    if not isinstance(attribution, dict) or attribution.get("algorithm_version") != expected_attribution:
        raise ValueError("Scoring engine does not provide the supported contribution ledger")
    ledger.verify_attribution(attribution, analysis["coords_avg"])
    profile = {
        "schema_version": _PROFILE_VERSIONS[algorithm_version],
        "algorithm_version": algorithm_version,
        "metadata_version": metadata_version(),
        "source": source,
        **provenance,
        "analysis": analysis,
    }
    if algorithm_version in (PORTABLE_LEARNED_ALGORITHM_VERSION, NATIVE_LEARNED_ALGORITHM_VERSION):
        model = attach_preference(analysis, [journal] if journal is not None else [])
        profile["preference_model"] = model
        profile["feature_version"] = model["feature_version"]
        if algorithm_version == PORTABLE_LEARNED_ALGORITHM_VERSION:
            profile["numerical_contract"] = model["numerical_contract"]
    elif algorithm_version != LEGACY_ALGORITHM_VERSION:
        raise ValueError("Unsupported scoring algorithm version")
    profile["profile_hash"] = content_hash(profile)
    return deepcopy(profile)


def build_event_profile(journal, *, algorithm_version=ALGORITHM_VERSION):
    """Build a completed journal's profile using the authoritative scorer."""
    replay = replay_events(journal)
    return _finish_profile(
        "event_journal", replay["input"], algorithm_version=algorithm_version,
        events_hash=replay["events_hash"], _journal=replay["journal"],
        _event_ids={"selection_event_ids": replay["selection_event_ids"],
                    "revision_event_ids": replay["revision_event_ids"]},
    )


def build_profile(journal):
    """Alias for the event-journal profile builder."""
    return build_event_profile(journal)


def build_snapshot_profile(validated_input, *, algorithm_version=ALGORITHM_VERSION):
    """Recompute a legacy snapshot; it cannot establish a real click history."""
    snapshot = validate_snapshot(validated_input)
    return _finish_profile("legacy_snapshot", snapshot, algorithm_version=algorithm_version, input=snapshot,
                           snapshot_hash=content_hash(snapshot))


def verify_profile(profile, journal=None):
    """Check stored content and recompute all analysis and attribution fields.

    A compatible runtime and metadata version are required.  Merely recomputing
    a tampered profile_hash cannot make altered derived fields pass replay.
    """
    if type(profile) is not dict:
        raise ValueError("Profile must be an object")
    # Validate JSON before equality: True == 1 must never hide a type change.
    canonical_json(profile)
    if profile.get("schema_version") not in _PROFILE_VERSIONS.values():
        raise ValueError("Unsupported profile schema version")
    algorithm_version = profile.get("algorithm_version")
    if algorithm_version not in _PROFILE_VERSIONS:
        raise ValueError("Unsupported scoring algorithm version")
    expected_schema = _PROFILE_VERSIONS[algorithm_version]
    if profile["schema_version"] != expected_schema:
        raise ValueError("Profile and scoring algorithm versions are incompatible")
    if algorithm_version in _PORTABLE_VERSIONS and profile.get("numerical_contract") != engine_module("numeric").PORTABLE_CONTRACT:
        raise ValueError("Unsupported profile numerical contract")
    if profile.get("metadata_version") != metadata_version():
        raise ValueError("Profile metadata version differs from this runtime")
    expected_hash = profile.get("profile_hash")
    if (type(expected_hash) is not str or len(expected_hash) != 71
            or not expected_hash.startswith("sha256:")
            or any(char not in "0123456789abcdef" for char in expected_hash[7:])):
        raise ValueError("Profile must contain profile_hash")
    unhashed = {key: value for key, value in profile.items() if key != "profile_hash"}
    if not hmac.compare_digest(expected_hash, content_hash(unhashed)):
        raise ValueError("Profile hash mismatch")
    if profile.get("source") == "event_journal":
        if journal is None:
            raise ValueError("An event profile requires its source journal for verification")
        rebuilt = build_event_profile(journal, algorithm_version=algorithm_version)
    elif profile.get("source") == "legacy_snapshot":
        if journal is not None:
            raise ValueError("A legacy snapshot cannot claim a source event journal")
        rebuilt = build_snapshot_profile(profile.get("input"), algorithm_version=algorithm_version)
    else:
        raise ValueError("Unsupported profile source")
    if canonical_json(profile) != canonical_json(rebuilt):
        if algorithm_version not in _PORTABLE_VERSIONS:
            raise ValueError("Historical profile requires a compatible native-libm runtime; replayed analysis or attribution differs")
        raise ValueError("Replayed profile differs from stored analysis or attribution")
    return True


def build_replay_bundle(profile, journal=None):
    """Create a detached export only after checking every derived field."""
    verify_profile(profile, journal=journal)
    canonical = replay_events(journal)["journal"] if journal is not None else None
    return {"schema_version": REPLAY_SCHEMA_VERSION, "journal": canonical,
            "profile": deepcopy(profile)}


def selected_card_ids(profile):
    """The finally selected cards of a verified profile, in active order."""
    return [row["card_id"] for row in profile["analysis"]["attribution"]["selections"]]


def export_replay(journal):
    """Build a completed event session's portable, deterministic JSON bundle."""
    replay = replay_events(journal)
    profile = build_event_profile(replay["journal"])
    return {"schema_version": REPLAY_SCHEMA_VERSION, "journal": replay["journal"],
            "profile": profile}


def replay_bundle(bundle):
    """Verify an exported bundle and return a newly reconstructed profile.

    An exported session may carry one optional ``post_session`` outcome label.
    It is checked against the verified profile but is never part of a hash.
    """
    fields = {"schema_version", "journal", "profile"}
    if type(bundle) is not dict or set(bundle) not in (fields, fields | {"post_session"}):
        raise ValueError("Replay bundle must contain schema_version, journal and profile")
    if bundle["schema_version"] != REPLAY_SCHEMA_VERSION:
        raise ValueError("Unsupported replay bundle schema version")
    verify_profile(bundle["profile"], journal=bundle["journal"])
    if "post_session" in bundle:
        profile = bundle["profile"]
        validate_post_session(bundle["post_session"], profile_hash=profile["profile_hash"],
                              selected_card_ids=selected_card_ids(profile))
    # Verification has already fitted and compared the entire projection.
    return deepcopy(bundle["profile"])

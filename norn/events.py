"""Bounded, append-only choice facts and their deterministic scoring projection.

The journal is the evidence; selections are a derived view.  Replaying never
changes a caller's journal and never invokes a provider or writes to disk.
"""

from copy import deepcopy
import hashlib
import json
import math

from .core import engine_module


LEGACY_EVENTS_SCHEMA_VERSION = "norn.events.v1"
ATTENTION_EVENTS_SCHEMA_VERSION = "norn.events.v2"
BEHAVIOUR_EVENTS_SCHEMA_VERSION = "norn.events.v3"
CHOICE_LAYOUT_EVENTS_SCHEMA_VERSION = "norn.events.v4"
EVENTS_SCHEMA_VERSION = "norn.events.v5"
SUPPORTED_EVENTS_SCHEMA_VERSIONS = (LEGACY_EVENTS_SCHEMA_VERSION, ATTENTION_EVENTS_SCHEMA_VERSION,
                                    BEHAVIOUR_EVENTS_SCHEMA_VERSION, CHOICE_LAYOUT_EVENTS_SCHEMA_VERSION,
                                    EVENTS_SCHEMA_VERSION)
POST_SESSION_SCHEMA_VERSION = "norn.post_session.v1"
MAX_EVENTS = 10_000
MAX_ROUNDS = 256
MAX_SELECTIONS = 256
MAX_REVISIONS = 4096
MAX_DURATION_MS = 86_400_000

# v1 records only clicks and a render-to-close duration.  v2 keeps every v1
# event and adds the evidence needed to separate deliberation from loading,
# idle tabs and cards that never reached the screen:
#   round_ready      all images settled (loaded, failed or timed out)
#   t_ms             ms since round_ready; choices are only admitted after it
#   hidden_ms        tab-hidden ms inside the window [ready (or start), close]
#   seen_card_ids    cards >=50% in the viewport with their image shown, in
#                    first-seen order; null when the browser cannot observe it
# v3 keeps every v2 field and records raw behaviour no model reads yet, so a
# later model can test it against held-out choices instead of assuming it:
#   layout           [row, column] screen slot of each displayed card at
#                    readiness, in card_ids order; null when not measurable
#   input            device behind a choice: mouse, touch, pen, keyboard or null
#   hover            mouse/pen hover segments [card index, start_ms, end_ms]
#                    from readiness, ordered and non-overlapping; null where
#                    the device cannot hover
# v4 keeps v3 unchanged and adds layout to every selection/deselection, so
# resizing, orientation changes and drawers do not turn the initial layout
# into a false claim about where a later choice was made.
# v5 keeps v4 unchanged and adds display to round_started: the session's
# display policy and each card's role (engine display_policy), so a later
# analysis knows which cards were shown because the model predicted them.
INPUT_KINDS = ("mouse", "touch", "pen", "keyboard")
MAX_HOVER_SEGMENTS = 512
_EVENT_FIELDS = {
    LEGACY_EVENTS_SCHEMA_VERSION: {
        "round_started": {"round", "card_ids"},
        "card_selected": {"round", "card_id"},
        "card_deselected": {"round", "card_id"},
        "round_completed": {"round", "duration_ms"},
        "session_completed": set(),
    },
    ATTENTION_EVENTS_SCHEMA_VERSION: {
        "round_started": {"round", "card_ids"},
        "round_ready": {"round", "ready_offset_ms", "unloaded_card_ids"},
        "card_selected": {"round", "card_id", "t_ms"},
        "card_deselected": {"round", "card_id", "t_ms"},
        "round_completed": {"round", "duration_ms", "hidden_ms", "seen_card_ids"},
        "session_completed": set(),
    },
}
_EVENT_FIELDS[BEHAVIOUR_EVENTS_SCHEMA_VERSION] = {
    **_EVENT_FIELDS[ATTENTION_EVENTS_SCHEMA_VERSION],
    "round_ready": {"round", "ready_offset_ms", "unloaded_card_ids", "layout"},
    "card_selected": {"round", "card_id", "t_ms", "input"},
    "card_deselected": {"round", "card_id", "t_ms", "input"},
    "round_completed": {"round", "duration_ms", "hidden_ms", "seen_card_ids", "hover"},
}
_EVENT_FIELDS[CHOICE_LAYOUT_EVENTS_SCHEMA_VERSION] = {
    **_EVENT_FIELDS[BEHAVIOUR_EVENTS_SCHEMA_VERSION],
    "card_selected": {"round", "card_id", "t_ms", "input", "layout"},
    "card_deselected": {"round", "card_id", "t_ms", "input", "layout"},
}
_EVENT_FIELDS[EVENTS_SCHEMA_VERSION] = {
    **_EVENT_FIELDS[CHOICE_LAYOUT_EVENTS_SCHEMA_VERSION],
    "round_started": {"round", "card_ids", "display"},
}
_COMMON_FIELDS = {"event_id", "sequence", "type"}


def _json_value(value, depth=0):
    """Reject coercions, nonfinite numbers and pathological nested input."""
    if depth > 64:
        raise ValueError("JSON nesting exceeds 64 levels")
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return
    if type(value) is list:
        for item in value:
            _json_value(item, depth + 1)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("JSON object keys must be strings")
            _json_value(item, depth + 1)
        return
    raise ValueError("Only JSON values are accepted")


def canonical_json(value):
    """The v1 hash encoding: sorted keys, UTF-8, compact, finite JSON."""
    _json_value(value)
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, OverflowError, UnicodeError) as exc:
        raise ValueError("Value cannot be encoded as canonical JSON") from exc


def content_hash(value):
    try:
        encoded = canonical_json(value).encode("utf-8")
    except UnicodeError as exc:
        raise ValueError("JSON strings must contain valid Unicode") from exc
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _object(value, fields, label):
    if type(value) is not dict or set(value) != fields:
        raise ValueError(f"{label} must contain exactly: {', '.join(sorted(fields))}")


def _integer(value, label, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be an integer from {minimum} to {maximum}")
    return value


def _card_id(value, cards):
    if type(value) is not str or value not in cards:
        raise ValueError("Unknown card ID")
    return value


def _layout(value, displayed):
    """One distinct [row, column] slot per displayed card, or null."""
    if value is None:
        return None
    if (type(value) is not list or len(value) != len(displayed)
            or any(type(slot) is not list or len(slot) != 2 for slot in value)):
        raise ValueError("layout must give one [row, column] per displayed card")
    for slot in value:
        for number in slot:
            _integer(number, "layout slot", 0, MAX_SELECTIONS - 1)
    if len({tuple(slot) for slot in value}) != len(value):
        raise ValueError("layout slots must be distinct")
    return [list(slot) for slot in value]


def _hover(value, displayed, window):
    """Ordered, non-overlapping [card index, start_ms, end_ms] inside the round window, or null."""
    if value is None:
        return None
    if type(value) is not list or len(value) > MAX_HOVER_SEGMENTS:
        raise ValueError(f"hover must be a list of at most {MAX_HOVER_SEGMENTS} segments")
    previous = 0
    for segment in value:
        if type(segment) is not list or len(segment) != 3:
            raise ValueError("hover segments must be [card index, start_ms, end_ms]")
        _integer(segment[0], "hover card index", 0, len(displayed) - 1)
        start = _integer(segment[1], "hover start_ms", previous, window)
        previous = _integer(segment[2], "hover end_ms", start + 1, window)
    return [list(segment) for segment in value]


def _card_subset(value, displayed, label, *, nullable=False):
    """A unique, ordered subset of this round's displayed cards."""
    if value is None and nullable:
        return None
    if (type(value) is not list or len(value) > len(displayed)
            or any(type(card) is not str or card not in displayed for card in value)
            or len(set(value)) != len(value)):
        raise ValueError(f"{label} must be unique cards displayed in this round")
    return list(value)


def empty_journal(schema_version=EVENTS_SCHEMA_VERSION):
    """Return a fresh journal suitable for append_event()."""
    if schema_version not in SUPPORTED_EVENTS_SCHEMA_VERSIONS:
        raise ValueError("Unsupported event schema version")
    return {"schema_version": schema_version, "events": []}


def replay_events(journal, *, require_complete=True, cards=None):
    """Validate every transition and return a detached scoring projection.

    ``cards`` replaces the deck the card IDs are checked against; only the
    new-image probe (norn.probe) passes its own catalog here.

    A deselection is evidence only if that card is currently selected and is
    displayed in the open round.  Its original selection can be from an earlier
    round.  Selecting it again creates a new position in the active order.

    The scoring ``input`` has the same shape and meaning for v1 and v2: v2
    attention evidence is reported separately in ``round_evidence`` and never
    changes the historical four-axis projection.
    """
    if type(require_complete) is not bool:
        raise ValueError("require_complete must be a boolean")
    _object(journal, {"schema_version", "events"}, "Journal")
    version = journal["schema_version"]
    if type(version) is not str or version not in SUPPORTED_EVENTS_SCHEMA_VERSIONS:
        raise ValueError("Unsupported event schema version")
    attention = version != LEGACY_EVENTS_SCHEMA_VERSION
    behaviour = version in (BEHAVIOUR_EVENTS_SCHEMA_VERSION, CHOICE_LAYOUT_EVENTS_SCHEMA_VERSION,
                            EVENTS_SCHEMA_VERSION)
    choice_layout = version in (CHOICE_LAYOUT_EVENTS_SCHEMA_VERSION, EVENTS_SCHEMA_VERSION)
    display_session = None
    schema = _EVENT_FIELDS[version]
    events = journal["events"]
    if type(events) is not list or len(events) > MAX_EVENTS:
        raise ValueError(f"events must be an array of at most {MAX_EVENTS} items")
    cards = engine_module("fast_thinker")._load_cards() if cards is None else cards
    active = {}
    revisions = []
    revision_ids = []
    durations = []
    evidence = []
    seen_event_ids = set()
    current_round = 0
    open_round = False
    displayed = []
    ready = None
    last_choice_ms = 0
    complete = False

    for position, event in enumerate(events, 1):
        if type(event) is not dict:
            raise ValueError("Each event must be an object")
        event_type = event.get("type")
        if type(event_type) is not str or event_type not in schema:
            raise ValueError("Unknown event type")
        _object(event, _COMMON_FIELDS | schema[event_type], "Event")
        event_id = event["event_id"]
        if (type(event_id) is not str or not 1 <= len(event_id) <= 64
                or any(ord(char) < 33 or ord(char) > 126 for char in event_id)):
            raise ValueError("event_id must be 1 to 64 printable ASCII characters without spaces")
        if event_id in seen_event_ids:
            raise ValueError("event_id must be unique")
        seen_event_ids.add(event_id)
        if type(event["sequence"]) is not int or event["sequence"] != position:
            raise ValueError("Event sequences must be continuous integers starting at 1")
        if complete:
            raise ValueError("No event may follow session_completed")

        if event_type == "session_completed":
            if current_round < 1 or open_round:
                raise ValueError("session_completed requires at least one completed round")
            complete = True
            continue

        rnd = _integer(event["round"], "round", 1, MAX_ROUNDS)
        if event_type == "round_started":
            if open_round or rnd != current_round + 1:
                raise ValueError("Rounds must start consecutively after the preceding round completed")
            card_ids = event["card_ids"]
            if type(card_ids) is not list or not 1 <= len(card_ids) <= MAX_SELECTIONS:
                raise ValueError("round_started requires 1 to 256 displayed card IDs")
            for card_id in card_ids:
                _card_id(card_id, cards)
            if len(set(card_ids)) != len(card_ids):
                raise ValueError("Displayed cards must be unique within the round")
            if version == EVENTS_SCHEMA_VERSION:
                # A research deck's rounds name their deck (engine research_deck); Norn's own do not.
                display = event["display"]
                research = type(display) is dict and "deck" in display
                rule = engine_module("research_deck" if research else "display_policy")
                identity = (rule.check_display(display, card_ids), display["deck"] if research else None)
                if display_session is not None and identity != display_session:
                    raise ValueError("A session keeps one display policy")
                display_session = identity
            displayed = list(card_ids)
            ready = layout = None
            choice_layouts = []
            last_choice_ms = 0
            current_round = rnd
            open_round = True
            continue

        if not open_round or rnd != current_round:
            raise ValueError("Event must belong to the currently open round")
        if event_type == "round_ready":
            if ready is not None:
                raise ValueError("A round can become ready only once")
            ready = {"ready_offset_ms": _integer(event["ready_offset_ms"], "ready_offset_ms", 0, MAX_DURATION_MS),
                     "unloaded_card_ids": _card_subset(event["unloaded_card_ids"], displayed, "unloaded_card_ids")}
            if behaviour:
                layout = _layout(event["layout"], displayed)
            continue
        if event_type == "round_completed":
            duration = _integer(event["duration_ms"], "duration_ms", 0, MAX_DURATION_MS)
            durations.append({"round": rnd, "duration_ms": duration})
            row = {"round": rnd, "displayed": list(displayed), "duration_ms": duration,
                   "ready_offset_ms": None, "unloaded_card_ids": None, "hidden_ms": None,
                   "deliberation_ms": None, "seen_card_ids": None}
            if attention:
                offset = ready["ready_offset_ms"] if ready is not None else 0
                window = duration - offset
                if window < last_choice_ms:
                    raise ValueError("duration_ms must cover round readiness and every timed choice")
                hidden = _integer(event["hidden_ms"], "hidden_ms", 0, MAX_DURATION_MS)
                if hidden > window:
                    raise ValueError("hidden_ms cannot exceed the measured round window")
                row.update(hidden_ms=hidden, deliberation_ms=window - hidden if ready is not None else None,
                           seen_card_ids=_card_subset(event["seen_card_ids"], displayed, "seen_card_ids",
                                                      nullable=True))
                if ready is not None:
                    row.update(ready)
            if behaviour:
                if ready is None and event["hover"] is not None:
                    raise ValueError("hover is timed from readiness and needs round_ready")
                row.update(layout=layout if ready is not None else None,
                           hover=_hover(event["hover"], displayed, window))
            if choice_layout:
                row["choice_layouts"] = choice_layouts
            evidence.append(row)
            open_round = False
            continue

        card_id = _card_id(event["card_id"], cards)
        if card_id not in displayed:
            raise ValueError("Choice event references a card not displayed in this round")
        if attention:
            if ready is None:
                raise ValueError("A choice requires round_ready in this round")
            moment = _integer(event["t_ms"], "t_ms", 0, MAX_DURATION_MS - ready["ready_offset_ms"])
            if moment < last_choice_ms:
                raise ValueError("Choice t_ms must not decrease within a round")
            last_choice_ms = moment
        if behaviour and event["input"] is not None and event["input"] not in INPUT_KINDS:
            raise ValueError(f"input must be one of {', '.join(INPUT_KINDS)} or null")
        if choice_layout:
            choice_layouts.append({"event_id": event_id, "t_ms": moment,
                                   "layout": _layout(event["layout"], displayed)})
        if event_type == "card_selected":
            if card_id in active:
                raise ValueError("A currently selected card cannot be selected twice")
            if len(active) >= MAX_SELECTIONS:
                raise ValueError("Too many active selections")
            active[card_id] = ({"id": card_id, "round": rnd}, event_id)
        else:
            if card_id not in active:
                raise ValueError("Cannot deselect a card that is not selected")
            del active[card_id]
            revisions.append({"id": card_id, "round": rnd})
            revision_ids.append(event_id)

    if require_complete and not complete:
        raise ValueError("A complete journal must end with session_completed")
    canonical = deepcopy(journal)
    return {
        "journal": canonical,
        "input": {
            "selections": [deepcopy(row[0]) for row in active.values()],
            "total_rounds": current_round,
            "round_durations": durations,
            "deselection_events": revisions,
        },
        "selection_event_ids": [row[1] for row in active.values()],
        "revision_event_ids": revision_ids,
        "round_evidence": evidence,
        "events_hash": content_hash(canonical),
        "complete": complete,
    }


def append_event(journal, event):
    """Append one fully specified event to a new copy after validation."""
    replay = replay_events(journal, require_complete=False)
    if replay["complete"]:
        raise ValueError("Cannot append to a completed session")
    if len(replay["journal"]["events"]) >= MAX_EVENTS:
        raise ValueError("Too many events")
    updated = replay["journal"]
    updated["events"].append(deepcopy(event))
    return replay_events(updated, require_complete=False)["journal"]


def _sha256(value, label):
    if (type(value) is not str or len(value) != 71 or not value.startswith("sha256:")
            or any(char not in "0123456789abcdef" for char in value[7:])):
        raise ValueError(f"{label} must be a sha256 content hash")


def validate_post_session(record, *, selected_card_ids, profile_hash):
    """Validate the optional outcome label given after a reading is shown.

    It is a separate, mutable annotation of one sealed session, never a journal
    event: rating a reading cannot alter replay, scoring or any content hash.
    ``closest_card_id`` may only name a card that the session finally selected.
    """
    fields = {"schema_version", "reading_rating", "closest_card_id", "profile_hash",
              "reading_hash", "context_hash", "provider", "simulated"}
    _object(record, fields, "Post-session record")
    if record["schema_version"] != POST_SESSION_SCHEMA_VERSION:
        raise ValueError("Unsupported post-session schema version")
    rating, closest = record["reading_rating"], record["closest_card_id"]
    if rating is not None:
        _integer(rating, "reading_rating", 1, 5)
    if closest is not None and (type(closest) is not str or closest not in selected_card_ids):
        raise ValueError("closest_card_id must be one of this session's selected cards")
    if rating is None and closest is None:
        raise ValueError("A post-session record needs a rating or a closest card; skipping sends nothing")
    _sha256(record["profile_hash"], "profile_hash")
    if record["profile_hash"] != profile_hash:
        raise ValueError("Post-session record belongs to a different profile")
    _sha256(record["reading_hash"], "reading_hash")
    if record["context_hash"] is not None:
        _sha256(record["context_hash"], "context_hash")
    if type(record["provider"]) is not str or not 1 <= len(record["provider"]) <= 64:
        raise ValueError("provider must be a short string")
    if type(record["simulated"]) is not bool:
        raise ValueError("simulated must be a boolean")
    content_hash(record)
    return deepcopy(record)


def validate_snapshot(snapshot):
    """Validate a legacy scoring snapshot without inventing missing clicks."""
    fields = {"selections", "total_rounds", "round_durations", "deselection_events"}
    _object(snapshot, fields, "Snapshot")
    rounds = _integer(snapshot["total_rounds"], "total_rounds", 1, MAX_ROUNDS)
    cards = engine_module("fast_thinker")._load_cards()
    for name, limit in (("selections", MAX_SELECTIONS), ("deselection_events", MAX_REVISIONS)):
        rows = snapshot[name]
        if type(rows) is not list or len(rows) > limit:
            raise ValueError(f"{name} must be an array of at most {limit} items")
        seen = set()
        for row in rows:
            _object(row, {"id", "round"}, name + " item")
            card_id = _card_id(row["id"], cards)
            _integer(row["round"], "round", 1, rounds)
            if name == "selections" and card_id in seen:
                raise ValueError("A snapshot cannot select a card twice")
            seen.add(card_id)
    durations = snapshot["round_durations"]
    if type(durations) is not list or len(durations) > rounds:
        raise ValueError("round_durations must have at most one entry per round")
    seen = set()
    for row in durations:
        _object(row, {"round", "duration_ms"}, "Duration")
        rnd = _integer(row["round"], "round", 1, rounds)
        _integer(row["duration_ms"], "duration_ms", 0, MAX_DURATION_MS)
        if rnd in seen:
            raise ValueError("A snapshot cannot repeat a round duration")
        seen.add(rnd)
    # JSON encoding also excludes malformed Unicode in otherwise valid strings.
    content_hash(snapshot)
    return deepcopy(snapshot)

"""Event admission, provenance, deterministic hashes and portable replay."""

from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from norn.core import engine_module
from norn.events import (
    ATTENTION_EVENTS_SCHEMA_VERSION, BEHAVIOUR_EVENTS_SCHEMA_VERSION, CHOICE_LAYOUT_EVENTS_SCHEMA_VERSION,
    LEGACY_EVENTS_SCHEMA_VERSION, MAX_EVENTS,
    MAX_HOVER_SEGMENTS, append_event, canonical_json, content_hash, empty_journal, replay_events,
    validate_snapshot,
)
from norn.profile import (
    build_event_profile, build_profile, build_replay_bundle,
    build_snapshot_profile, export_replay, metadata_version,
    replay_bundle, verify_profile,
)


FIXTURE = Path(__file__).parent / "fixtures" / "replay-session.json"


def journal_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def empty_choice_journal():
    return {"schema_version": "norn.events.v1", "events": [
        {"event_id": "s", "sequence": 1, "type": "round_started", "round": 1,
         "card_ids": ["NORN_0001"]},
        {"event_id": "r", "sequence": 2, "type": "round_completed", "round": 1,
         "duration_ms": 0},
        {"event_id": "c", "sequence": 3, "type": "session_completed"},
    ]}


def attention_journal(*rounds, complete=True):
    """Build a norn.events.v2 journal from compact per-round facts."""
    events = []

    def add(kind, **fields):
        events.append({"event_id": f"v2-{len(events) + 1}", "sequence": len(events) + 1,
                       "type": kind, **fields})
    for number, row in enumerate(rounds, 1):
        add("round_started", round=number, card_ids=list(row["cards"]))
        if row.get("ready") is not None:
            add("round_ready", round=number, ready_offset_ms=row["ready"],
                unloaded_card_ids=list(row.get("unloaded", [])))
        for kind, card, moment in row.get("choices", []):
            add(kind, round=number, card_id=card, t_ms=moment)
        add("round_completed", round=number, duration_ms=row["duration"],
            hidden_ms=row.get("hidden", 0), seen_card_ids=row.get("seen"))
    if complete:
        add("session_completed")
    return {"schema_version": ATTENTION_EVENTS_SCHEMA_VERSION, "events": events}


def behaviour_journal(journal, *, layouts, inputs, hovers):
    """A v2 journal as v3: per-round layouts and hovers, one input per choice, in order."""
    layouts, inputs, hovers = iter(layouts), iter(inputs), iter(hovers)
    events = []
    for event in deepcopy(journal["events"]):
        if event["type"] == "round_ready":
            event["layout"] = next(layouts)
        elif event["type"] in ("card_selected", "card_deselected"):
            event["input"] = next(inputs)
        elif event["type"] == "round_completed":
            event["hover"] = next(hovers)
        events.append(event)
    return {"schema_version": BEHAVIOUR_EVENTS_SCHEMA_VERSION, "events": events}


def choice_layout_journal(journal, layouts):
    """Keep v3 facts intact and add one current layout per v4 choice."""
    journal = deepcopy(journal)
    journal["schema_version"] = CHOICE_LAYOUT_EVENTS_SCHEMA_VERSION
    layouts = iter(layouts)
    for event in journal["events"]:
        if event["type"] in ("card_selected", "card_deselected"):
            event["layout"] = next(layouts)
    return journal


BEHAVIOUR = {"layouts": ([[0, 0], [0, 1], [1, 0], [1, 1]], None),
             "inputs": ("mouse", "touch", "keyboard", None),
             "hovers": ([[0, 0, 250], [1, 300, 1100], [0, 1100, 1400]], [], None)}


def v1_projection(journal):
    """Remove only the v2 attention facts, keeping every v1 choice fact."""
    events = []
    for event in journal["events"]:
        if event["type"] == "round_ready":
            continue
        row = {key: value for key, value in event.items()
               if key not in ("t_ms", "hidden_ms", "seen_card_ids")}
        row["sequence"] = len(events) + 1
        events.append(row)
    return {"schema_version": LEGACY_EVENTS_SCHEMA_VERSION, "events": events}


ATTENTION_ROUNDS = (
    {"cards": ["NORN_0001", "NORN_0002", "NORN_0003", "NORN_0004"], "ready": 900, "unloaded": ["NORN_0004"],
     "choices": [("card_selected", "NORN_0001", 1200), ("card_selected", "NORN_0002", 1500),
                 ("card_deselected", "NORN_0002", 2600)],
     "duration": 9000, "hidden": 3000, "seen": ["NORN_0002", "NORN_0001"]},
    {"cards": ["NORN_0005", "NORN_0006", "NORN_0001"], "ready": 0,
     "choices": [("card_selected", "NORN_0006", 400)], "duration": 400, "seen": None},
    {"cards": ["NORN_0007", "NORN_0008"], "duration": 300, "hidden": 300},
)


class EventReplayTests(unittest.TestCase):
    def test_fixture_retains_active_order_and_every_real_revision(self):
        replay = replay_events(journal_fixture())
        self.assertTrue(replay["complete"])
        self.assertEqual(replay["input"]["selections"], [
            {"id": "NORN_0003", "round": 2}, {"id": "NORN_0004", "round": 3}])
        self.assertEqual(replay["selection_event_ids"], ["e09", "e13"])
        self.assertEqual(replay["input"]["deselection_events"], [
            {"id": "NORN_0001", "round": 1},
            {"id": "NORN_0001", "round": 2},
            {"id": "NORN_0002", "round": 3}])
        self.assertEqual(replay["revision_event_ids"], ["e04", "e08", "e12"])
        self.assertEqual(replay["input"]["round_durations"][1]["duration_ms"], 86400000)

    def test_reselect_becomes_new_order_position_with_new_source_id(self):
        journal = journal_fixture()
        journal["events"] = journal["events"][:6] + [
            {"event_id": "done", "sequence": 7, "type": "session_completed"}]
        replay = replay_events(journal)
        self.assertEqual(replay["input"]["selections"], [
            {"id": "NORN_0002", "round": 1}, {"id": "NORN_0001", "round": 1}])
        self.assertEqual(replay["selection_event_ids"], ["e03", "e05"])

    def test_append_is_copy_on_write_and_validates_new_transition(self):
        original = empty_journal(LEGACY_EVENTS_SCHEMA_VERSION)
        event = empty_choice_journal()["events"][0]
        updated = append_event(original, event)
        self.assertEqual(original["events"], [])
        event["card_ids"].append("NORN_0002")
        self.assertEqual(updated["events"][0]["card_ids"], ["NORN_0001"])
        with self.assertRaises(ValueError):
            append_event(updated, updated["events"][0])
        with self.assertRaises(ValueError):
            append_event(empty_choice_journal(), event)

    def test_replay_outputs_do_not_alias_source(self):
        journal = journal_fixture()
        before = deepcopy(journal)
        replay = replay_events(journal)
        replay["journal"]["events"][0]["card_ids"].clear()
        replay["input"]["selections"][0]["id"] = "altered"
        self.assertEqual(journal, before)

    def test_incomplete_is_explicit_and_cannot_build_profile(self):
        for events in ([], empty_choice_journal()["events"][:1],
                       empty_choice_journal()["events"][:2]):
            journal = {"schema_version": "norn.events.v1", "events": events}
            self.assertFalse(replay_events(journal, require_complete=False)["complete"])
            with self.assertRaises(ValueError):
                replay_events(journal)
            with self.assertRaises(ValueError):
                build_event_profile(journal)

    def test_invalid_schema_fields_types_and_identity_are_rejected(self):
        cases = []
        for key, value in (("schema_version", "norn.events.v99"), ("events", ()),
                           ("events", [None]), ("events", [None] * (MAX_EVENTS + 1))):
            journal = empty_choice_journal()
            journal[key] = value
            cases.append(journal)
        journal = empty_choice_journal()
        journal["extra"] = True
        cases.append(journal)
        for key, value in (("type", "made_up"), ("sequence", True), ("sequence", 2),
                           ("event_id", "x" * 65), ("event_id", "中文"),
                           ("event_id", "has space"), ("event_id", ""),
                           ("card_ids", []), ("card_ids", ["UNKNOWN"]),
                           ("card_ids", ["NORN_0001", "NORN_0001"]),
                           ("round", True), ("round", 257), ("round", 1.0)):
            journal = empty_choice_journal()
            journal["events"][0][key] = value
            cases.append(journal)
        for field in ("extra", "duration_ms"):
            journal = empty_choice_journal()
            journal["events"][0][field] = 1
            cases.append(journal)
        journal = empty_choice_journal()
        journal["events"][1]["event_id"] = "s"
        cases.append(journal)
        for duration in (-1, 86400001, True, 1.5, float("nan"), float("inf")):
            journal = empty_choice_journal()
            journal["events"][1]["duration_ms"] = duration
            cases.append(journal)
        for journal in cases:
            with self.subTest(journal=journal), self.assertRaises(ValueError):
                replay_events(journal)

    def test_fabricated_or_out_of_round_events_are_rejected(self):
        initial = empty_choice_journal()["events"][0]
        complete_round = empty_choice_journal()["events"][1]
        finished = empty_choice_journal()["events"][2]
        select = {"event_id": "pick", "sequence": 2, "type": "card_selected",
                  "round": 1, "card_id": "NORN_0001"}
        bad_rows = [
            [finished],
            [initial, finished],
            [initial, {**initial, "sequence": 2, "event_id": "again", "round": 2}],
            [initial, {**select, "card_id": "NORN_0002"}],
            [initial, {**select, "round": 2}],
            [initial, {**select, "type": "card_deselected"}],
            [initial, select, {**select, "sequence": 3, "event_id": "duplicate"}],
            [initial, complete_round, {**select, "sequence": 3}],
            [initial, complete_round, finished,
             {**finished, "sequence": 4, "event_id": "again"}],
            [initial, complete_round,
             {**initial, "sequence": 3, "event_id": "skip", "round": 3}],
        ]
        for rows in bad_rows:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                replay_events({"schema_version": "norn.events.v1", "events": rows},
                              require_complete=False)

    def test_cross_round_deselection_requires_current_display_evidence(self):
        journal = journal_fixture()
        journal["events"][6]["card_ids"].remove("NORN_0001")
        with self.assertRaisesRegex(ValueError, "not displayed"):
            replay_events(journal)

    def test_maximum_round_boundary_and_empty_selection_are_valid(self):
        events = []
        for rnd in range(1, 257):
            events.extend([
                {"event_id": f"s{rnd}", "sequence": len(events) + 1,
                 "type": "round_started", "round": rnd, "card_ids": ["NORN_0001"]},
                {"event_id": f"r{rnd}", "sequence": len(events) + 2,
                 "type": "round_completed", "round": rnd, "duration_ms": rnd},
            ])
        events.append({"event_id": "finish", "sequence": len(events) + 1,
                       "type": "session_completed"})
        replay = replay_events({"schema_version": "norn.events.v1", "events": events})
        self.assertEqual(replay["input"]["total_rounds"], 256)
        self.assertEqual(replay["input"]["selections"], [])

    def test_journal_uses_one_event_budget_including_many_revisions(self):
        events = [empty_choice_journal()["events"][0]]
        for index in range(4998):
            for event_type in ("card_selected", "card_deselected"):
                events.append({"event_id": f"event-{len(events) + 1}",
                               "sequence": len(events) + 1, "type": event_type,
                               "round": 1, "card_id": "NORN_0001"})
        events.append({"event_id": "last-pick", "sequence": len(events) + 1,
                       "type": "card_selected", "round": 1, "card_id": "NORN_0001"})
        events.append({"event_id": "last-round", "sequence": len(events) + 1,
                       "type": "round_completed", "round": 1, "duration_ms": 45000})
        events.append({"event_id": "finish", "sequence": len(events) + 1,
                       "type": "session_completed"})
        self.assertEqual(len(events), MAX_EVENTS)
        journal = {"schema_version": "norn.events.v1", "events": events}
        replay = replay_events(journal)
        self.assertEqual(len(replay["input"]["deselection_events"]), 4998)
        journal["events"].append({})
        with self.assertRaisesRegex(ValueError, "at most"):
            replay_events(journal)

    def test_v1_fixture_replays_exactly_as_before_attention_support(self):
        journal = journal_fixture()
        raw = FIXTURE.read_text(encoding="utf-8")
        replay = replay_events(journal)
        self.assertEqual(journal["schema_version"], LEGACY_EVENTS_SCHEMA_VERSION)
        self.assertEqual(replay["journal"], json.loads(raw))
        self.assertEqual(canonical_json(replay["journal"]), canonical_json(json.loads(raw)))
        self.assertEqual(replay["events_hash"],
                         "sha256:079413f295c556eb24aed661bec247ec79dc0aeba63f63629e9a4c1ce061e34b")
        self.assertEqual(replay["input"]["round_durations"],
                         [{"round": 1, "duration_ms": 1000}, {"round": 2, "duration_ms": 86400000},
                          {"round": 3, "duration_ms": 45000}])
        self.assertTrue(all(row["seen_card_ids"] is None and row["ready_offset_ms"] is None
                            and row["deliberation_ms"] is None for row in replay["round_evidence"]),
                        "v1 has no attention evidence and none is invented")
        self.assertEqual(export_replay(journal)["journal"], json.loads(raw))
        self.assertEqual(empty_journal(LEGACY_EVENTS_SCHEMA_VERSION)["schema_version"], "norn.events.v1")
        self.assertEqual(empty_journal()["schema_version"], "norn.events.v5")

    def test_v2_attention_evidence_separates_loading_idle_and_unseen_cards(self):
        replay = replay_events(attention_journal(*ATTENTION_ROUNDS))
        first, second, third = replay["round_evidence"]
        self.assertEqual(first["ready_offset_ms"], 900)
        self.assertEqual(first["deliberation_ms"], 9000 - 900 - 3000)
        self.assertEqual(first["unloaded_card_ids"], ["NORN_0004"])
        self.assertEqual(first["seen_card_ids"], ["NORN_0002", "NORN_0001"])
        self.assertEqual((second["deliberation_ms"], second["seen_card_ids"]), (400, None))
        self.assertEqual((third["ready_offset_ms"], third["deliberation_ms"], third["hidden_ms"]),
                         (None, None, 300), "A round closed while loading has no deliberation time")
        self.assertEqual(replay["input"]["selections"], [{"id": "NORN_0001", "round": 1},
                                                         {"id": "NORN_0006", "round": 2}])

    def test_v2_attention_never_changes_the_scoring_projection(self):
        journal = attention_journal(*ATTENTION_ROUNDS)
        legacy = v1_projection(journal)
        replay, legacy_replay = replay_events(journal), replay_events(legacy)
        self.assertEqual(replay["input"], legacy_replay["input"])
        v3 = "norn.scoring.v3"  # the four-axis projection exists only in v1–v3
        self.assertEqual(build_event_profile(journal, algorithm_version=v3)["analysis"]["coords_avg"],
                         build_event_profile(legacy, algorithm_version=v3)["analysis"]["coords_avg"])
        bundle = export_replay(journal)
        self.assertEqual(replay_bundle(json.loads(canonical_json(bundle))), bundle["profile"])

    def test_v2_rejects_impossible_attention_facts_and_cross_version_fields(self):
        def mutated(change):
            journal = attention_journal(*ATTENTION_ROUNDS)
            change(journal["events"])
            return journal
        ready, pick, repick, withdraw, closed = (1, 2, 3, 4, 5)
        cases = {
            "choice before readiness": lambda e: e.pop(ready),
            "second readiness": lambda e: e.insert(ready + 1, {**e[ready], "event_id": "again"}),
            "decreasing t_ms": lambda e: e[repick].update(t_ms=1100),
            "negative t_ms": lambda e: e[pick].update(t_ms=-1),
            "boolean t_ms": lambda e: e[pick].update(t_ms=True),
            "choice after close": lambda e: e[closed].update(duration_ms=900 + 2599),
            "hidden longer than window": lambda e: e[closed].update(hidden_ms=9000 - 900 + 1),
            "unseen card not displayed": lambda e: e[closed].update(seen_card_ids=["NORN_0009"]),
            "duplicate seen card": lambda e: e[closed].update(seen_card_ids=["NORN_0001", "NORN_0001"]),
            "seen is not a list": lambda e: e[closed].update(seen_card_ids="NORN_0001"),
            "unloaded card not displayed": lambda e: e[ready].update(unloaded_card_ids=["NORN_0009"]),
            "fractional offset": lambda e: e[ready].update(ready_offset_ms=1.5),
            "v1 choice in v2": lambda e: e[pick].pop("t_ms"),
            "v1 close in v2": lambda e: [e[closed].pop(key) for key in ("hidden_ms", "seen_card_ids")],
            "unknown field": lambda e: e[ready].update(extra=1),
            "ready in another round": lambda e: e[ready].update(round=2),
        }
        for label, change in cases.items():
            with self.subTest(label), self.assertRaises(ValueError):
                replay_events(mutated(change))
        for change in (lambda e: e[1].update(t_ms=0),
                       lambda e: e.insert(1, {"event_id": "r", "sequence": 2, "type": "round_ready", "round": 1,
                                              "ready_offset_ms": 0, "unloaded_card_ids": []}),
                       lambda e: e[-2].update(hidden_ms=0)):
            journal = journal_fixture()
            change(journal["events"])
            for sequence, event in enumerate(journal["events"], 1):
                event["sequence"] = sequence
            with self.subTest(v1=change), self.assertRaises(ValueError):
                replay_events(journal)

    def test_v3_records_layout_input_and_hover_without_changing_any_model_input(self):
        v2 = attention_journal(*ATTENTION_ROUNDS)
        v3 = behaviour_journal(v2, **BEHAVIOUR)
        replay, before = replay_events(v3), replay_events(v2)
        self.assertEqual(replay["input"], before["input"])
        first, second, third = replay["round_evidence"]
        self.assertEqual(first["layout"], BEHAVIOUR["layouts"][0])
        self.assertEqual(first["hover"], BEHAVIOUR["hovers"][0])
        self.assertEqual((second["layout"], second["hover"]), (None, []))
        self.assertEqual((third["layout"], third["hover"]), (None, None))  # never ready: no timeline origin
        # v2 evidence keeps its exact shape.
        self.assertTrue(all("hover" not in row and "layout" not in row for row in before["round_evidence"]))
        choice = engine_module("choice_model")
        self.assertEqual(choice.observations(v3), choice.observations(v2))
        legacy = engine_module("preference_model")
        self.assertEqual(legacy.train([v3])["weights"], legacy.train([v2])["weights"])
        profile = build_profile(v3)
        self.assertEqual(profile["analysis"]["preference"]["posterior"],
                         build_profile(v2)["analysis"]["preference"]["posterior"])

    def test_v3_rejects_impossible_behaviour(self):
        def mutated(change):
            journal = behaviour_journal(attention_journal(*ATTENTION_ROUNDS), **BEHAVIOUR)
            change(journal["events"])
            return journal
        ready, pick, closed, late_close = 1, 2, 5, -2
        cases = {
            "layout too short": lambda e: e[ready].update(layout=[[0, 0]]),
            "shared slot": lambda e: e[ready].update(layout=[[0, 0], [0, 0], [1, 0], [1, 1]]),
            "negative slot": lambda e: e[ready].update(layout=[[0, -1], [0, 1], [1, 0], [1, 1]]),
            "slot not a pair": lambda e: e[ready].update(layout=[[0], [0, 1], [1, 0], [1, 1]]),
            "unknown input": lambda e: e[pick].update(input="gamepad"),
            "missing input": lambda e: e[pick].pop("input"),
            "hover past window": lambda e: e[closed].update(hover=[[0, 0, 9000 - 900 + 1]]),
            "hover overlap": lambda e: e[closed].update(hover=[[0, 0, 300], [1, 200, 400]]),
            "hover unordered": lambda e: e[closed].update(hover=[[1, 300, 400], [0, 0, 100]]),
            "empty hover": lambda e: e[closed].update(hover=[[0, 100, 100]]),
            "hover card out of range": lambda e: e[closed].update(hover=[[4, 0, 100]]),
            "hover as dict": lambda e: e[closed].update(hover={"NORN_0001": 100}),
            "too many segments": lambda e: e[closed].update(
                hover=[[0, k, k + 1] for k in range(MAX_HOVER_SEGMENTS + 1)]),
            "hover without readiness": lambda e: e[late_close].update(hover=[]),
            "v2 close in v3": lambda e: e[closed].pop("hover"),
        }
        for label, change in cases.items():
            with self.subTest(label), self.assertRaises(ValueError):
                replay_events(mutated(change))

    def test_v4_choice_layouts_change_with_the_screen_without_changing_model_input(self):
        v3 = behaviour_journal(attention_journal(*ATTENTION_ROUNDS), **BEHAVIOUR)
        layouts = [BEHAVIOUR["layouts"][0], [[0, 0], [1, 0], [2, 0], [3, 0]], None,
                   [[0, 0], [0, 1], [0, 2]]]
        v4 = choice_layout_journal(v3, layouts)
        old, current = replay_events(v3), replay_events(v4)
        self.assertEqual(current["input"], old["input"])
        first = current["round_evidence"][0]
        self.assertEqual(first["layout"], BEHAVIOUR["layouts"][0])
        self.assertEqual([row["layout"] for row in first["choice_layouts"]], layouts[:3])
        self.assertEqual([row["event_id"] for row in first["choice_layouts"]],
                         [v4["events"][index]["event_id"] for index in (2, 3, 4)])
        self.assertEqual([row["t_ms"] for row in first["choice_layouts"]], [1200, 1500, 2600])
        self.assertEqual(current["round_evidence"][2]["choice_layouts"], [])
        self.assertTrue(all("choice_layouts" not in row for row in old["round_evidence"]))
        self.assertNotEqual(current["events_hash"], old["events_hash"])
        self.assertEqual(old["events_hash"],
                         "sha256:73193f5ed010601934acaf45c94d7c1fb4b49e9fbe6a5de70deb80b8de92dcf7")
        old_profile, profile = (build_event_profile(item, algorithm_version="norn.scoring.v4") for item in (v3, v4))
        self.assertEqual(old_profile["profile_hash"],
                         "sha256:437413d16a0fffbf977a866422213a8e56f7d8b9f6572a6af9def8bab401e62e")
        self.assertNotEqual(profile["profile_hash"], old_profile["profile_hash"])
        self.assertEqual(profile["analysis"]["preference"]["posterior"],
                         old_profile["analysis"]["preference"]["posterior"])
        choice = engine_module("choice_model")
        self.assertEqual(choice.observations(v4), choice.observations(v3))
        legacy = engine_module("preference_model")
        self.assertEqual(legacy.train([v4])["weights"], legacy.train([v3])["weights"])

    def test_v4_rejects_invalid_choice_layouts_and_v3_does_not_accept_v4_fields(self):
        v3 = behaviour_journal(attention_journal(*ATTENTION_ROUNDS), **BEHAVIOUR)
        v4 = choice_layout_journal(v3, [None] * 4)
        for value in ([[0, 0]], [[0, 0]] * 4, [[0, -1], [0, 1], [1, 0], [1, 1]], "unknown"):
            invalid = deepcopy(v4)
            invalid["events"][2]["layout"] = value
            with self.subTest(layout=value), self.assertRaises(ValueError):
                replay_events(invalid)
        missing = deepcopy(v4)
        del missing["events"][2]["layout"]
        with self.assertRaises(ValueError):
            replay_events(missing)
        amended_old = deepcopy(v4)
        amended_old["schema_version"] = BEHAVIOUR_EVENTS_SCHEMA_VERSION
        with self.assertRaises(ValueError):
            replay_events(amended_old)

    def test_canonical_hash_is_key_order_independent_and_finite(self):
        self.assertEqual(content_hash({"b": [1, 2], "a": "文"}),
                         content_hash({"a": "文", "b": [1, 2]}))
        self.assertNotEqual(content_hash([1, 2]), content_hash([2, 1]))
        for value in (float("nan"), float("inf"), {1: "key"}, (1, 2), "\ud800"):
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                content_hash(value)


class VersionedProfileTests(unittest.TestCase):
    def test_profile_replay_and_all_dimension_reconstruction(self):
        journal = journal_fixture()
        bundle = export_replay(journal)
        profile = bundle["profile"]
        self.assertEqual(profile["schema_version"], "norn.profile.v5")
        self.assertEqual(profile["algorithm_version"], "norn.scoring.v5")
        self.assertEqual(profile["source"], "event_journal")
        self.assertNotIn("snapshot_hash", profile)
        self.assertNotIn("input", profile)
        self.assertEqual(profile["metadata_version"], metadata_version())
        self.assertEqual(profile["events_hash"], replay_events(journal)["events_hash"])
        self.assertTrue(verify_profile(profile, journal))
        self.assertEqual(replay_bundle(json.loads(canonical_json(bundle))), profile)
        self.assertEqual(build_profile(journal), profile)
        self.assertEqual(profile["analysis"]["attribution"]["schema_version"], "norn.attribution.v3")
        for heuristic in ("coords_avg", "mbti_type", "confidence", "top_traits", "signature_signal"):
            self.assertNotIn(heuristic, profile["analysis"], "v4 computes no four-axis heuristic")
        older = build_event_profile(journal, algorithm_version="norn.scoring.v3")
        ledger = engine_module("ledger")
        self.assertTrue(ledger.verify_attribution(older["analysis"]["attribution"], older["analysis"]["coords_avg"]))
        self.assertEqual(ledger.reconstruct_coords(older["analysis"]["attribution"]), older["analysis"]["coords_avg"])
        self.assertEqual([row["event_id"] for row in profile["analysis"]["attribution"]["selections"]],
                         ["e09", "e13"])
        self.assertEqual([row["event_id"] for row in profile["analysis"]["attribution"]["revision"]["events"]],
                         ["e04", "e08", "e12"])

    def test_empty_selection_revision_profile_preserves_empty_algorithm(self):
        journal = empty_choice_journal()
        older = build_event_profile(journal, algorithm_version="norn.scoring.v3")
        self.assertEqual(older["analysis"]["mbti_type"], "XXXX")
        self.assertEqual(older["analysis"]["coords_avg"], dict.fromkeys(engine_module("fast_thinker").DIMS, 0))
        profile = build_event_profile(journal)
        self.assertEqual(profile["analysis"]["total_selections"], 0)
        self.assertEqual(profile["analysis"]["preference"]["stop_count"], 1, "An empty round is still one stop choice")
        self.assertEqual(replay_bundle(export_replay(journal)), profile)

    def test_profiles_and_exports_are_detached_from_inputs_and_each_other(self):
        journal = journal_fixture()
        expected = deepcopy(journal)
        bundle = export_replay(journal)
        returned = replay_bundle(bundle)
        returned["analysis"]["preference"]["posterior"]["mean"][0] = 999
        self.assertNotEqual(bundle["profile"]["analysis"]["preference"]["posterior"]["mean"][0], 999)
        bundle["journal"]["events"][0]["card_ids"].clear()
        self.assertEqual(journal, expected)

    def test_any_profile_tampering_fails_even_after_rehash(self):
        original = export_replay(journal_fixture())
        mutations = [
            lambda p: p.update(schema_version="norn.profile.v999"),
            lambda p: p.update(algorithm_version="norn.scoring.v999"),
            lambda p: p.update(metadata_version="sha256:" + "0" * 64),
            lambda p: p.update(events_hash="sha256:" + "0" * 64),
            lambda p: p["analysis"]["preference"]["posterior"]["mean"].__setitem__(1, 999),
            lambda p: p["analysis"]["attribution"]["selections"][0].update(card_id="NORN_0256"),
            lambda p: p.update(source="invented"),
            lambda p: p.update(session_id="should_not_belong_here"),
            lambda p: p.update(profile_hash="中文"),
        ]
        for mutation in mutations:
            bundle = deepcopy(original)
            mutation(bundle["profile"])
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                replay_bundle(bundle)
            if bundle["profile"]["profile_hash"] != "中文":
                profile = bundle["profile"]
                profile["profile_hash"] = content_hash({k: v for k, v in profile.items() if k != "profile_hash"})
                with self.assertRaises(ValueError):
                    replay_bundle(bundle)

    def test_journal_tampering_and_missing_journal_fail_replay(self):
        bundle = export_replay(journal_fixture())
        bundle["journal"]["events"][1]["card_id"] = "NORN_0003"
        with self.assertRaises(ValueError):
            replay_bundle(bundle)
        bundle = export_replay(journal_fixture())
        bundle["journal"] = None
        with self.assertRaises(ValueError):
            replay_bundle(bundle)
        for key, value in (("extra", 1), ("schema_version", "norn.replay.v999")):
            bad = export_replay(journal_fixture())
            bad[key] = value
            with self.assertRaises(ValueError):
                replay_bundle(bad)

    def test_snapshot_replay_does_not_claim_click_evidence(self):
        snapshot = replay_events(journal_fixture())["input"]
        profile = build_snapshot_profile(snapshot)
        self.assertEqual(profile["source"], "legacy_snapshot")
        self.assertEqual(profile["input"], snapshot)
        self.assertEqual(profile["snapshot_hash"], content_hash(snapshot))
        self.assertNotIn("events_hash", profile)
        self.assertNotIn("journal", profile)
        self.assertTrue(verify_profile(profile))
        bundle = build_replay_bundle(profile)
        self.assertIsNone(bundle["journal"])
        self.assertEqual(replay_bundle(bundle), profile)
        self.assertEqual(profile["analysis"]["preference"]["status"], "no_observations",
                         "A snapshot has no displayed sets, so no choice labels")
        self.assertEqual(profile["analysis"]["total_selections"],
                         build_event_profile(journal_fixture())["analysis"]["total_selections"])
        with self.assertRaises(ValueError):
            build_replay_bundle(profile, journal_fixture())

    def test_snapshot_validation_rejects_type_and_field_coercion(self):
        snapshot = replay_events(journal_fixture())["input"]
        for mutation in (
            lambda p: p.update(extra=True),
            lambda p: p.update(total_rounds=True),
            lambda p: p["selections"][0].update(round=999),
            lambda p: p["selections"][0].update(id="missing"),
            lambda p: p["selections"].append(deepcopy(p["selections"][0])),
            lambda p: p["round_durations"].append(deepcopy(p["round_durations"][0])),
            lambda p: p["round_durations"][0].update(duration_ms=float("nan")),
        ):
            bad = deepcopy(snapshot)
            mutation(bad)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                build_snapshot_profile(bad)

    def test_hash_changes_with_evidence_or_metadata_without_changing_coordinates(self):
        journal = journal_fixture()
        initial = build_event_profile(journal)
        journal["events"][0]["event_id"] = "new-display-evidence"
        changed = build_event_profile(journal)
        # Renaming a display event changes provenance, not the choice labels.
        initial_model, changed_model = initial["analysis"]["preference"], changed["analysis"]["preference"]
        self.assertEqual(initial_model["posterior"], changed_model["posterior"])
        self.assertEqual(initial_model["training_hash"], changed_model["training_hash"])
        self.assertNotEqual(initial["events_hash"], changed["events_hash"])
        self.assertNotEqual(initial["profile_hash"], changed["profile_hash"])
        fast = engine_module("fast_thinker")
        cards = deepcopy(fast._load_cards())
        cards["NORN_0256"]["scene"] += " metadata update"
        with patch.object(fast, "_CARDS", cards):
            self.assertNotEqual(metadata_version(), initial["metadata_version"])
            with self.assertRaisesRegex(ValueError, "metadata version"):
                verify_profile(initial, journal_fixture())


if __name__ == "__main__":
    unittest.main()

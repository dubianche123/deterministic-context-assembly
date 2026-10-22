"""Consented visits that end without a reading, and the research deck's optional population context."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from norn.actor_state import new_actor_service
from norn.collection import (CONSENT_VERSION, DisabledCollector, SQLiteCollector, build_partial, visitor_id)
from norn.core import engine_module
from norn.service import NornService, RequestError
from tests.test_research_deck import DECK, research, session

VISITOR = "partial-visit-test-visitor-000001"
KEY = "0123456789abcdef" * 2


def prefix(journal, rounds, open_next=True):
    """A journal as the browser holds it when round rounds + 1 has just started."""
    events, completed = [], 0
    for event in journal["events"]:
        if event["type"] == "round_started" and completed == rounds:
            if open_next:
                events.append(event)
            break
        events.append(event)
        completed += event["type"] == "round_completed"
    return {"schema_version": journal["schema_version"], "events": events}


def active_round(journal, number=1):
    """Retain the real last round's ready/choice facts without inventing a close."""
    events = []
    for event in journal["events"]:
        if event["type"] == "round_completed" and event["round"] == number:
            break
        events.append(deepcopy(event))
    return {"schema_version": journal["schema_version"], "events": events}


def attention(journal, elapsed=1000, **changes):
    start = next(event for event in reversed(journal["events"]) if event["type"] == "round_started")
    ready = next((event for event in journal["events"]
                  if event["type"] == "round_ready" and event["round"] == start["round"]), None)
    return {"round": start["round"], "elapsed_ms": elapsed, "hidden_ms": 0,
            "ready_offset_ms": ready["ready_offset_ms"] if ready else None,
            "seen_card_ids": None, "hover": None, **changes}


class PartialRecordTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = SQLiteCollector(Path(directory.name) / "records.sqlite3")
        self.addCleanup(self.store.close)
        self.service = new_actor_service(clock=lambda: 1000, deck="pamela-v1")
        self.service.collection = self.store
        self.journal = session(research.MIXED, rounds=5)

    def body(self, journal, **changes):
        return {"consent": CONSENT_VERSION, "visitor": VISITOR, "session_key": KEY, "journal": journal, **changes}

    def test_snapshots_replace_each_other_and_the_reading_replaces_them(self):
        for rounds in (0, 2, 4):
            self.assertEqual(self.service.record_partial(self.body(prefix(self.journal, rounds))), {"stored": True})
        [record] = self.store.page()
        self.assertEqual((record["schema_version"], record["record_id"]), ("norn.partial.v1", KEY))
        self.assertEqual(record["visitor_id"], visitor_id(VISITOR))
        self.assertEqual(sum(e["type"] == "round_completed" for e in record["journal"]["events"]), 4)
        self.assertEqual(record["experiments"], {"display": "mixed", "deck": "pamela-v1"})
        self.assertEqual(self.store.stats()["partial_records"], 1)
        self.service.analyze({"journal": self.journal, "consent": CONSENT_VERSION, "visitor": VISITOR,
                              "session_key": KEY, "request_id": "partial-then-full"})
        [record] = self.store.page()
        self.assertEqual(record["schema_version"], "norn.record.v2")
        self.assertEqual((record["visit"], record["experiments"]["session_key"]), (1, KEY))
        self.assertEqual(self.store.stats()["partial_records"], 0)
        # A snapshot can never overwrite a full record, nor another visitor's row.
        self.service.record_partial(self.body(prefix(self.journal, 1), session_key=record["record_id"]))
        self.assertEqual([row["schema_version"] for row in self.store.page()], ["norn.record.v2"])
        other = "another-visitor-key-0000001"
        self.service.record_partial(self.body(prefix(self.journal, 1), session_key="f" * 32))
        self.service.record_partial(self.body(prefix(self.journal, 3), session_key="f" * 32, visitor=other))
        rows = {row["record_id"]: row for row in self.store.page()}
        self.assertEqual(rows["f" * 32]["visitor_id"], visitor_id(VISITOR))
        self.assertEqual(sum(e["type"] == "round_completed" for e in rows["f" * 32]["journal"]["events"]), 1)

    def test_network_reordering_never_rewinds_or_forks_a_visit(self):
        advanced = prefix(self.journal, 4)
        self.assertEqual(self.service.record_partial(self.body(advanced)), {"stored": True})
        self.assertEqual(self.service.record_partial(self.body(prefix(self.journal, 1))), {"stored": False})
        # Identical retries acknowledge the already-kept snapshot, not a new visit.
        self.assertEqual(self.service.record_partial(self.body(advanced)), {"stored": True})
        conflicting = deepcopy(advanced)
        next(event for event in conflicting["events"] if event["type"] == "round_completed")["duration_ms"] += 1
        self.assertEqual(self.service.record_partial(self.body(conflicting)), {"stored": False})
        longer_fork = deepcopy(self.journal)
        longer_fork["events"].pop()  # a valid non-complete journal with a different earlier fact
        next(event for event in longer_fork["events"] if event["type"] == "round_completed")["duration_ms"] += 1
        self.assertEqual(self.service.record_partial(self.body(longer_fork)), {"stored": False})
        [record] = self.store.page()
        self.assertEqual(record["journal"], advanced)
        self.assertEqual(record["visit"], 1)

    def test_current_round_choices_and_dwell_are_retained_without_an_invented_stop(self):
        current = active_round(session(research.RANDOM, rounds=2, keep=lambda _: False))
        self.assertEqual(self.service.record_partial(self.body(current, open_round=attention(current))),
                         {"stored": True})
        start = current["events"][0]
        layout = current["events"][1]["layout"]
        for kind, moment in (("card_selected", 1100), ("card_deselected", 1250), ("card_selected", 1400)):
            current["events"].append({"event_id": f"evt-{len(current['events']) + 1:06d}",
                                      "sequence": len(current["events"]) + 1, "type": kind,
                                      "round": 1, "card_id": start["card_ids"][0], "t_ms": moment,
                                      "input": "mouse", "layout": layout})
            sample = attention(current, moment + 100, seen_card_ids=[start["card_ids"][0]],
                               hover=[[0, 20, moment + 50]])
            self.assertEqual(self.service.record_partial(self.body(current, open_round=sample)), {"stored": True})
        [record] = self.store.page()
        self.assertEqual(record["journal"], current)
        self.assertEqual(record["open_round"]["elapsed_ms"], 1500)
        self.assertEqual([e["type"] for e in current["events"]][-3:],
                         ["card_selected", "card_deselected", "card_selected"])
        self.assertFalse(any(e["type"] in ("round_completed", "session_completed") for e in record["journal"]["events"]))
        self.assertEqual(research.observations(record["journal"]), [], "An open checkpoint cannot become a model STOP")

    def test_same_event_prefix_allows_monotone_attention_and_rejects_stale_or_forked_samples(self):
        current = active_round(session(research.RANDOM, rounds=2, keep=lambda _: False))
        cards = current["events"][0]["card_ids"]
        first = attention(current, 1000, hidden_ms=100, seen_card_ids=cards[:1], hover=[[0, 20, 500]])
        second = attention(current, 2000, hidden_ms=200, seen_card_ids=cards[:2], hover=[[0, 20, 700], [1, 800, 1200]])
        self.assertEqual(self.service.record_partial(self.body(current, open_round=first)), {"stored": True})
        self.assertEqual(self.service.record_partial(self.body(current, open_round=second)), {"stored": True})
        self.assertEqual(self.service.record_partial(self.body(current, open_round=second)), {"stored": True})
        for changes in ({"elapsed_ms": 1500}, {"hidden_ms": 150}, {"seen_card_ids": cards[:1]},
                        {"hover": [[0, 20, 600], [1, 800, 1200]]}, {"hover": None}):
            with self.subTest(changes=changes):
                sample = {**second, **changes}
                self.assertEqual(self.service.record_partial(self.body(current, open_round=sample)), {"stored": False})
        # A legacy retry with the same events may not silently erase measured attention.
        self.assertEqual(self.service.record_partial(self.body(current)), {"stored": False})
        self.assertEqual(self.store.page()[0]["open_round"], second)

    def test_open_checkpoint_is_bound_to_ready_choice_clock_and_displayed_cards(self):
        current = active_round(self.journal)
        sample = attention(current)
        bad = [
            {**sample, "round": 2}, {**sample, "elapsed_ms": True}, {**sample, "elapsed_ms": 0},
            {**sample, "hidden_ms": 1001}, {**sample, "ready_offset_ms": 1},
            {**sample, "seen_card_ids": ["unknown-card"]}, {**sample, "hover": [[0, 0, 1001]]},
            {**sample, "hover": [[0, 0, 100], [1, 90, 120]]}, {**sample, "extra": "invalid"},
        ]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(RequestError):
                self.service.record_partial(self.body(current, open_round=value))
        unready = prefix(self.journal, 0)
        for changes in ({"ready_offset_ms": 0}, {"hover": []}, {"hidden_ms": 1}):
            with self.subTest(changes=changes), self.assertRaises(RequestError):
                self.service.record_partial(self.body(unready, open_round=attention(unready, **changes)))
        self.assertEqual(self.store.page(), [])

    def test_real_close_covers_checkpoint_and_full_upgrade_still_preserves_identity(self):
        full = session(research.RANDOM, rounds=2, keep=lambda _: False)
        current = active_round(full)
        sample = attention(current, 400)
        self.assertEqual(self.service.record_partial(self.body(current, open_round=sample)), {"stored": True})
        # The real first close is 500 ms, so advancing to the second round covers this sample.
        self.assertEqual(self.service.record_partial(self.body(prefix(full, 1))), {"stored": True})
        self.assertNotIn("open_round", self.store.page()[0])
        result = self.service.analyze({"journal": full, "consent": CONSENT_VERSION, "visitor": VISITOR,
                                       "session_key": KEY, "request_id": "complete-after-active-checkpoint"})
        self.assertEqual(result["record"], {"record_id": KEY})
        self.assertEqual((self.store.page()[0]["schema_version"], self.store.page()[0]["visit"]),
                         ("norn.record.v2", 1))
        self.assertEqual(self.service.record_partial(self.body(current, open_round=sample)), {"stored": False})

    def test_close_cannot_discard_a_later_sample_or_revive_a_withdrawn_visit(self):
        full = session(research.RANDOM, rounds=2, keep=lambda _: False)
        current = active_round(full)
        self.service.record_partial(self.body(current, open_round=attention(current, 1000)))
        self.assertEqual(self.service.record_partial(self.body(prefix(full, 1))), {"stored": False})
        self.service.withdraw_record({"visitor": VISITOR, "all": True})
        self.assertEqual(self.service.record_partial(self.body(current, open_round=attention(current, 2000))),
                         {"stored": False})
        self.assertEqual(self.store.page(), [])

    def test_full_upgrades_in_place_and_late_original_snapshots_are_refused(self):
        self.service.record_partial(self.body(prefix(self.journal, 2)))
        self.store.annotate(KEY, {"engagement": {"test_marker": True}})
        other_key = "f" * 32
        self.service.record_partial(self.body(prefix(self.journal, 1), session_key=other_key))
        result = self.service.analyze({"journal": self.journal, "consent": CONSENT_VERSION, "visitor": VISITOR,
                                       "session_key": KEY, "request_id": "upgrade-visit-one"})
        self.assertEqual(result["record"], {"record_id": KEY})
        self.assertEqual(self.service.record_partial(self.body(prefix(self.journal, 4))), {"stored": False})
        records = {record["record_id"]: record for record in self.store.page()}
        self.assertEqual(set(records), {KEY, other_key})
        self.assertEqual(records[KEY]["schema_version"], "norn.record.v2")
        self.assertEqual((records[KEY]["visit"], records[other_key]["visit"]), (1, 2))
        self.assertEqual(records[KEY]["annotations"], {"engagement": {"test_marker": True}})
        # A retry of the archive operation is idempotent, but a different full
        # reading cannot claim that it replaced this completed visit.
        saved = {key: value for key, value in records[KEY].items() if key not in {"visit", "annotations"}}
        self.assertEqual(self.store.submit(saved), {"record_id": KEY})
        changed = deepcopy(saved)
        changed["reading"] += "different reading"
        self.assertIsNone(self.store.submit(changed))
        self.assertEqual(self.store.page()[0]["reading"], saved["reading"])

    def test_withdrawal_barriers_reject_late_partial_and_complete_writes(self):
        self.service.record_partial(self.body(prefix(self.journal, 2)))
        partial = self.store.page()[0]
        self.service.withdraw_record({"visitor": VISITOR, "record_id": KEY})
        self.assertEqual(self.service.record_partial(self.body(prefix(self.journal, 4))), {"stored": False})
        # A different new session of the same visitor is allowed after a single withdrawal.
        other_key = "e" * 32
        self.assertEqual(self.service.record_partial(self.body(prefix(self.journal, 1), session_key=other_key)),
                         {"stored": True})
        self.service.withdraw_record({"visitor": VISITOR, "all": True})
        self.assertEqual(self.service.record_partial(self.body(prefix(self.journal, 4), session_key=other_key)),
                         {"stored": False})
        complete = self.service.analyze({"journal": self.journal, "consent": CONSENT_VERSION, "visitor": VISITOR,
                                         "session_key": KEY, "request_id": "late-complete"})
        self.assertIsNone(complete["record"])
        self.assertEqual(self.store.page(), [])
        # Barriers retain no source keys, record IDs, behavior or payload.
        rows = self.store._execute("SELECT scope, token FROM archive_tombstones")
        self.assertTrue(rows)
        for scope, token in rows:
            self.assertIn(scope, ("visitor", "record"))
            self.assertRegex(token, r"^[a-f0-9]{64}$")
        text = str(rows)
        self.assertNotIn(VISITOR, text)
        self.assertNotIn(KEY, text)
        self.assertNotIn(partial["visitor_id"], text)

    def test_other_visitors_cannot_replace_or_block_an_owned_record(self):
        self.service.record_partial(self.body(prefix(self.journal, 1)))
        other = "another-visitor-key-0000001"
        self.assertEqual(self.service.record_partial(self.body(prefix(self.journal, 4), visitor=other)),
                         {"stored": False})
        with self.assertRaises(RequestError):
            self.service.withdraw_record({"visitor": other, "record_id": KEY})
        self.assertEqual(self.service.record_partial(self.body(prefix(self.journal, 2))), {"stored": True})
        self.assertEqual(self.store.page()[0]["visitor_id"], visitor_id(VISITOR))

    def test_forgetting_a_visitor_removes_partial_records(self):
        self.service.record_partial(self.body(prefix(self.journal, 2)))
        self.service.withdraw_record({"visitor": VISITOR, "all": True})
        self.assertEqual(self.store.page(), [])

    def test_invalid_snapshots_are_refused(self):
        refused = [
            {"journal": prefix(self.journal, 2)},
            self.body(prefix(self.journal, 2), consent="norn.consent.v3"),
            self.body(prefix(self.journal, 2), session_key="not-a-key"),
            self.body(self.journal),  # already revealed: a full record is the place for it
        ]
        other = deepcopy(prefix(self.journal, 2))
        for event in other["events"]:
            if event["type"] == "round_started":
                event["display"]["deck"] = "pamela-v1@sha256:" + "0" * 64
        refused.append(self.body(other))
        for body in refused:
            with self.assertRaises(RequestError):
                self.service.record_partial(body)
        self.assertEqual(self.store.page(), [])

    def test_no_collection_or_classic_deck_stores_nothing(self):
        service = new_actor_service(clock=lambda: 1000, deck="pamela-v1")
        service.collection = DisabledCollector()
        self.assertEqual(service.record_partial(self.body(prefix(self.journal, 1))), {"stored": False})
        self.assertEqual(NornService(provider="mock").record_partial(self.body(prefix(self.journal, 1))),
                         {"stored": False})

    def test_session_key_needs_consent(self):
        with self.assertRaises(RequestError):
            self.service.analyze({"journal": self.journal, "session_key": KEY})


class PopulationContextTests(unittest.TestCase):
    @staticmethod
    def deck_with(context):
        document = deepcopy(DECK.document)
        document["context"] = context
        document.pop("deck_version")
        document["deck_version"] = engine_module("choice_model")._hash(document)
        return research.Deck(document)

    def context(self, **changes):
        return {"schema_version": research.CONTEXT_SCHEMA_VERSION, "gamma": -0.5, "provenance": {"purpose": "test"},
                "offsets": {"4": [0.6, 0.2, -0.2, -0.6], "5": [0.5, 0.25, 0.0, -0.25, -0.5],
                            "6": [0.5, 0.3, 0.1, -0.1, -0.3, -0.5]}, **changes}

    def test_a_context_is_part_of_the_deck_version(self):
        self.assertIsNone(DECK.context)
        deck = self.deck_with(self.context())
        self.assertNotEqual(deck.label, DECK.label)
        self.assertEqual(deck.context["gamma"], -0.5)
        journal = session(research.MIXED, rounds=6)
        plain = research.build_profile(journal, DECK, "sha256:" + "1" * 64)
        rows = [row for row in research.observations(journal) if row["shown"]]
        self.assertTrue(all(len(row["slots"]) == len(row["shown"]) and 4 <= row["m"] <= 6 for row in rows))
        aware = research.session(rows, deck)
        self.assertNotEqual(aware.l2, research.session(rows, DECK).l2)
        self.assertEqual(plain["profile_hash"], research.build_profile(journal, DECK, "sha256:" + "1" * 64)["profile_hash"])

    def test_invalid_contexts_are_refused(self):
        for bad in (self.context(schema_version="other"), self.context(gamma=float("inf")),
                    self.context(offsets={"4": [1.0, 0.0, 0.0, 0.0]}), self.context(offsets={"4": [0.5, -0.5]}),
                    {**self.context(), "extra": 1}):
            with self.assertRaises(ValueError):
                self.deck_with(bad)


if __name__ == "__main__":
    unittest.main()

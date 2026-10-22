"""A/B assignment, engagement counts and the measurement report."""

from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from norn.cli import main
from norn.collection import SQLiteCollector, visitor_id
from norn.experiments import assign
from norn.report import build_report, format_report
from norn.service import NornService
from norn.simulate import simulate_records
from tests.test_collection import CONSENT, OTHER_VISITOR, VISITOR, journal, render

TRIAL = {"trial": {"arms": ("control", "variant")}}


def with_unseen_rounds(journal, rounds):
    """A simulated v1 journal as v2, where the given rounds' cards never reached the screen."""
    events, shown = [], None
    for event in journal["events"]:
        kind = event["type"]
        if kind == "round_started":
            shown = event["card_ids"]
            events += [dict(event), {"type": "round_ready", "round": event["round"], "ready_offset_ms": 0,
                                     "unloaded_card_ids": []}]
        elif kind in ("card_selected", "card_deselected"):
            if event["round"] not in rounds:
                events.append({**event, "t_ms": 100})
        elif kind == "round_completed":
            events.append({**event, "hidden_ms": 0, "seen_card_ids": [] if event["round"] in rounds else list(shown)})
        else:
            events.append(dict(event))
    for number, event in enumerate(events, 1):
        event.update(event_id=f"evt-{number:06d}", sequence=number)
    return {"schema_version": "norn.events.v2", "events": events}


class AssignmentTests(unittest.TestCase):
    def test_assignment_is_stable_balanced_and_validated(self):
        self.assertEqual(assign("anyone", {}), {})
        self.assertEqual(assign("visitor-1", TRIAL), assign("visitor-1", TRIAL))
        counts = Counter(assign(f"visitor-{n}", TRIAL)["trial"] for n in range(2000))
        self.assertLess(abs(counts["control"] - counts["variant"]), 200)
        skewed = Counter(assign(f"v{n}", {"t": {"arms": ("a", "b"), "weights": (9, 1)}})["t"] for n in range(2000))
        self.assertGreater(skewed["a"], 1600)
        for bad in ({"arms": ("only",)}, {"arms": ("a", "b"), "weights": (1, 0)}, {"arms": ("a", "b"), "weights": (1,)}):
            with self.assertRaises(ValueError):
                assign("v", {"x": bad})


class RecordedMeasurementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SQLiteCollector(Path(self.tmp.name) / "records.sqlite3")
        self.service = NornService(generate=render, collector=self.store)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_records_carry_the_visitors_arm_and_dialogue_turn_count(self):
        with patch.dict("norn.experiments.EXPERIMENTS", TRIAL, clear=True):
            first = self.service.analyze({"journal": journal(), **CONSENT, "request_id": "a"})
            self.service.analyze({"journal": journal(), **CONSENT, "request_id": "b"})
            self.service.analyze({"journal": journal(), "consent": CONSENT["consent"], "visitor": OTHER_VISITOR})
        self.service.dialogue({"session_id": first["session_id"], "user_message": "为什么是这张？"})
        rows = self.store.page()
        mine = [row for row in rows if row["visitor_id"] == visitor_id(VISITOR)]
        expected = assign(visitor_id(VISITOR), TRIAL)
        self.assertEqual([row["experiments"] for row in mine], [expected, expected])
        engaged = next(row for row in mine if row["record_id"] == first["record"]["record_id"])
        self.assertEqual(engaged["annotations"]["engagement"], {"dialogue_turns": 1, "max_turns": 1})  # 2 rounds allow one follow-up
        self.assertNotIn("为什么是这张", json.dumps(rows, ensure_ascii=False))

    def test_cli_report_reads_the_local_store(self):
        self.service.analyze({"journal": journal(), **CONSENT})
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            self.assertEqual(main(["records", "report", "--data-dir", self.tmp.name, "--json", "--draws", "50"]), 0)
        report = json.loads(out.getvalue())
        self.assertEqual((report["volume"]["records"], report["volume"]["visitors"]), (1, 1))
        self.assertEqual(report["validity"]["cross_visit"]["status"], "insufficient_returning_visitors")


class SimulatedReportTests(unittest.TestCase):
    def test_report_finds_a_stable_person_and_not_strangers(self):
        stable = build_report(simulate_records(24, visits=(2, 2), stability=1.0, seed=3), draws=200)
        cross = stable["validity"]["cross_visit"]
        self.assertLess(cross["personal_minus_stranger"]["ci95"][1], 0)
        self.assertGreater(cross["same_type_rate"]["value"], cross["same_type_chance"])
        self.assertTrue(cross["person_specific_detected"] and cross["stable_preference_detected"])
        strangers = build_report(simulate_records(24, visits=(2, 2), stability=0.0, seed=3), draws=200)
        unlinked = strangers["validity"]["cross_visit"]["personal_minus_stranger"]
        self.assertLess(unlinked["ci95"][0], 0)
        self.assertGreater(unlinked["ci95"][1], 0)
        self.assertFalse(strangers["validity"]["cross_visit"]["person_specific_detected"])
        self.assertFalse(strangers["validity"]["cross_visit"]["stable_preference_detected"])
        self.assertIn("next visit log-loss", format_report(stable))

    def test_a_lasting_stop_habit_alone_is_not_reported_as_a_preference(self):
        # No picture preference, but each person keeps their own stop habit across
        # visits. A stranger's model with the stranger's stop habit loses to the
        # visitor's own here, so the stranger gets the visitor's stop habit too.
        records = simulate_records(30, visits=(2, 2), stability=1.0, scale=0.0, stop_habit=1.0, seed=4)
        report = build_report(records, draws=200)
        self.assertLess(report["outcome"]["holdout_gain_vs_stop_nats"]["value"], 0)
        cross = report["validity"]["cross_visit"]
        self.assertGreaterEqual(cross["personal_minus_stop_only"]["value"], 0)
        low, high = cross["personal_minus_stranger"]["ci95"]
        self.assertLess(low, 0)
        self.assertGreater(high, 0)
        self.assertFalse(cross["person_specific_detected"] or cross["stable_preference_detected"])
        self.assertIn("person-specific picture signal: not shown", format_report(report))

    def test_the_stranger_keeps_the_profile_stop_only_baseline_at_zero_weights(self):
        from norn.core import engine_module
        from norn.report import _stop_only_theta, _with_own_stop
        model = engine_module("choice_model")
        catalog = model.load_catalog()
        rows = model.observations(simulate_records(1, visits=(1, 1), seed=3)[0]["journal"], catalog)
        own = _with_own_stop(model, catalog, [0.0] * model.PARAMETERS, rows)
        self.assertAlmostEqual(own[-1], _stop_only_theta(model, catalog, rows)[-1], places=12)
        # Extreme stranger weights overshoot plain Newton; the bracketed fit still converges.
        for weight in (-40.0, 40.0):
            steep = _with_own_stop(model, catalog, [weight] * model.FEATURES + [0.0], rows)
            self.assertEqual(steep[:model.FEATURES], [weight] * model.FEATURES)
            self.assertTrue(math.isfinite(steep[-1]))

    def test_v4_reference_report_and_v4_profile_share_the_validation_split(self):
        from norn.core import engine_module
        from norn.profile import build_event_profile
        from norn.report import _derive
        model = engine_module("choice_model")
        catalog = model.load_catalog()
        record = simulate_records(1, visits=(1, 1), rounds=(10, 12), seed=1)[0]
        # Two rounds whose cards never reached the screen used to shift the report's split.
        record["journal"] = with_unseen_rounds(record["journal"], {2, 3})
        validation = build_event_profile(record["journal"], algorithm_version="norn.scoring.v4")["analysis"]["preference"]["validation"]
        self.assertEqual(validation["status"], "measured")
        derived = _derive(record, model, catalog)
        self.assertEqual(derived["holdout_gain_vs_stop"],
                         validation["stop_only_baseline"]["log_loss"] - validation["log_loss"])
        self.assertEqual(derived["holdout_gain"], validation["random_baseline"]["log_loss"] - validation["log_loss"])
        self.assertEqual(derived["rounds"], len(model.observations(record["journal"], catalog)) - 2)

    def test_stranger_is_matched_on_history_size(self):
        report = build_report(simulate_records(24, visits=(2, 2), rounds=(4, 14), seed=6), draws=50)
        cross = report["validity"]["cross_visit"]
        self.assertLessEqual(cross["stranger_evidence_gap_median"], 3)

    def test_groups_compare_arms_against_the_control(self):
        records = simulate_records(80, visits=(1, 1), rounds=(4, 6), seed=5,
                                   arms=("trial", ("control", "variant")), rating_effect=1.0)
        report = build_report(records, by="experiments.trial", control="control", draws=200)
        variant = report["groups"]["arms"]["variant"]
        self.assertGreater(variant["minus_control"]["rating"]["ci95"][0], 0)
        self.assertEqual(report["groups"]["arms"]["control"]["records"] + variant["records"], 80)
        self.assertIn("Δrating", format_report(report))

    def test_simulated_journals_are_valid_app_journals(self):
        from norn.events import replay_events
        for record in simulate_records(3, visits=(1, 2), rounds=(12, 14), seed=2):
            replay_events(record["journal"])

    def test_broken_records_are_counted_not_fatal(self):
        report = build_report([{"journal": {"events": "nope"}}, *simulate_records(2, visits=(1, 1), seed=1)],
                              draws=20)
        self.assertEqual((report["volume"]["records"], report["volume"]["skipped_records"]), (2, 1))


if __name__ == "__main__":
    unittest.main()

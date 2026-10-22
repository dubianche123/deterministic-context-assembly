"""The offline diagnosis preserves exposure, targets and scoring denominators."""

from copy import deepcopy
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from norn.core import engine_module
from norn.events import replay_events
from norn.fast_thinkers import LocalPredictor
from scripts import fast_thinker_diagnostic as diagnostic


model = engine_module("choice_model")
numeric = engine_module("numeric")


class FastThinkerDiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = model.load_catalog()
        cls.spaces = diagnostic._card_spaces(cls.catalog)

    def test_exposure_is_reproducible_without_repeats_or_deck_exhaustion(self):
        shown = diagnostic.exposure(11, 0, self.catalog)
        self.assertEqual(shown, diagnostic.exposure(11, 0, self.catalog))
        self.assertNotEqual(shown, diagnostic.exposure(11, 1, self.catalog))
        self.assertEqual(len(shown), 40)
        self.assertTrue(all(4 <= len(row) <= 6 for row in shown))
        cards = [card for row in shown for card in row]
        self.assertEqual(len(cards), len(set(cards)))
        self.assertLessEqual(len(cards), 240)
        self.assertTrue(set(cards).issubset(self.catalog.ids))

    def test_all_cells_share_exposure_and_constant_duration(self):
        shown = diagnostic.exposure(29, 3, self.catalog)
        before = deepcopy(shown)
        stop_bars = []
        for cell in diagnostic.CELLS:
            with self.subTest(cell=cell):
                journal, stats = diagnostic.simulated_visit(cell, 29, 3, self.catalog, self.spaces, shown)
                replay_events(journal)
                self.assertEqual([e["card_ids"] for e in journal["events"]
                                  if e["type"] == "round_started"], shown)
                self.assertEqual([e["duration_ms"] for e in journal["events"]
                                  if e["type"] == "round_completed"], [3000] * 40)
                stop_bars.append(stats["stop_bar"])
                if cell == "position_only":
                    self.assertEqual(stats["first_slot_target_fraction"], 1.0)
                    self.assertEqual(stats["mean_kept_per_round"], 1.0)
        self.assertEqual(stop_bars, [stop_bars[0]] * len(stop_bars))
        self.assertEqual(shown, before)

    def test_pixel_reversal_keeps_prefix_and_negates_only_future_utility(self):
        shown = diagnostic.exposure(47, 2, self.catalog)
        captured = []
        choose = diagnostic._choose_threshold

        def record(rng, utility, bar, candidates):
            captured.append(dict(utility))
            return choose(rng, utility, bar, candidates)

        with patch.object(diagnostic, "_choose_threshold", side_effect=record):
            stable, _ = diagnostic.simulated_visit("pixel_threshold", 47, 2, self.catalog, self.spaces, shown)
            reversal, _ = diagnostic.simulated_visit("pixel_reversal", 47, 2, self.catalog, self.spaces, shown)
        self.assertEqual(diagnostic.prefix_of(stable, 32), diagnostic.prefix_of(reversal, 32))
        stable_values, reversed_values = captured[:40], captured[40:]
        self.assertEqual(stable_values[:32], reversed_values[:32])
        for number in range(32, 40):
            self.assertEqual(reversed_values[number],
                             {card: -value for card, value in stable_values[number].items()})
        # Exact utility equality is the intervention contract; random choices
        # need not change on every round when the sign changes.

    def test_prefix_is_detached_and_ignores_all_later_events(self):
        shown = diagnostic.exposure(11, 5, self.catalog)
        journal, _ = diagnostic.simulated_visit("position_only", 11, 5, self.catalog, self.spaces, shown)
        expected = diagnostic.prefix_of(journal, 8)
        prefix = diagnostic.prefix_of(journal, 8)
        self.assertEqual(prefix["events"][-1]["type"], "round_completed")
        self.assertEqual(prefix["events"][-1]["round"], 8)
        self.assertEqual(len(model.observations(prefix, self.catalog)), 8)
        for event in journal["events"]:
            if event.get("round", 0) > 8 and event["type"] == "round_started":
                event["card_ids"] = ["unusable_future_card"]
        self.assertEqual(diagnostic.prefix_of(journal, 8), expected)
        prefix["events"][0]["card_ids"].reverse()
        self.assertEqual(diagnostic.prefix_of(journal, 8), expected)

    def test_every_prefix_uses_the_same_final_eight_test_rounds(self):
        fitted_prefixes, scored_rounds = [], []

        def fake_predictors(prefix, rows, catalog):
            self.assertIs(catalog, self.catalog)
            count = len(rows)
            self.assertEqual([r["round"] for r in rows], list(range(1, count + 1)))
            self.assertEqual(prefix["events"][-1]["round"], count)
            self.assertEqual(model.observations(prefix, catalog), rows)
            fitted_prefixes.append(count)
            return {"count": count}

        def fake_score(rows, fitted, catalog, *, first_only):
            scored_rounds.append((fitted["count"], first_only, [r["round"] for r in rows]))
            return {name: {"all_nll": 0.0} for name in diagnostic.NAMES}

        with patch.object(diagnostic, "SEEDS", (11,)), \
                patch.object(diagnostic, "VISITORS_PER_SEED", 1), \
                patch.object(diagnostic, "CELLS", ("features_threshold",)), \
                patch.object(diagnostic, "fixture_audit", return_value={}), \
                patch.object(diagnostic, "predictors", side_effect=fake_predictors), \
                patch.object(diagnostic, "score", side_effect=fake_score), \
                patch.object(diagnostic, "aggregate", return_value={}):
            report = diagnostic.run()
        self.assertEqual(fitted_prefixes, list(diagnostic.PREFIXES))
        for offset, prefix in enumerate(diagnostic.PREFIXES):
            expected = [
                (prefix, True, list(range(33, 41))),
                (prefix, True, list(range(1, prefix + 1))),
                (prefix, False, list(range(33, 41))),
                (prefix, False, list(range(1, prefix + 1))),
            ]
            self.assertEqual(scored_rounds[offset * 4:offset * 4 + 4], expected)
        self.assertEqual(report["api_calls"], 0)

    def test_score_keeps_card_stop_denominators_and_excludes_forced_stop(self):
        rows = [
            {"round": 1, "shown": ["NORN_0001", "NORN_0017"],
             "picks": ["NORN_0001", "NORN_0017"], "stopped": True},
            {"round": 2, "shown": ["NORN_0002", "NORN_0018", "NORN_0003"],
             "picks": [], "stopped": True},
            {"round": 3, "shown": ["NORN_0004", "NORN_0020"],
             "picks": ["NORN_0004"], "stopped": True},
        ]
        uniform = LocalPredictor(dict.fromkeys(self.catalog.ids, 0), 0, {}, 0)
        full = diagnostic.score(rows, {"uniform": uniform}, self.catalog, first_only=False)["uniform"]
        self.assertEqual(full["decision_count"], 5)
        self.assertEqual(full["cards_count"], 3)
        self.assertEqual(full["within_type_count"], 2)
        expected = (2 * math.log(3) + 2 * math.log(2) + math.log(4)) / 5
        self.assertAlmostEqual(full["all_nll"], expected, delta=1e-12)
        self.assertAlmostEqual(full["cards_nll"], 2 * math.log(2) / 3, delta=1e-12)
        self.assertAlmostEqual(full["stop_binary_nll"] + 3 / 5 * full["cards_nll"],
                               full["all_nll"], delta=1e-12)
        first = diagnostic.score(rows, {"uniform": uniform}, self.catalog, first_only=True)["uniform"]
        self.assertEqual((first["decision_count"], first["cards_count"]), (3, 2))
        self.assertAlmostEqual(first["all_nll"], (2 * math.log(3) + math.log(4)) / 3, delta=1e-12)
        empty = diagnostic.score([], {"uniform": uniform}, self.catalog, first_only=False)["uniform"]
        self.assertEqual(empty["decision_count"], 0)
        self.assertIsNone(empty["all_nll"])
        self.assertIsNone(empty["stop_binary_nll"])

    def test_fixture_audit_reconstructs_saved_scores_and_converged_objective(self):
        with numeric.use(numeric.PORTABLE_CONTRACT):
            audit = diagnostic.fixture_audit(self.catalog)
        heldout = audit["groups"]["test_9_24"]
        self.assertAlmostEqual(heldout["first"]["heuristic"]["all_nll"], 1.7162204189315207, delta=1e-12)
        self.assertAlmostEqual(heldout["first"]["ml"]["all_nll"], 2.314665075622904, delta=1e-12)
        self.assertEqual(heldout["first"]["ml"]["decision_count"], 16)
        self.assertEqual(heldout["full"]["ml"]["decision_count"], 32)
        self.assertEqual(audit["training_test_card_overlap"], [])
        self.assertTrue(audit["display_order_invariance"]["same_prefix_and_targets"])
        self.assertLessEqual(audit["display_order_invariance"]["max_abs_probability_delta"], 1e-12)
        numerical = audit["numerical_audit"]
        self.assertLess(numerical["max_abs_gradient"], 1e-8)
        self.assertLess(numerical["max_gradient_error"], 1e-7)
        self.assertLess(numerical["max_negative_hessian_error"], 1e-7)
        self.assertAlmostEqual(numerical["adapter_minus_engine_nll"], 0, delta=1e-12)
        self.assertGreater(numerical["log_posterior_gain"], 0)
        self.assertAlmostEqual(numerical["log_posterior_gain"],
                               numerical["fitted_log_posterior"] - numerical["initial_log_posterior"],
                               delta=1e-12)
        gap = audit["first_loss_gap_identity_not_causal"]
        self.assertAlmostEqual(gap["stop_mass"] + gap["type_mass_given_card"] + gap["within_type"],
                               gap["all"], delta=1e-12)

    def test_aggregate_uses_equal_visitor_weight_and_paired_eligible_counts(self):
        visitors = []
        for index in range(2):
            metrics = {
                name: {"all_nll": (index + 1) * (2 if name == "ml" else 1),
                       "cards_nll": None if index == 0 else (2 if name == "ml" else 1),
                       "within_type_nll": None, "stop_binary_nll": 0.5,
                       "decision_count": 100 if index == 0 else 1}
                for name in diagnostic.NAMES
            }
            visitors.append({"prefixes": {"4": {"first": metrics}}})
        with patch.object(diagnostic, "PREFIXES", (4,)), patch.object(diagnostic, "MODES", ("first",)):
            result = diagnostic.aggregate(visitors)["4"]["first"]
        self.assertEqual(result["all_nll"]["models"]["ml"], {"mean": 3.0, "visitors": 2})
        self.assertEqual(result["all_nll"]["paired"]["ml_minus_heuristic"]["mean"], 1.5)
        cards = result["cards_nll"]["paired"]["ml_minus_heuristic"]
        self.assertEqual(cards, {"mean": 1.0, "ci95": [1.0, 1.0], "visitors": 1})
        within = result["within_type_nll"]["paired"]["ml_minus_heuristic"]
        self.assertEqual(within, {"mean": None, "ci95": None, "visitors": 0})

    def test_existing_result_is_rejected_before_running(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "existing.json"
            destination.write_text("preserved original", encoding="utf-8")
            with patch.object(diagnostic, "run", side_effect=AssertionError("must not simulate")) as run:
                with self.assertRaises(FileExistsError):
                    diagnostic.main(["--output", str(destination)])
            run.assert_not_called()
            self.assertEqual(destination.read_text(encoding="utf-8"), "preserved original")


if __name__ == "__main__":
    unittest.main()

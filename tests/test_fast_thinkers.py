"""Local comparison adapters preserve each algorithm and its frozen prefix."""

from copy import deepcopy
import math
import unittest
from unittest.mock import patch

from norn.core import engine_module
from norn.fast_thinkers import STOP, prepare_local, prepare_policy, validate_probabilities


model = engine_module("choice_model")
legacy = engine_module("fast_thinker")
numeric = engine_module("numeric")


def journal():
    return {"schema_version": "norn.events.v1", "events": [
        {"type": "round_started", "round": 1, "card_ids": ["NORN_0001", "NORN_0002", "NORN_0008"]},
        {"type": "card_selected", "round": 1, "card_id": "NORN_0002"},
        {"type": "card_deselected", "round": 1, "card_id": "NORN_0002"},
        {"type": "card_selected", "round": 1, "card_id": "NORN_0001"},
        {"type": "round_completed", "round": 1, "duration_ms": 1500},
        {"type": "round_started", "round": 2, "card_ids": ["NORN_0017", "NORN_0018", "NORN_0024"]},
        {"type": "card_selected", "round": 2, "card_id": "NORN_0017"},
        {"type": "card_selected", "round": 2, "card_id": "NORN_0024"},
        {"type": "round_completed", "round": 2, "duration_ms": 9500},
        {"type": "round_started", "round": 3, "card_ids": ["NORN_0033", "NORN_0034"]},
        {"type": "round_completed", "round": 3, "duration_ms": 3000},
    ]}


class FastThinkerAdapterTests(unittest.TestCase):
    def test_ml_choice_probabilities_reconstruct_original_loss(self):
        source = journal()
        rows = model.observations(source)
        catalog = model.load_catalog()
        heldout = [
            {"round": 4, "shown": ["NORN_0051", "NORN_0052", "NORN_0056"],
             "picks": ["NORN_0051", "NORN_0056"], "stopped": True},
            {"round": 5, "shown": ["NORN_0067", "NORN_0068"], "picks": [], "stopped": True},
        ]
        for contract in (numeric.NATIVE_CONTRACT, numeric.PORTABLE_CONTRACT):
            with self.subTest(contract=contract), numeric.use(contract):
                predictors = prepare_local(source, rows)
                theta, _, _ = model.fit(model.Prepared(rows, catalog))
                expected, _, count = model._log_loss(theta, model.Prepared(heldout, catalog))
                losses = []
                for row in heldout:
                    remaining = list(row["shown"])
                    for choice in [*row["picks"], STOP]:
                        probabilities = predictors["ml"].predict(remaining)
                        losses.append(-numeric.log(probabilities[choice]))
                        if choice != STOP:
                            remaining.remove(choice)
                self.assertEqual(len(losses), count)
                self.assertAlmostEqual(math.fsum(losses) / count, expected, delta=1e-12)

    def test_heuristic_uses_real_final_pick_coordinates_and_fixed_formula(self):
        source = journal()
        rows = model.observations(source)
        with patch.object(legacy, "fast_think", wraps=legacy.fast_think) as called:
            predictor = prepare_local(source, rows)["heuristic"]
        called.assert_called_once_with(
            [{"id": "NORN_0001", "round": 1}, {"id": "NORN_0017", "round": 2},
             {"id": "NORN_0024", "round": 2}], total_rounds=3,
            round_durations=[{"round": 1, "duration_ms": 1500},
                             {"round": 2, "duration_ms": 9500},
                             {"round": 3, "duration_ms": 3000}])
        coordinates = legacy.fast_think(called.call_args.args[0], **called.call_args.kwargs)["coords_avg"]
        candidates = ["NORN_0065", "NORN_0072"]
        cards = legacy._load_cards()
        utilities = [sum((coordinates[axis] / 8) * (cards[card]["coords"][axis] / 8)
                         for axis in model.AXES) for card in candidates]
        denominator = math.fsum([*(math.exp(value) for value in utilities), 1.0])
        actual = predictor.predict(candidates)
        for card, utility in zip(candidates, utilities):
            self.assertAlmostEqual(actual[card], math.exp(utility) / denominator, delta=1e-14)
        self.assertAlmostEqual(actual[STOP], 1 / denominator, delta=1e-14)
        self.assertFalse(predictor.metadata["fitted"])
        self.assertIn("New fixed probability adapter", predictor.metadata["semantics"])

    def test_heuristic_predictions_do_not_depend_on_the_ml_fit(self):
        source = journal()
        rows = model.observations(source)
        candidates = ["NORN_0065", "NORN_0072"]
        reference = prepare_local(source, rows)["heuristic"].predict(candidates)
        with patch.object(model, "fit", return_value=([7.0] * model.PARAMETERS, [], 1)):
            predictors = prepare_local(source, rows)
        with patch.object(model, "fit", side_effect=AssertionError("prediction refitted")), \
                patch.object(legacy, "fast_think", side_effect=AssertionError("prediction re-read prefix")):
            self.assertEqual(predictors["heuristic"].predict(candidates), reference)

    def test_stop_only_matches_engine_and_prior_is_uniform(self):
        source = journal()
        rows = model.observations(source)
        predictors = prepare_local(source, rows)
        bias = model._fit_stop_only(model.Prepared(rows, model.load_catalog()))
        probabilities = predictors["stop_only"].predict(["NORN_0065", "NORN_0072"])
        self.assertAlmostEqual(probabilities[STOP], math.exp(bias) / (2 + math.exp(bias)), delta=1e-14)
        self.assertEqual(probabilities["NORN_0065"], probabilities["NORN_0072"])
        prior = prepare_local({"events": []}, [])
        self.assertEqual(set(prior), {"heuristic", "ml", "stop_only"})
        for predictor in prior.values():
            self.assertEqual(predictor.predict(["NORN_0001", "NORN_0008"]),
                             {"NORN_0001": 1 / 3, "NORN_0008": 1 / 3, STOP: 1 / 3})
            self.assertEqual(predictor.predict([]), {STOP: 1.0})
            self.assertTrue(predictor.metadata["prior_fallback"])

    def test_prepared_prefix_is_frozen_against_later_events_and_input_mutation(self):
        source = journal()
        rows = model.observations(source)
        predictors = prepare_local(source, rows)
        candidates = ["NORN_0065", "NORN_0072"]
        expected = {name: predictor.predict(candidates) for name, predictor in predictors.items()}
        metadata = {name: deepcopy(predictor.metadata) for name, predictor in predictors.items()}
        source["events"].extend([
            {"type": "round_started", "round": 4, "card_ids": candidates},
            {"type": "card_selected", "round": 4, "card_id": candidates[1]},
            {"type": "round_completed", "round": 4, "duration_ms": 40000}])
        source["events"][4]["duration_ms"] = 999999
        rows[0]["picks"][:] = ["NORN_0008"]
        for name, predictor in predictors.items():
            self.assertEqual(predictor.predict(candidates), expected[name])
            self.assertEqual(predictor.metadata, metadata[name])
            self.assertEqual(predictor.metadata["training_round_count"], 3)
            self.assertEqual(predictor.metadata["feature_version"], model.load_catalog().feature_version)
            self.assertGreaterEqual(predictor.prepare_ms, 0)

    def test_validator_rejects_missing_unknown_nonfinite_negative_and_unnormalized(self):
        candidates = ["NORN_0001"]
        invalid = [None, [], {STOP: 1.0}, {"NORN_0001": 0.5, STOP: 0.5, "extra": 0.0},
                   {"NORN_0001": math.nan, STOP: 0.5}, {"NORN_0001": math.inf, STOP: 0.5},
                   {"NORN_0001": -0.1, STOP: 1.1}, {"NORN_0001": 0.2, STOP: 0.5},
                   {"NORN_0001": True, STOP: 0.0}, {"NORN_0001": "0.5", STOP: 0.5},
                   {"NORN_0001": 10 ** 1000, STOP: 0.0}]
        for distribution in invalid:
            with self.subTest(distribution=distribution), self.assertRaises(ValueError):
                validate_probabilities(distribution, candidates)
        valid = {"NORN_0001": 0.5, STOP: 0.5}
        self.assertEqual(validate_probabilities(valid, candidates), valid)
        self.assertEqual(validate_probabilities({STOP: 1}, []), {STOP: 1.0})
        for bad_candidates in (["NORN_0001", "NORN_0001"], [STOP], "NORN_0001", [1]):
            with self.subTest(candidates=bad_candidates), self.assertRaises(ValueError):
                validate_probabilities(valid, bad_candidates)

    def test_unknown_cards_and_disagreeing_training_prefix_are_rejected(self):
        source = journal()
        rows = model.observations(source)
        for predictor in prepare_local(source, rows).values():
            with self.assertRaisesRegex(ValueError, "Unknown card"):
                predictor.predict(["NOT_A_CARD"])
        rows[0]["picks"] = []
        with self.assertRaisesRegex(ValueError, "match"):
            prepare_local(source, rows)

    def test_single_round_prefix_uses_only_visible_final_picks(self):
        source = {"events": journal()["events"][:5]}
        source["events"][-1]["seen_card_ids"] = ["NORN_0008"]
        rows = model.observations(source)
        self.assertEqual((rows[0]["shown"], rows[0]["picks"]), (["NORN_0008"], []))
        with patch.object(legacy, "fast_think", wraps=legacy.fast_think) as called:
            predictors = prepare_local(source, rows)
        called.assert_called_once_with([], total_rounds=1,
                                       round_durations=[{"round": 1, "duration_ms": 1500}])
        self.assertTrue(predictors["heuristic"].metadata["prior_fallback"])
        for predictor in predictors.values():
            self.assertEqual(predictor.metadata["training_round_count"], 1)
            validate_probabilities(predictor.predict(["NORN_0017", "NORN_0024"]),
                                   ["NORN_0017", "NORN_0024"])

    def test_policy_without_content_evidence_is_exactly_the_stop_reference(self):
        source = journal()
        source["events"] = [event for event in source["events"]
                            if event["type"] not in ("card_selected", "card_deselected")]
        rows = model.observations(source)
        candidates = ["NORN_0065", "NORN_0072", "NORN_0001"]
        for contract in (numeric.NATIVE_CONTRACT, numeric.PORTABLE_CONTRACT):
            with self.subTest(contract=contract), numeric.use(contract):
                original = prepare_local(source, rows)
                predictor = prepare_policy(source, rows)
                self.assertEqual(predictor.predict(candidates), original["stop_only"].predict(candidates))
                self.assertEqual(predictor.metadata["guard"]["status"], "baseline")
                self.assertEqual(predictor.metadata["effective_theta"][:12], [0.0] * 12)
                self.assertTrue(predictor.metadata["frozen_prefix"])
                self.assertTrue(predictor.metadata["prior_fallback"])

    def test_policy_precomputed_state_is_bound_to_training_choices_and_prediction_never_fits(self):
        policy = engine_module("choice_policy")
        source = journal()
        rows = model.observations(source)
        state = policy.fit_policy(rows)
        with patch.object(policy, "fit_policy", side_effect=AssertionError("state must be reused")):
            predictor = prepare_policy(source, rows, state=state)
        candidates = ["NORN_0065", "NORN_0072"]
        expected = predictor.predict(candidates)
        state["effective_theta"][0] = 1000
        source["events"][-1]["duration_ms"] = 9999
        rows[0]["picks"].clear()
        with patch.object(policy, "fit_policy", side_effect=AssertionError("prediction must stay frozen")), \
                patch.object(model, "fit", side_effect=AssertionError("prediction must not fit raw ML")):
            self.assertEqual(predictor.predict(candidates), expected)
        other = journal()
        other_rows = model.observations(other)
        wrong = policy.fit_policy(other_rows[:-1])
        with self.assertRaisesRegex(ValueError, "state must match"):
            prepare_policy(other, other_rows, state=wrong)
        other_rows[0]["picks"].clear()
        with self.assertRaisesRegex(ValueError, "Training choices must match"):
            prepare_policy(other, other_rows)


if __name__ == "__main__":
    unittest.main()

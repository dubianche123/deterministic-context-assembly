"""Synthetic learning checks are algorithm tests, not real-user accuracy claims."""

from copy import deepcopy
import json
import math
from pathlib import Path
import random
import tempfile
import time
import unittest
from unittest.mock import patch

from norn.core import engine_module
from norn.learning import (train, summarize, rank, verify_checkpoint, observations_from_journals,
                           load_catalog, feature_label, save_checkpoint, load_checkpoint, predict_scores)

model = engine_module("preference_model")


def journal(rounds, *, complete=True, leave_open=False):
    events, active = [], set()
    def add(kind, **fields):
        events.append({"sequence": len(events) + 1, "event_id": "event-" + str(len(events) + 1), "type": kind, **fields})
    for number, (displayed, selected) in enumerate(rounds, 1):
        add("round_started", round=number, card_ids=list(displayed))
        for card in displayed:
            if card in active and card not in selected:
                add("card_deselected", round=number, card_id=card)
                active.remove(card)
        for card in selected:
            if card not in active:
                add("card_selected", round=number, card_id=card)
                active.add(card)
        if not (leave_open and number == len(rounds)):
            add("round_completed", round=number, duration_ms=1000)
    if complete:
        add("session_completed")
    return {"schema_version": "norn.events.v1", "events": events}


def learning_fixture(count=40, shuffled=False):
    cards, rounds = {}, []
    labels = [True] * count
    if shuffled:
        labels = [index % 2 == 0 for index in range(count)]
        random.Random(20261020).shuffle(labels)
    for index in range(count):
        positive, negative = f"card-{index}-bright", f"card-{index}-dark"
        cards[positive], cards[negative] = [1.0], [-1.0]
        rounds.append(([positive, negative], [positive if labels[index] else negative]))
    catalog = {"schema_version": "norn.features.v1", "feature_names": ["visual.brightness"], "cards": cards}
    return catalog, journal(rounds)


class PreferenceModelTests(unittest.TestCase):
    def test_learns_a_planted_preference_and_predicts_disjoint_heldout_cards(self):
        catalog, source = learning_fixture(80)
        checkpoint = train([source], catalog=catalog)
        summary = summarize(checkpoint, catalog=catalog)
        self.assertTrue(summary["trained"])
        self.assertGreater(checkpoint["weights"][0], 0)
        self.assertEqual(summary["validation"]["status"], "measured")
        self.assertEqual(summary["validation"]["holdout_observation_count"], 16)
        self.assertEqual(summary["validation"]["train_observation_count"], 64)
        self.assertEqual(summary["validation"]["pairwise_accuracy"], 1.0)
        self.assertLess(summary["validation"]["log_loss"], math.log(2))
        self.assertTrue(summary["validation"]["beats_random"])
        self.assertIn("not personality", summary["score_semantics"])

    def test_shuffled_labels_do_not_claim_high_accuracy(self):
        catalog, source = learning_fixture(200, shuffled=True)
        checkpoint = train([source], catalog=catalog)
        validation = checkpoint["validation"]
        self.assertEqual(validation["status"], "measured")
        self.assertLessEqual(validation["pairwise_accuracy"], 0.65)
        self.assertGreater(validation["log_loss"], 0.60)
        self.assertEqual(validation["random_baseline"]["pairwise_accuracy"], 0.5)

    def test_validation_fit_does_not_train_on_holdout_labels(self):
        cards, rounds = {}, []
        for index in range(10):
            a, b = f"a-{index}", f"b-{index}"
            values = [1.0, 0.0] if index < 8 else [0.0, 1.0]
            cards[a], cards[b] = values, [-value for value in values]
            rounds.append(([a, b], [a]))
        catalog = {"schema_version": "norn.features.v1", "feature_names": ["visual.one", "visual.two"], "cards": cards}
        checkpoint = train([journal(rounds)], catalog=catalog)
        # Full-data model learns the second feature, but the validation model
        # never observes it with a label and must tie on the two holdout rounds.
        self.assertGreater(checkpoint["weights"][1], 0)
        self.assertEqual(checkpoint["validation"]["pairwise_accuracy"], 0.5)
        self.assertAlmostEqual(checkpoint["validation"]["log_loss"], math.log(2))
        self.assertFalse(checkpoint["validation"]["beats_random"])

    def test_repeated_displays_are_purged_from_independent_validation(self):
        catalog = {"schema_version": "norn.features.v1", "feature_names": ["visual.one"],
                   "cards": {"a": [1.0], "b": [-1.0]}}
        source = journal([(["a", "b"], ["a"])] * 10)
        checkpoint = train([source], catalog=catalog)
        self.assertEqual(checkpoint["validation"]["status"], "insufficient")
        self.assertEqual(checkpoint["validation"]["purged_observation_count"], 8)
        self.assertEqual(checkpoint["validation"]["train_pair_count"], 0)
        self.assertFalse(checkpoint["validation"]["beats_random"])

    def test_small_holdout_retains_point_metric_but_stays_insufficient(self):
        catalog, source = learning_fixture(5)
        validation = train([source], catalog=catalog)["validation"]
        self.assertEqual(validation["holdout_observation_count"], 1)
        self.assertEqual(validation["status"], "insufficient")
        self.assertIsNotNone(validation["pairwise_accuracy"])
        self.assertFalse(validation["beats_random"])

    def test_repeated_holdout_rounds_count_as_one_independent_group(self):
        catalog, source = learning_fixture(10)
        last_cards = ["card-8-bright", "card-8-dark"]
        rounds = [([f"card-{index}-bright", f"card-{index}-dark"], [f"card-{index}-bright"])
                  for index in range(8)] + [(last_cards, [last_cards[0]])] * 2
        validation = train([journal(rounds)], catalog=catalog)["validation"]
        self.assertEqual(validation["holdout_observation_count"], 2)
        self.assertEqual(validation["holdout_independent_group_count"], 1)
        self.assertEqual(validation["status"], "insufficient")
        self.assertFalse(validation["beats_random"])

    def test_no_choices_and_all_choices_do_not_fabricate_negative_labels(self):
        catalog = {"schema_version": "norn.features.v1", "feature_names": ["visual.one"],
                   "cards": {"a": [1.0], "b": [-1.0]}}
        source = journal([(["a", "b"], []), (["a", "b"], ["a", "b"])])
        checkpoint = train([source], catalog=catalog)
        self.assertEqual(checkpoint["training"]["observation_count"], 0)
        self.assertEqual(checkpoint["training"]["pair_count"], 0)
        self.assertEqual(checkpoint["weights"], [0.0])
        summary = summarize(checkpoint, catalog=catalog)
        self.assertEqual(summary["status"], "no_observations")
        self.assertEqual(summary["recommendations"], [])
        self.assertIsNone(summary["validation"]["pairwise_accuracy"])
        self.assertEqual(train([], catalog=catalog)["training"]["pair_count"], 0)

    def test_withdrawal_reselection_uses_actual_round_end_state(self):
        catalog = {"schema_version": "norn.features.v1", "feature_names": ["visual.one"],
                   "cards": {"a": [1.0], "b": [-1.0], "c": [0.0]}}
        source = journal([(["a", "b", "c"], ["a"])])
        events = source["events"]
        events[2:2] = [{"type": "card_deselected", "round": 1, "card_id": "a"},
                       {"type": "card_selected", "round": 1, "card_id": "b"},
                       {"type": "card_selected", "round": 1, "card_id": "a"}]
        for index, event in enumerate(events, 1):
            event.update(sequence=index, event_id=f"e{index}")
        row = observations_from_journals([source], catalog)["observations"][0]
        self.assertEqual(row["selected"], ["a", "b"])
        self.assertEqual(row["unselected"], ["c"])
        self.assertEqual(row["selection_event_ids"]["a"], "e5")

    def test_open_round_preview_is_explicitly_provisional(self):
        catalog, source = learning_fixture(1)
        source["events"] = source["events"][:-2]
        with self.assertRaises(ValueError):
            train([source], catalog=catalog)
        checkpoint = train([source], catalog=catalog, include_open=True)
        self.assertTrue(checkpoint["training"]["provisional"])
        row = observations_from_journals([source], catalog, include_open=True)["observations"][0]
        self.assertTrue(row["provisional"])
        self.assertIsNone(row["closing_event_id"])
        self.assertIsNone(checkpoint["validation"]["pairwise_accuracy"])

    def test_duplicate_journals_do_not_add_training_evidence(self):
        catalog, source = learning_fixture(12)
        once = train([source], catalog=catalog)
        twice = train([source, deepcopy(source)], catalog=catalog)
        self.assertEqual(twice["weights"], once["weights"])
        self.assertEqual(twice["training_hash"], once["training_hash"])
        self.assertEqual(twice["training"]["observation_count"], 12)
        self.assertEqual(twice["training"]["duplicates_ignored"], 1)

    def test_pair_sampling_and_training_are_bounded_and_deterministic(self):
        cards = {f"c{index}": [float(index)] for index in range(16)}
        catalog = {"schema_version": "norn.features.v1", "feature_names": ["visual.one"], "cards": cards}
        source = journal([(list(cards), list(cards)[:8])])
        first = train([source], catalog=catalog, config={"max_pairs_per_round": 3, "max_pairs": 3})
        second = train([deepcopy(source)], catalog=deepcopy(catalog), config={"max_pairs_per_round": 3, "max_pairs": 3})
        self.assertEqual(first, second)
        self.assertEqual(first["training"]["pair_count"], 3)
        self.assertEqual(first["training"]["possible_pair_count"], 64)
        self.assertEqual(first["training"]["omitted_pair_count"], 61)

    def test_round_sampling_is_stable_under_future_labels_clocks_and_completion(self):
        cards = {f"c{index}": [float(index)] for index in range(24)}
        catalog = {"schema_version": "norn.features.v1", "feature_names": ["visual.one"], "cards": cards}
        first_display, last_display = list(cards)[:16], list(cards)[16:]
        source = journal([(first_display, first_display[:8]), (last_display, last_display[:4])])
        changed_future = journal([(first_display, first_display[:8]), (last_display, last_display[4:])])
        changed_clock = deepcopy(source)
        next(event for event in changed_clock["events"] if event["type"] == "round_completed")["duration_ms"] = 54321
        prefix = deepcopy(source)
        first_end = next(index for index, event in enumerate(prefix["events"]) if event["type"] == "round_completed")
        prefix["events"] = prefix["events"][:first_end + 1]
        expected = None
        config = model._config({"max_pairs_per_round": 3})
        for document in (source, changed_future, changed_clock, prefix):
            row = observations_from_journals([document], catalog, include_open=True)["observations"][0]
            pairs, _ = model._pairs([row], config)
            current = [(positive, negative) for positive, negative, _ in pairs]
            if expected is None:
                expected = current
            self.assertEqual(current, expected)

    def test_additive_contributions_rebuild_every_score_and_topk_is_consistent(self):
        catalog, source = learning_fixture(20)
        checkpoint = train([source], catalog=catalog)
        predictions = rank(checkpoint, catalog=catalog)
        scores = predict_scores(checkpoint, catalog=catalog)
        self.assertEqual(scores, [{"card_id": row["card_id"], "score": row["score"]} for row in predictions])
        self.assertTrue(all(set(row) == {"card_id", "score"} for row in scores))
        self.assertEqual(rank(checkpoint, top_k=8, catalog=catalog), predictions[:8])
        for item in predictions:
            reconstructed = math.fsum(row["value"] * row["weight"] for row in item["contributions"])
            self.assertAlmostEqual(reconstructed, item["score"], places=14)
            self.assertEqual(item["score"], item["reconstructed_score"])
            self.assertNotIn("probability", item)
            self.assertTrue(all(row["label"] for row in item["contributions"]))

    def test_changed_features_sources_or_parameters_fail_verification(self):
        catalog, source = learning_fixture(10)
        checkpoint = train([source], catalog=catalog)
        self.assertTrue(verify_checkpoint(checkpoint, catalog, [source]))
        altered_catalog = deepcopy(catalog)
        altered_catalog["cards"]["card-0-bright"][0] = 0.5
        with self.assertRaises(ValueError):
            verify_checkpoint(checkpoint, altered_catalog)
        altered_source = deepcopy(source)
        next(event for event in altered_source["events"] if event["type"] == "round_completed")["duration_ms"] = 2000
        with self.assertRaises(ValueError):
            verify_checkpoint(checkpoint, catalog, [altered_source])
        changed = deepcopy(checkpoint)
        changed["weights"][0] += 1
        with self.assertRaises(ValueError):
            verify_checkpoint(changed, catalog)
        changed["checkpoint_hash"] = model._hash({key: value for key, value in changed.items() if key != "checkpoint_hash"})
        with self.assertRaises(ValueError):
            verify_checkpoint(changed, catalog, [source])

    def test_checkpoint_roundtrip_has_full_weights_and_refuses_overwrite(self):
        catalog, source = learning_fixture(10)
        checkpoint = train([source], catalog=catalog)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "personal.json"
            save_checkpoint(checkpoint, path, catalog=catalog)
            loaded = load_checkpoint(path, catalog=catalog, journals=[source])
            self.assertEqual(loaded, checkpoint)
            self.assertEqual(rank(loaded, top_k=3, catalog=catalog), rank(checkpoint, top_k=3, catalog=catalog))
            with self.assertRaises(FileExistsError):
                save_checkpoint(checkpoint, path, catalog=catalog)

    def test_feature_catalog_and_training_config_reject_invalid_data(self):
        catalog, source = learning_fixture(5)
        for feature in ("coords_avg", "E_I", "type", "card_id", "scene:unique"):
            bad = deepcopy(catalog)
            bad["feature_names"] = [feature]
            with self.assertRaises(ValueError):
                train([source], catalog=bad)
        for config in ({"epochs": 100000}, {"max_pairs": 0}, {"l2": 0}, {"unknown": True}):
            with self.assertRaises(ValueError):
                train([source], catalog=catalog, config=config)
        bad = deepcopy(catalog)
        bad["cards"]["card-0-bright"][0] = float("nan")
        with self.assertRaises(ValueError):
            train([source], catalog=bad)

    def test_shipped_catalog_is_sparse_cached_and_humanized_without_personality_labels(self):
        first = load_catalog()
        first["cards"]["NORN_0001"][0] = 999
        second = load_catalog()
        self.assertNotEqual(second["cards"]["NORN_0001"][0], 999)
        self.assertEqual(len(second["feature_names"]), 979)
        self.assertLessEqual(max(sum(value != 0 for value in row) for row in second["cards"].values()), 80)
        for name in second["feature_names"]:
            self.assertNotIn(name, ("coords", "type", "group", "scene", "card_id"))
            self.assertNotIn("pixel.", feature_label(name))
        self.assertIn("饱和度", feature_label("pixel.saturation.bin_5"))
        self.assertIn("画面特质", feature_label("trait.克制"))
        self.assertIn("画面意象", feature_label("motif.暖灯"))


if __name__ == "__main__":
    unittest.main()

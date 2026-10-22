"""Full-precision accounting, legacy-result compatibility, and tamper detection."""

from copy import deepcopy
import json
import math
import random
import unittest
from unittest.mock import patch

from norn.core import engine_module


engine = engine_module("fast_thinker")
ledger = engine_module("ledger")
DIMS = engine.DIMS


class ContributionLedgerTests(unittest.TestCase):
    def assertReconstructs(self, result):
        attribution = result["attribution"]
        self.assertTrue(ledger.verify_attribution(attribution, result["coords_avg"]))
        reconstructed = ledger.reconstruct_coords(attribution)
        for dim in DIMS:
            # Independently traverse entries, rather than trusting the stored
            # final coordinate or the helper under test.
            terms = [row["contribution"][dim] for row in attribution["selections"]]
            terms.extend(row["delta"][dim] for row in attribution["revision"]["events"])
            info = attribution["dimensions"][dim]
            terms.extend((info["summation_delta"], info["rounding_delta"]))
            self.assertAlmostEqual(math.fsum(terms), result["coords_avg"][dim], delta=1e-12)
            self.assertAlmostEqual(reconstructed[dim], result["coords_avg"][dim], delta=1e-12)

    def test_original_scoring_values_traits_and_signature_remain_unchanged(self):
        result = engine.fast_think(
            [{"id": "NORN_0001", "round": 1}, {"id": "NORN_0008", "round": 2}],
            3,
            [{"round": 1, "duration_ms": -100}, {"round": 2, "duration_ms": 99000}],
            [{"id": "NORN_0002", "round": 1}],
        )
        # Captured from the original scorer, before adding accounting.
        self.assertEqual(result["mbti_type"], "ENFP")
        self.assertEqual(result["coords_avg"], {"E_I": 1.76, "S_N": 8.0, "T_F": -1.76, "J_P": -2.29})
        self.assertEqual(result["confidence"], {
            "E_I": 0.17575249040547195, "S_N": 0.8,
            "T_F": 0.17575249040547195, "J_P": 0.22855249040547196,
        })
        self.assertEqual(result["top_traits"], ["灵感", "自由", "联结", "可能性", "热烈", "反复校准", "战略", "系统"])
        self.assertEqual(result["top_motifs"], ["灯笼", "人流", "小吃摊", "路口", "音乐形", "城市平面图", "红色标记点", "长桌"])
        self.assertEqual(result["signature_signal"], "灯笼")
        self.assertEqual(result["choice_revision"]["strength"], 0.22)
        self.assertReconstructs(result)

    def test_actual_weights_and_normalized_card_contributions_are_exposed(self):
        result = engine.fast_think(
            [{"id": "NORN_0001", "round": 1}, {"id": "NORN_0008", "round": 2}],
            2, [{"round": 1, "duration_ms": 2000}, {"round": 2, "duration_ms": 20000}],
            selection_event_ids=["pick-a", "pick-b"],
        )
        attribution = result["attribution"]
        self.assertEqual(attribution["schema_version"], "norn.attribution.v1")
        self.assertEqual(attribution["algorithm_version"], "norn.scoring.v1")
        self.assertEqual([row["event_id"] for row in attribution["selections"]], ["pick-a", "pick-b"])
        for index, row in enumerate(attribution["selections"], 1):
            self.assertEqual(row["base_weight"], math.exp(index / 2))
            self.assertEqual(row["hesitation_multiplier"], 1.0 if index == 1 else 1.12)
            self.assertEqual(row["weight"], row["base_weight"] * row["hesitation_multiplier"])
            self.assertEqual(row["normalized_weight"], row["weight"] / attribution["total_weight"])
            for dim in DIMS:
                self.assertEqual(row["weighted_coords"][dim], row["coords"][dim] * row["weight"])
                self.assertEqual(row["contribution"][dim], row["weighted_coords"][dim] / attribution["total_weight"])
                self.assertAlmostEqual(row["base_contribution"][dim] + row["hesitation_effect"][dim],
                                       row["contribution"][dim], delta=1e-12)
        self.assertReconstructs(result)

    def test_duration_clamp_equal_and_missing_explain_the_used_multiplier(self):
        selections = [{"id": "NORN_0001", "round": 1}, {"id": "NORN_0008", "round": 2}]
        cases = [
            ([], ["missing_duration"] * 3, [1.0] * 3),
            ([{"round": 1, "duration_ms": 1234}],
             ["insufficient_durations", "missing_duration", "missing_duration"], [1.0] * 3),
            ([{"round": 1, "duration_ms": 100}, {"round": 2, "duration_ms": 100}],
             ["equal_durations", "equal_durations", "missing_duration"], [1.0] * 3),
            ([{"round": 1, "duration_ms": -1}, {"round": 2, "duration_ms": 100000}],
             ["session_min_max", "session_min_max", "missing_duration"], [1.0, 1.12, 1.0]),
        ]
        for durations, reasons, multipliers in cases:
            with self.subTest(durations=durations):
                result = engine.fast_think(selections, 3, durations)
                rounds = result["attribution"]["rounds"]
                self.assertEqual([row["normalization"] for row in rounds], reasons)
                self.assertEqual([row["hesitation_multiplier"] for row in rounds], multipliers)
                self.assertReconstructs(result)
        rounds = result["attribution"]["rounds"]
        self.assertEqual([row["clamped_duration_ms"] for row in rounds], [0, 45000, None])
        self.assertEqual([row["duration_was_clamped"] for row in rounds], [True, True, False])
        self.assertEqual([row["normalized_duration"] for row in rounds], [0.0, 1.0, None])

    def test_revision_uses_existing_rounded_strength_and_allocates_exact_session_nudge(self):
        result = engine.fast_think(
            [{"id": "NORN_0001", "round": 1}], 10,
            deselection_events=[{"id": "NORN_0002", "round": 1}],
            revision_event_ids=["undo-a"],
        )
        revision = result["attribution"]["revision"]
        self.assertEqual(revision["strength"], 0.07)  # Existing round(1 / 10 / 1.5, 2).
        self.assertEqual(revision["total_jp_delta"], -0.07 * 2.4)
        self.assertEqual(revision["events"][0]["event_id"], "undo-a")
        self.assertEqual(revision["events"][0]["delta"]["J_P"], -0.168)
        self.assertEqual(result["coords_avg"]["J_P"], 7.83)
        self.assertIn("not psychological causality", revision["allocation_note"])
        self.assertReconstructs(result)

    def test_revision_saturation_and_all_event_shares_reconstruct(self):
        for count in (3, 31, 128):
            with self.subTest(count=count):
                result = engine.fast_think(
                    [{"id": "NORN_0001", "round": 1}], 2,
                    deselection_events=[{"id": "NORN_0002", "round": 1}] * count,
                )
                revision = result["attribution"]["revision"]
                self.assertEqual(revision["strength"], 1.0)
                self.assertEqual(revision["total_jp_delta"], -2.4)
                self.assertAlmostEqual(math.fsum(row["delta"]["J_P"] for row in revision["events"]), -2.4, delta=1e-12)
                self.assertReconstructs(result)

    def test_empty_and_revision_only_sessions_have_zero_contributions(self):
        for revisions in ([], [{"id": "NORN_0002", "round": 1}]):
            with self.subTest(revisions=revisions):
                result = engine.fast_think([], 3, deselection_events=revisions)
                self.assertEqual(result["mbti_type"], "XXXX")
                self.assertEqual(result["attribution"]["selections"], [])
                self.assertEqual(result["attribution"]["revision"]["total_jp_delta"], 0)
                self.assertFalse(result["attribution"]["revision"]["applied"])
                self.assertEqual(result["attribution"]["revision"]["ignored_reason"], "no_valid_selections")
                self.assertReconstructs(result)

    def test_cancellation_and_near_zero_type_use_unrounded_coordinates(self):
        cards = {
            "positive": {"coords": dict.fromkeys(DIMS, 8), "traits": [], "motifs": [], "scene": "a", "rationale": "a"},
            "negative": {"coords": dict.fromkeys(DIMS, -8), "traits": [], "motifs": [], "scene": "b", "rationale": "b"},
        }
        selections = [{"id": "positive", "round": 1}, {"id": "negative", "round": 1}]
        with patch.object(engine, "_CARDS", cards):
            result = engine.fast_think(selections, 1)
            self.assertEqual(result["coords_avg"], dict.fromkeys(DIMS, 0.0))
            self.assertEqual(result["mbti_type"], "ENTJ")
            self.assertReconstructs(result)
            cards["negative"]["coords"]["E_I"] = -8.000000000001
            near_zero = engine.fast_think(selections, 1)
            self.assertEqual(near_zero["coords_avg"]["E_I"], 0)
            self.assertLess(near_zero["attribution"]["dimensions"]["E_I"]["unrounded_final"], 0)
            self.assertEqual(near_zero["mbti_type"], "INTJ")
            self.assertReconstructs(near_zero)

    def test_all_256_cards_multiple_rounds_and_json_roundtrip(self):
        result = engine.fast_think(
            [{"id": f"NORN_{i:04}", "round": (i - 1) % 16 + 1} for i in range(1, 257)],
            16, [{"round": i, "duration_ms": i * 1200} for i in range(1, 17)],
            [{"id": "NORN_0001", "round": 1}] * 30,
        )
        self.assertEqual(len(result["attribution"]["selections"]), 256)
        self.assertEqual(result["coords_avg"], {"E_I": 0.56, "S_N": -2.18, "T_F": -0.58, "J_P": -3.21})
        self.assertEqual(result["mbti_type"], "ESFP")
        self.assertEqual(result["signature_signal"], "人群")
        self.assertReconstructs(json.loads(json.dumps(result, allow_nan=False)))
        for dim in DIMS:
            info = result["attribution"]["dimensions"][dim]
            self.assertLess(abs(info["summation_delta"]), 1e-12)
            self.assertEqual(info["rounding_delta"], info["final"] - info["unrounded_final"])

    def test_varied_sessions_reconstruct_without_rounding_individual_entries(self):
        rng = random.Random(1701)
        for _ in range(70):
            rounds = rng.randint(1, 32)
            selections = [{"id": f"NORN_{rng.randint(1, 256):04}", "round": rng.randint(1, rounds)}
                          for _ in range(rng.randint(1, 256))]
            durations = [{"round": i, "duration_ms": rng.randint(-1000, 90000)}
                         for i in range(1, rounds + 1) if rng.choice((True, False))]
            revisions = [{"id": "NORN_0001", "round": rng.randint(1, rounds)}
                         for _ in range(rng.randint(0, 20))]
            result = engine.fast_think(selections, rounds, durations, revisions)
            self.assertReconstructs(result)
        self.assertTrue(any(row["contribution"]["E_I"] != round(row["contribution"]["E_I"], 2)
                            for row in result["attribution"]["selections"]))

    def test_stable_snapshot_ids_and_legacy_invalid_card_compatibility(self):
        selections = [{"id": "not-a-card", "round": 1}, {"id": "NORN_0001", "round": 1}]
        revisions = [None, {"id": "anything", "round": 0}, {"id": "legacy-unknown-card", "round": 999}]
        first = engine.fast_think(selections, 3, deselection_events=revisions)
        second = engine.fast_think(deepcopy(selections), 3, deselection_events=deepcopy(revisions))
        self.assertEqual(first, second)
        self.assertEqual(len(first["attribution"]["selections"]), 1)
        self.assertTrue(first["attribution"]["selections"][0]["event_id"].startswith("legacy.selection.2."))
        self.assertEqual(first["choice_revision"]["total"], 1)
        self.assertTrue(first["attribution"]["revision"]["events"][0]["event_id"].startswith("legacy.revision.3."))
        self.assertReconstructs(first)

    def test_supplied_ids_match_original_inputs_and_are_not_mutated(self):
        ids = ["invalid-card", "valid-card"]
        result = engine.fast_think(
            [{"id": "unknown", "round": 1}, {"id": "NORN_0001", "round": 1}], 1,
            selection_event_ids=ids,
        )
        self.assertEqual(result["attribution"]["selections"][0]["event_id"], "valid-card")
        self.assertEqual(ids, ["invalid-card", "valid-card"])
        for invalid in ([], ["duplicate", "duplicate"], ["a", ""], "not-a-list"):
            with self.subTest(ids=invalid), self.assertRaises(ValueError):
                engine.fast_think([{"id": "NORN_0001", "round": 1}] * 2, 1, selection_event_ids=invalid)
        with self.assertRaises(ValueError):
            engine.fast_think(
                [{"id": "NORN_0001", "round": 1}], 1,
                deselection_events=[{"id": "NORN_0002", "round": 1}],
                selection_event_ids=["same-event"], revision_event_ids=["same-event"],
            )

    def test_reconstructor_does_not_trust_final_and_verifier_detects_tampering(self):
        result = engine.fast_think([{"id": "NORN_0001", "round": 1}], 1)
        attribution = result["attribution"]
        wrong_final = deepcopy(attribution)
        wrong_final["dimensions"]["E_I"]["final"] = 999
        self.assertEqual(ledger.reconstruct_coords(wrong_final), result["coords_avg"])
        with self.assertRaises(ValueError):
            ledger.verify_attribution(wrong_final, result["coords_avg"])
        for field in ("contribution", "weighted_coords", "base_contribution", "hesitation_effect"):
            altered = deepcopy(attribution)
            altered["selections"][0][field]["E_I"] += 0.01
            with self.subTest(field=field), self.assertRaises(ValueError):
                ledger.verify_attribution(altered, result["coords_avg"])
        altered = deepcopy(attribution)
        altered["dimensions"]["E_I"]["summation_delta"] += 0.01
        with self.assertRaises(ValueError):
            ledger.verify_attribution(altered, result["coords_avg"])
        with self.assertRaises(ValueError):
            ledger.verify_attribution(attribution, {**result["coords_avg"], "J_P": 999})

    def test_revision_entries_cannot_hide_opposite_tampering_in_the_session_total(self):
        result = engine.fast_think(
            [{"id": "NORN_0001", "round": 1}], 3,
            deselection_events=[{"id": "NORN_0002", "round": 1}, {"id": "NORN_0003", "round": 2}],
        )
        for dim in DIMS:
            altered = deepcopy(result["attribution"])
            altered["revision"]["events"][0]["delta"][dim] += 0.25
            altered["revision"]["events"][1]["delta"][dim] -= 0.25
            self.assertEqual(ledger.reconstruct_coords(altered), ledger.reconstruct_coords(result["attribution"]))
            with self.subTest(dim=dim), self.assertRaises(ValueError):
                ledger.verify_attribution(altered, result["coords_avg"])

    def test_malformed_nonfinite_and_unsupported_ledger_are_rejected(self):
        result = engine.fast_think([{"id": "NORN_0001", "round": 1}], 1)
        for value in (float("nan"), float("inf"), True, "8"):
            attribution = deepcopy(result["attribution"])
            attribution["selections"][0]["contribution"]["E_I"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                ledger.reconstruct_coords(attribution)
        for attribution in ({}, None, {**result["attribution"], "schema_version": "future"}):
            with self.subTest(attribution=attribution), self.assertRaises(ValueError):
                ledger.verify_attribution(attribution, result["coords_avg"])


if __name__ == "__main__":
    unittest.main()

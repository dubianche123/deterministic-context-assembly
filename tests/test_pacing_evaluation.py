"""Descriptive pacing reports must preserve denominators and group holdouts."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
import math
from pathlib import Path
import tempfile
import unittest

from norn.collection import build_partial, build_record
from norn.finale import create, finish
from norn.pacing import trace
from norn.research_inspection import build_bundle
from scripts.evaluate_pacing import (DEFAULTS, candidate, describe, evaluate, load_records,
                                     main, split_for)
from tests.test_last_card_archive import PRODUCT
from tests.test_partial_records import prefix
from tests.test_research_deck import DECK, research, session


class PacingEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.journal = session(research.RANDOM, rounds=3, keep=lambda _: False)
        cls.bundle = build_bundle(cls.journal, DECK)
        cls.private = create(cls.journal, DECK, "evaluation-fixture")
        cls.finale = finish(cls.private, cls.private["candidate_ids"][0], 1000)

    def row(self, number=1, visitor=None, *, partial=False, with_finale=True):
        fields = {"visitor": visitor or f"visitor-private-marker-{number}",
                  "deployment": "cloudflare", "product": PRODUCT}
        journal = self.journal if with_finale else prefix(self.journal, 1)
        if partial:
            result = build_partial(journal, session=f"{number:032x}", pacing=trace(journal),
                                   finale=self.finale if with_finale else None, **fields)
        else:
            result = build_record(self.bundle, {"text": "PRIVATE_READING_MUST_NOT_APPEAR", "simulated": True},
                                  pacing=trace(self.journal), finale=self.finale if with_finale else None, **fields)
        result["record_id"] = f"{number:032x}"
        return result

    def test_empty_or_only_self_selected_exits_never_produce_a_trained_policy(self):
        for rows in ([], [self.row(1, partial=True, with_finale=False)]):
            report = evaluate(rows)
            self.assertFalse(report["candidate_policy"]["trained"])
            self.assertEqual(report["candidate_policy"]["training_rounds"], [])
            self.assertEqual(report["model_calls"], 0)
            self.assertEqual(report["candidate_policy"]["active_policy_unchanged"], "norn.pacing.v1")

    def test_missing_finale_and_missing_jev_are_not_scored_as_incorrect(self):
        one = self.row(1)
        pending = self.row(2)
        pending["finale"]["jev"] = {"status": "pending", "provider_calls": 0}
        missing = self.row(3, partial=True, with_finale=False)
        report = evaluate([one, pending, missing])
        self.assertEqual(report["counts"]["valid_product_visits"], 3)
        self.assertEqual(report["counts"]["valid_finales"], 2)
        self.assertAlmostEqual(report["counts"]["no_finale_fraction"], 1 / 3)
        by_round = {row["exploration_rounds"]: row for row in report["by_actual_exploration_rounds"]}
        self.assertEqual(by_round[3]["all_valid_finales"]["local"]["n"], 2)
        self.assertEqual(by_round[3]["jev_common_coverage"]["n"], 0)
        self.assertIsNone(by_round[3]["jev_common_coverage"]["lanes"]["jev"]["hit_rate"])
        self.assertEqual(by_round[1]["partial_without_finale"], 1)

    def test_known_nll_hits_gains_and_common_coverage_use_exactly_their_own_rows(self):
        # Calculation fixtures use controlled probabilities, not a claimed
        # model result. Whole-record replay is tested separately below.
        a = {"candidate_ids": ["a", "b", "c", "d"], "chosen_card_id": "a", "predicted_card_id": "a",
             "probabilities": [.5, .2, .2, .1], "baseline_probabilities": [.25] * 4,
             "jev": {"status": "off"}}
        b = {**deepcopy(a), "predicted_card_id": "b", "probabilities": [.1, .6, .2, .1],
             "jev": {"status": "ok", "predicted_card_id": "a", "probabilities": [.8, .1, .05, .05]}}
        visits = [{"rounds": 3, "started_rounds": 3, "group": str(i), "partial": False,
                   "finale": f, "finale_state": "valid", "pacing": {"decisions": []}}
                  for i, f in enumerate((a, b))]
        [row] = describe(visits)["by_actual_exploration_rounds"]
        self.assertAlmostEqual(row["all_valid_finales"]["baseline"]["mean_nll"], math.log(4))
        self.assertAlmostEqual(row["all_valid_finales"]["local"]["mean_nll"], (-math.log(.5) - math.log(.1)) / 2)
        self.assertEqual(row["all_valid_finales"]["local"]["hit_rate"], .5)
        self.assertEqual(row["jev_common_coverage"]["n"], 1)
        self.assertAlmostEqual(row["jev_common_coverage"]["lanes"]["local"]["mean_nll"], -math.log(.1))
        self.assertAlmostEqual(row["jev_common_coverage"]["lanes"]["jev"]["mean_nll"], -math.log(.8))
        self.assertAlmostEqual(row["local_gain_over_baseline"]["mean_nll_gain"],
                               math.log(4) - (-math.log(.5) - math.log(.1)) / 2)
        self.assertEqual(row["local_gain_over_baseline"]["paired_n"], 2)
        self.assertEqual(row["local_gain_over_baseline"]["hit_rate_gain"], -.5)
        self.assertEqual(row["jev_common_coverage"]["local_gain_over_baseline"]["hit_rate_gain"], -1)
        zero = deepcopy(visits[:1])
        zero[0]["finale"]["probabilities"] = [0., .5, .4, .1]
        metric = describe(zero)["by_actual_exploration_rounds"][0]["all_valid_finales"]["local"]
        self.assertEqual((metric["nll_status"], metric["infinite_nll_count"]), ("infinite", 1))
        self.assertIsNone(metric["mean_nll"])

    def test_rebuild_failure_and_invalid_finale_have_separate_exclusion_semantics(self):
        bad_trace = self.row(1)
        bad_trace["pacing"]["decisions"][0]["offer_probability"] = .123
        invalid = self.row(2)
        invalid["finale"]["hit"] = not invalid["finale"]["hit"]
        report = evaluate([bad_trace, invalid])
        self.assertEqual(report["excluded"]["unreconstructible_pacing"], 1)
        self.assertEqual(report["counts"]["valid_product_visits"], 1)
        self.assertEqual(report["counts"]["invalid_finales"], 1)
        self.assertEqual(report["counts"]["valid_finales"], 0)
        self.assertEqual(report["counts"]["no_finale_at_export"], 0)

    def test_identical_evaluation_facts_deduplicate_but_conflicting_snapshots_fail_closed(self):
        one = self.row(1)
        same_facts = {**deepcopy(one), "reading": "A different annotation is not a new visit"}
        report = evaluate([one, same_facts])
        self.assertEqual(report["counts"]["valid_product_visits"], 1)
        self.assertEqual(report["excluded"]["identical_duplicate"], 1)
        conflict = deepcopy(one)
        conflict["finale"] = finish(self.private, self.private["candidate_ids"][1], 1100)
        report = evaluate([one, conflict])
        self.assertEqual(report["counts"]["valid_product_visits"], 0)
        self.assertEqual(report["excluded"]["conflicting_record_id"], 1)

    def test_participant_mapping_keeps_all_visitor_visits_in_one_split_without_outputting_ids(self):
        rows = [self.row(1, "visitor-private-one"), self.row(2, "visitor-private-two"),
                self.row(3, "visitor-private-one")]
        rows[0]["participant_id"] = rows[1]["participant_id"] = "participant-private-marker"
        report = evaluate(rows)
        splits = report["group_split"]
        self.assertEqual(splits["train_groups"] + splits["test_groups"], 1)
        self.assertFalse(splits["same_group_crosses_split"])
        for secret in ("visitor-private-one", "visitor-private-two", "participant-private-marker",
                       "PRIVATE_READING_MUST_NOT_APPEAR"):
            self.assertNotIn(secret, json.dumps(report))
        rows[2]["participant_id"] = "contradictory-participant"
        report = evaluate(rows)
        self.assertEqual(report["excluded"]["conflicting_participant_mapping"], 2)
        self.assertEqual(split_for("same-person", "fixed-seed", .25), split_for("same-person", "fixed-seed", .25))

    def test_decision_behavior_distinguishes_followed_by_finale_continued_and_censored(self):
        decisions = [{"round": 3, "action": "offer_finale", "offer_probability": .25}]
        visits = [{"rounds": rounds, "started_rounds": started, "group": str(i), "partial": state != "valid",
                   "finale": None, "finale_state": state, "pacing": {"decisions": decisions}}
                  for i, (rounds, started, state) in enumerate(((3, 3, "valid"), (4, 4, "missing"), (3, 3, "missing")))]
        # The report's terminal-scoring portion is irrelevant for this isolated
        # decision audit; use a minimally valid scoring record for the first.
        visits[0]["finale"] = self.finale
        [row] = describe(visits)["decision_behavior"]
        self.assertEqual(row["after_offer"], {"finale_at_this_round": 1, "continued": 1, "unknown_at_export": 1})
        self.assertEqual(row["propensity_counts"], {"0.25": 3})

    def test_cli_reads_export_formats_and_writes_only_aggregate_report_and_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            rows = [self.row(1), self.row(2, partial=True, with_finale=False)]
            for name, data in (("array.json", json.dumps(rows)), ("envelope.json", json.dumps({"records": rows})),
                               ("export.jsonl", "\n".join(json.dumps(r) for r in rows))):
                source = folder / name
                source.write_text(data, encoding="utf-8")
                loaded, digest = load_records(source)
                self.assertEqual(loaded, rows)
                self.assertEqual(len(digest), 64)
            output, policy = folder / "summary.json", folder / "policy.json"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(["--input", str(source), "--output", str(output), "--policy-output", str(policy)])
            self.assertEqual(code, 0)
            written = output.read_text() + policy.read_text() + stdout.getvalue()
            self.assertNotIn("PRIVATE_READING_MUST_NOT_APPEAR", written)
            self.assertNotIn(rows[0]["visitor_id"], written)
            self.assertEqual(json.loads(output.read_text())["model_calls"], 0)
            self.assertFalse(json.loads(policy.read_text())["trained"])
            self.assertEqual(source.read_text(), data)
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                self.assertEqual(main(["--input", str(source), "--output", str(source)]), 2)
            self.assertEqual(source.read_text(), data)

    def test_malformed_journal_is_counted_without_crashing_or_disclosing_content(self):
        bad = self.row(1)
        bad["journal"] = None
        report = evaluate([bad])
        self.assertEqual(report["counts"]["valid_product_visits"], 0)
        self.assertEqual(report["excluded"]["invalid_journal_or_deck"], 1)
        self.assertNotIn("PRIVATE_READING_MUST_NOT_APPEAR", json.dumps(report))

    def test_invalid_options_or_nonfinite_json_do_not_write_a_report(self):
        for options in ({"test_fraction": True}, {"test_fraction": float("nan")},
                        {"min_train_groups": False}, {"round_cost": float("inf")},
                        {"typo": "PRIVATE_MARKER"}):
            with self.subTest(options=list(options)):
                with self.assertRaises(ValueError):
                    evaluate([], **options)
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "bad.json", Path(directory) / "out.json"
            source.write_text('[{"record_id":"PRIVATE_MARKER","score":NaN}]', encoding="utf-8")
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                self.assertEqual(main(["--input", str(source), "--output", str(output)]), 2)
            self.assertFalse(output.exists())
            self.assertNotIn("PRIVATE_MARKER", stderr.getvalue())


def synthetic_visit(group, split, number, gain):
    """Known counterfactual forecasts for candidate-fitting unit tests only."""
    p = .25 * math.exp(gain)
    return {"group": group, "split": split, "rounds": number, "finale_state": "valid",
            "finale": {"candidate_ids": ["a", "b", "c", "d"], "chosen_card_id": "a", "predicted_card_id": "a",
                       "probabilities": [p] + [(1 - p) / 3] * 3, "baseline_probabilities": [.25] * 4}}


class PacingCandidateTests(unittest.TestCase):
    def options(self):
        return {**DEFAULTS, "min_train_groups": 2, "min_test_groups": 2, "min_groups_per_round": 1,
                "shrinkage_groups": 0., "round_cost": .05}

    def visits(self):
        return [synthetic_visit(group, split, number, gain)
                for split, groups in (("train", ("t1", "t2")), ("test", ("v1", "v2")))
                for group in groups for number, gain in ((3, .6), (6, .7))]

    def test_candidate_uses_train_only_and_test_only_validates_the_selected_rounds(self):
        rows = self.visits()
        result = candidate(rows, self.options())
        self.assertTrue(result["trained"])
        self.assertEqual(result["recommended_interval"], [3, 3])
        changed = [synthetic_visit(v["group"], "test", v["rounds"], -.5) if v["split"] == "test" else v for v in rows]
        rejected = candidate(changed, self.options())
        self.assertEqual(rejected["training_rounds"], result["training_rounds"])
        self.assertEqual(rejected["recommended_interval"], result["recommended_interval"])
        self.assertFalse(rejected["trained"])
        self.assertIn("heldout_product_utility_not_positive", rejected["refusal_reasons"])

    def test_repeated_sessions_do_not_outweigh_another_group_and_gaps_are_not_filled(self):
        rows = self.visits()
        reference = candidate(rows, self.options())
        rows += [synthetic_visit("t1", "train", 3, .6)] * 100
        result = candidate(rows, self.options())
        self.assertEqual(result["training_rounds"], reference["training_rounds"])
        generous = self.options()
        generous["utility_tolerance"] = 1
        self.assertEqual(candidate(rows, generous)["recommended_interval"], [3, 3])

    def test_insufficient_independent_groups_round_variation_or_same_round_test_refuses_training(self):
        rows = self.visits()
        for sample in (rows[:1], [v for v in rows if v["rounds"] == 3],
                       [v for v in rows if v["split"] == "train" or v["rounds"] == 6]):
            with self.subTest(size=len(sample)):
                result = candidate(sample, self.options())
                self.assertFalse(result["trained"])
                self.assertTrue(result["refusal_reasons"])
                self.assertFalse(result["automatic_deployment"])

    def test_zero_probability_cannot_disappear_from_candidate_training(self):
        rows = self.visits()
        invalid_for_finite_fit = synthetic_visit("another-group", "train", 3, .1)
        invalid_for_finite_fit["finale"]["probabilities"] = [0., .5, .4, .1]
        rows.append(invalid_for_finite_fit)
        result = candidate(rows, self.options())
        self.assertFalse(result["trained"])
        self.assertEqual(result["nonfinite_paired_finales"], 1)
        self.assertIn("nonfinite_paired_finales_need_a_predefined_training_rule", result["refusal_reasons"])


if __name__ == "__main__":
    unittest.main()

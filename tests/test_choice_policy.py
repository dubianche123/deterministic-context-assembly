"""Conservative policy learns only from past rounds and keeps old numerics."""

from copy import deepcopy
import math
import unittest
from unittest.mock import patch

from norn.core import engine_module

base = engine_module("choice_model")
policy = engine_module("choice_policy")
numeric = engine_module("numeric")


def row(number, picks=None, *, stopped=True, shown=None):
    return {"round": number, "shown": list(shown or ["NORN_0001", "NORN_0009"]),
            "picks": list(picks or []), "stopped": stopped, "duration_ms": 3000}


class ConservativeChoiceTests(unittest.TestCase):
    def setUp(self):
        self.catalog = base.load_catalog()

    def test_prior_is_dimension_history_rule_and_does_not_mutate_v4(self):
        self.assertEqual([policy.weight_precision(n) for n in (0, 1, 4, 8, 32)],
                         [19.5, 19.5, 6, 3.75, 2.0625])
        rows = [row(i, ["NORN_0001"]) for i in range(1, 5)]
        prepared = base.Prepared(rows, self.catalog)
        before = base.fit(prepared)
        original_prior = base.PRIOR
        state = policy.fit_policy(rows)
        self.assertEqual(base.PRIOR, original_prior)
        self.assertEqual(base.fit(prepared), before)
        self.assertLess(math.fsum(w * w for w in state["theta"][:12]),
                        math.fsum(w * w for w in before[0][:12]))
        gradient, hessian = base._evaluate(state["theta"], prepared, len(rows))
        for k in range(12):
            gradient[k] -= (6 - base.PRIOR[k]) * state["theta"][k]
        self.assertLess(max(map(abs, gradient)), 1e-8)

    def test_evidence_freezes_candidate_within_round_and_excludes_stop(self):
        rows = [row(1, ["NORN_0001", "NORN_0009"]), row(2, ["NORN_0001"])]
        theta = [0.0] * 13
        theta[1] = 1
        covariance = [[float(a == b) for b in range(13)] for a in range(13)]

        def fitted(prepared, count, precision, start=None):
            return ([0.0] * 13 if count == 0 else list(theta)), deepcopy(covariance), 1

        with patch.object(policy, "_fit", side_effect=fitted) as fit:
            states = policy.path(rows)
        self.assertEqual(fit.call_count, 3)
        self.assertEqual(states[1]["guard"]["log_evalue"], 0)
        expected = math.log(2 * math.exp(1) / (math.exp(1) + math.exp(-1)))
        self.assertAlmostEqual(states[2]["guard"]["log_evalue"], expected, delta=1e-14)
        self.assertEqual(states[2]["guard"]["evidence_card_choices"], 2)

    def test_current_evidence_can_close_gate_after_counterevidence(self):
        rows = [row(i, ["NORN_0001"]) for i in range(1, 41)]
        states = policy.path(rows)
        self.assertEqual(states[-1]["guard"]["status"], "active")
        rows += [row(i, ["NORN_0009"]) for i in range(41, 81)]
        state = policy.fit_policy(rows)
        self.assertLess(state["guard"]["log_evalue"], numeric.log(20))
        self.assertEqual(state["guard"]["status"], "baseline")
        self.assertEqual(state["effective_theta"][:12], [0.0] * 12)

    def test_exact_fallback_empty_stop_and_forced_choices(self):
        rows = [row(1), row(2, ["NORN_0001"], shown=["NORN_0001"]),
                {"round": 3, "shown": [], "picks": [], "stopped": True, "duration_ms": 3000}]
        state = policy.fit_policy(rows)
        self.assertEqual(state["guard"]["training_rounds"], 2)
        self.assertEqual(state["guard"]["evidence_card_choices"], 0)
        self.assertEqual(state["guard"]["log_evalue"], 0)
        self.assertEqual(state["effective_theta"], [0.0] * 12 + [base._fit_stop_only(base.Prepared(rows, self.catalog))])
        summary = policy.summarize(rows)
        self.assertEqual(summary["recommendations"], [])
        self.assertEqual(summary["top_features"], [])
        self.assertEqual(sum(x["p"] for x in summary["type_probabilities"]), 1)
        self.assertEqual(policy.trajectory(rows)["states"][-1]["top"], [])
        self.assertEqual(policy.fit_policy([])["effective_theta"], [0.0] * 13)

    def test_open_labels_do_not_enter_evidence_or_next_completed_candidate(self):
        closed = [row(1, ["NORN_0001"]), row(2, ["NORN_0001"])]
        opened = closed + [row(3, ["NORN_0009"], stopped=False)]
        before = policy.fit_policy(closed)
        after = policy.fit_policy(opened)
        self.assertEqual(after["guard"], before["guard"])
        self.assertEqual(policy.path(opened), policy.path(closed))
        self.assertNotEqual(after["training_hash"], before["training_hash"])
        with self.assertRaises(ValueError):
            policy.fit_policy(opened + [row(4)])

    def test_log_evidence_has_no_probability_floor_or_negative_reset(self):
        theta = [0.0] * 13
        theta[1] = 1000
        gain, count = policy._content_gain(theta, row(1, ["NORN_0009"]), self.catalog)
        self.assertEqual(count, 1)
        self.assertAlmostEqual(gain, math.log(2) - 2000, delta=1e-10)
        states = policy.path([row(i, ["NORN_0001" if i % 2 else "NORN_0009"]) for i in range(1, 9)])
        self.assertLess(states[-1]["guard"]["log_evalue"], 0)

    def test_effective_contributions_reconstruct_and_covariance_is_candidate(self):
        for rows in ([row(1, ["NORN_0001"])], [row(i, ["NORN_0001"]) for i in range(1, 41)]):
            summary = policy.summarize(rows, selected=["NORN_0001"])
            self.assertTrue(base.verify_contributions(summary["contributions"]))
            self.assertEqual(summary["contributions"]["weights"], summary["effective"]["theta"][:12])
            self.assertIn("Candidate", summary["effective"]["uncertainty"])
            self.assertGreater(summary["posterior"]["sd"][0], 0)

    def test_no_outer_test_influence_and_native_portable_agree(self):
        training = [row(i, ["NORN_0001"]) for i in range(1, 9)]
        frozen = deepcopy(training)
        outputs = []
        for contract in (numeric.NATIVE_CONTRACT, numeric.PORTABLE_CONTRACT):
            with numeric.use(contract):
                outputs.append(policy.fit_policy(training))
        self.assertEqual(training, frozen)
        self.assertEqual(outputs[0]["training_hash"], outputs[1]["training_hash"])
        for key in ("theta", "effective_theta"):
            for a, b in zip(outputs[0][key], outputs[1][key]):
                self.assertAlmostEqual(a, b, delta=1e-12)
        # validate must train/gate only its chronological training prefix.
        all_rows = training + [row(9, ["NORN_0009"]), row(10)]
        expected = policy.fit_policy(all_rows[:8])["guard"]
        self.assertEqual(policy.validate(all_rows)["training_guard"], expected)


if __name__ == "__main__":
    unittest.main()

"""Held-out model comparison (norn/evaluate.py): agreement with the shipped model and honest verdicts."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    import numpy  # noqa: F401
except ImportError:  # pragma: no cover - numpy is optional
    numpy = None

from norn.core import engine_module
from norn.simulate import simulate_records


class DecisionBoundaryTests(unittest.TestCase):
    @staticmethod
    def summary():
        from norn.evaluate import METRICS, MODELS
        models = {}
        for name in MODELS:
            models[name] = {metric: {"per_choice": {"value": 0.0, "ci95": [-0.1, 0.1]},
                                    "minus_hybrid": {"value": 0.0, "ci95": [-0.1, 0.1]}}
                            for metric in METRICS}
            models[name]["all"]["minus_stop_only"] = {"value": 0.0, "ci95": [-0.1, 0.1]}
        models["pixel"]["all"]["minus_stop_only"] = {"value": -0.2, "ci95": [-0.3, -0.1]}
        return {"visitors": 100, "records": 100, "models": models}

    def test_missing_within_type_evidence_cannot_approve_a_candidate(self):
        from norn.evaluate import decide
        summary = self.summary()
        pixel = summary["models"]["pixel"]
        pixel["all"]["minus_hybrid"] = {"value": -0.2, "ci95": [-0.3, -0.1]}
        pixel["within_type"]["minus_hybrid"] = {"value": None, "ci95": None}
        decision = decide(summary)
        self.assertEqual(decision["verdict"], "useful_candidates_inconclusive")
        self.assertEqual(decision["selection_blockers"], {"pixel": ["within_type_unavailable"]})
        # The same evidence is enough to keep a useful current model, but not
        # enough to replace it with a candidate whose within-type test is absent.
        summary["models"]["hybrid"]["all"]["minus_stop_only"] = {"value": -0.2, "ci95": [-0.3, -0.1]}
        self.assertEqual(decide(summary)["verdict"], "keep_hybrid")

    def test_a_useful_candidate_is_not_reported_as_no_picture_signal(self):
        from norn.evaluate import decide, format_evaluation
        summary = self.summary()
        summary["models"]["pixel"]["within_type"]["minus_hybrid"] = {"value": 0.0, "ci95": [-0.01, 0.01]}
        decision = decide(summary)
        self.assertEqual(decision["verdict"], "useful_candidates_inconclusive")
        self.assertEqual(decision["beat_stop_only"], ["pixel"])
        self.assertEqual(decision["selection_blockers"], {"pixel": ["not_better_than_hybrid"]})
        text = format_evaluation({"held_out": summary, "skipped_records": 0, "decision": decision})
        self.assertIn("beat stop-only: pixel", text)
        self.assertIn("selection pending pixel: not_better_than_hybrid", text)
        self.assertNotIn("no_picture_model_beats_stop_only", text)

    def test_observed_within_type_bound_must_be_strictly_below_the_declared_margin(self):
        from norn.evaluate import decide
        summary = self.summary()
        pixel = summary["models"]["pixel"]
        pixel["all"]["minus_hybrid"] = {"value": -0.2, "ci95": [-0.3, -0.1]}
        pixel["within_type"]["minus_hybrid"] = {"value": 0.0, "ci95": [-0.01, 0.02]}
        self.assertEqual(decide(summary)["verdict"], "useful_candidates_inconclusive")
        pixel["within_type"]["minus_hybrid"]["ci95"][1] = 0.019
        self.assertEqual(decide(summary)["verdict"], "candidate_better")


@unittest.skipUnless(numpy, "norn records evaluate needs the optional numpy")
class EvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from norn.evaluate import feature_sets
        cls.model = engine_module("choice_model")
        cls.catalog, cls.features, _ = feature_sets()
        kinds = sorted(set(cls.catalog.types))
        cls.types = numpy.array([kinds.index(kind) for kind in cls.catalog.types])

    def rows(self, seed, **settings):
        record = simulate_records(1, visits=(1, 1), rounds=(8, 14), seed=seed, **settings)[0]
        return self.model.observations(record["journal"], self.catalog)

    def test_the_current_model_is_scoring_v4_and_its_numbers_are_the_profiles(self):
        from norn.evaluate import Steps, evaluate_rows, fit
        for seed in range(4):
            rows = self.rows(seed)
            theta, _, _ = self.model.fit(self.model.Prepared(rows, self.catalog))
            mine = fit(Steps(rows, self.catalog), self.features["hybrid"])
            self.assertLess(max(abs(a - b) for a, b in zip(theta, mine)), 1e-9)
            validation = self.model.validate(rows, self.catalog)
            held = evaluate_rows(rows, self.catalog, self.features, self.types)
            for name, expected in (("hybrid", validation["log_loss"]),
                                   ("stop_only", validation["stop_only_baseline"]["log_loss"]),
                                   ("uniform", validation["random_baseline"]["log_loss"])):
                loss, count = held[name]["all"]
                self.assertAlmostEqual(loss / count, expected, places=12)

    def test_guarded_candidate_matches_the_same_policy_validation_and_denominators(self):
        from norn.evaluate import evaluate_rows
        policy, numeric = engine_module("choice_policy"), engine_module("numeric")
        rows = self.rows(3)
        with numeric.use(numeric.PORTABLE_CONTRACT):
            expected = policy.validate(rows, self.catalog)
        held = evaluate_rows(rows, self.catalog, self.features, self.types)
        loss, count = held["hybrid_guarded"]["all"]
        self.assertAlmostEqual(loss / count, expected["log_loss"], delta=1e-12)
        for metric in ("all", "cards", "within_type", "top1"):
            self.assertEqual(held["hybrid_guarded"][metric][1], held["hybrid"][metric][1])

    def test_guarded_candidate_never_passes_outer_holdout_to_its_gate(self):
        from norn.evaluate import evaluate_rows
        policy = engine_module("choice_policy")
        rows = self.rows(2)
        cutoff = len(rows) - 3
        alternate = deepcopy(rows)
        for row in alternate[cutoff:]:
            row["picks"] = list(reversed(row["shown"][:2]))
            row["duration_ms"] = 45000
        calls, fitted = [], policy.fit_policy

        def capture(train_rows, catalog):
            calls.append(deepcopy(train_rows))
            return fitted(train_rows, catalog)

        with patch.object(policy, "fit_policy", side_effect=capture):
            evaluate_rows(rows, self.catalog, self.features, self.types, train_count=cutoff)
            evaluate_rows(alternate, self.catalog, self.features, self.types, train_count=cutoff)
        self.assertEqual(calls, [rows[:cutoff], rows[:cutoff]])

    def test_new_candidate_has_a_separate_version_without_advancing_old_bootstrap_rng(self):
        import random
        from norn.evaluate import _candidate_rng, build_evaluation
        rng = random.Random(29)
        before = rng.getstate()
        guarded = _candidate_rng(rng, "hybrid_guarded")
        guarded.random()
        self.assertEqual(rng.getstate(), before)
        self.assertIs(_candidate_rng(rng, "hybrid"), rng)
        result = build_evaluation(simulate_records(1, visits=(1, 1), rounds=(8, 8), seed=1), draws=10)
        self.assertEqual(result["model_version"], "norn.choice.seq-mnl.v1")
        self.assertEqual(result["candidate_versions"], {"hybrid": "norn.choice.seq-mnl.v1",
                                                       "hybrid_guarded": "norn.choice.guarded-mnl.v1"})
        self.assertEqual(result["candidates"]["hybrid_guarded"], 12)

    def test_type_features_are_at_chance_within_a_type(self):
        from norn.evaluate import evaluate_rows
        for seed in range(4):
            held = evaluate_rows(self.rows(seed), self.catalog, self.features, self.types)
            for name in ("axes", "type16"):
                self.assertAlmostEqual(held[name]["within_type"][0], held["uniform"]["within_type"][0], places=9)
            self.assertEqual(held["axes"]["within_type"][1], held["hybrid"]["within_type"][1])

    def test_missing_type_annotations_only_disable_within_type_metric(self):
        from norn.evaluate import Steps, fit, score
        rows = self.rows(3)
        matrix = self.features["pixel"]
        theta = fit(Steps(rows[:-2], self.catalog), matrix)
        steps = Steps(rows[-2:], self.catalog)
        self.assertTrue((steps.chosen >= 0).any())
        typed = score(steps, matrix, theta, self.types)
        untyped = score(steps, matrix, theta, None)
        self.assertEqual(untyped["within_type"], (0.0, 0))
        for metric in ("all", "cards", "top1"):
            self.assertEqual(untyped[metric], typed[metric])

    def test_short_or_empty_visits_are_not_scored(self):
        from norn.evaluate import evaluate_rows
        rows = self.rows(1)
        self.assertIsNone(evaluate_rows(rows[:4], self.catalog, self.features, self.types))
        self.assertIsNone(evaluate_rows(rows, self.catalog, self.features, self.types, train_count=len(rows)))

    def test_verdicts_switch_only_when_the_data_say_so(self):
        from norn.evaluate import build_evaluation
        cases = {"no taste": ({"scale": 0.0}, "no_picture_model_beats_stop_only"),
                 "the model's own features": ({"scale": 1.0}, "keep_hybrid"),
                 "raw pixels": ({"truth": "pixel", "scale": 1.0}, "candidate_better")}
        for label, (settings, verdict) in cases.items():
            with self.subTest(label):
                records = simulate_records(120, visits=(1, 1), rounds=(8, 16), seed=11, **settings)
                decision = build_evaluation(records, draws=100)["decision"]
                self.assertEqual(decision["verdict"], verdict, decision)
                if verdict == "candidate_better":
                    self.assertEqual(decision["model"], "pixel")

    def test_too_few_visitors_and_broken_records(self):
        from norn.evaluate import build_evaluation, format_evaluation
        records = [{"journal": {"events": "nope"}}, *simulate_records(6, visits=(1, 2), rounds=(8, 12), seed=2)]
        result = build_evaluation(records, draws=20)
        self.assertEqual(result["skipped_records"], 1)
        self.assertEqual(result["decision"]["verdict"], "insufficient_data")
        self.assertIn("decision: insufficient_data", format_evaluation(result))
        self.assertTrue(0 <= result["held_out"]["models"]["uniform"]["top1"]["per_choice"]["value"] <= 1)

    def test_journals_are_authoritatively_validated_and_legacy_versions_still_score(self):
        from norn.evaluate import build_evaluation, format_evaluation
        from norn.events import replay_events
        original = simulate_records(1, visits=(1, 1), rounds=(8, 8), seed=3)[0]

        def as_version(version):
            record = deepcopy(original)
            if version == "norn.events.v1":
                return record
            events = []
            for row in record["journal"]["events"]:
                event = deepcopy(row)
                kind = event["type"]
                if kind in ("card_selected", "card_deselected"):
                    event["t_ms"] = 0
                    if version == "norn.events.v3":
                        event["input"] = None
                elif kind == "round_completed":
                    event.update(hidden_ms=0, seen_card_ids=None)
                    if version == "norn.events.v3":
                        event["hover"] = []
                events.append(event)
                if kind == "round_started":
                    ready = {"type": "round_ready", "round": event["round"], "ready_offset_ms": 0,
                             "unloaded_card_ids": []}
                    if version == "norn.events.v3":
                        ready["layout"] = None
                    events.append(ready)
            for number, event in enumerate(events, 1):
                event.update(event_id=f"event-{number}", sequence=number)
            record["journal"] = {"schema_version": version, "events": events}
            return record

        valid = [as_version(version) for version in ("norn.events.v1", "norn.events.v2", "norn.events.v3")]
        for record in valid:
            replay_events(record["journal"])
        invalid = [deepcopy(original) for _ in range(3)]
        invalid[0]["journal"]["schema_version"] = "norn.events.v99"
        invalid[1]["journal"]["events"][1]["sequence"] += 1
        invalid[2]["journal"]["events"][0].pop("event_id")
        # These three malformed journals still have plausible observation
        # rows; the trusted replay gate must reject them before model fitting.
        for record in invalid:
            self.assertTrue(self.model.observations(record["journal"], self.catalog))
        result = build_evaluation([*valid, *invalid, None, {}], draws=10)
        self.assertEqual(result["held_out"]["records"], 3)
        self.assertEqual(result["skipped_records"], 5)
        self.assertEqual(result["skipped_record_reasons"], {"journal_validation": 3, "record_shape": 2})
        self.assertIn("journal_validation=3", format_evaluation(result))
        self.assertEqual(sum(result["skipped_record_reasons"].values()), result["skipped_records"])

    def test_cli_reads_an_export(self):
        from norn.cli import main
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "export.jsonl"
            path.write_text("".join(json.dumps(record) + "\n" for record in
                                    simulate_records(8, visits=(1, 1), rounds=(8, 12), seed=3)), encoding="utf-8")
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["records", "evaluate", "--input", str(path), "--json", "--draws", "20"]), 0)
        result = json.loads(out.getvalue())
        self.assertEqual(result["schema_version"], "norn.evaluation.v1")
        self.assertEqual(result["held_out"]["records"], 8)
        self.assertEqual(set(result["learning_curve"]), {"2", "4", "6", "8"})


class MisspecifiedSimulationTests(unittest.TestCase):
    def test_every_taste_and_choice_rule_yields_valid_journals(self):
        from norn.events import replay_events
        from norn.simulate import PROCESSES, TRUTHS
        for truth in TRUTHS:
            for process in PROCESSES:
                with self.subTest(truth=truth, process=process):
                    for record in simulate_records(2, visits=(1, 1), rounds=(6, 8), truth=truth, process=process,
                                                   position_bias=0.5, fatigue=0.5, seed=1):
                        replay_events(record["journal"])
        with self.assertRaises(ValueError):
            simulate_records(1, truth="mood")

    def test_default_settings_replay_earlier_seeds(self):
        # The stress settings draw nothing when off, so earlier simulated results stay reproducible
        # (checked against cells of measurement-power-simulated-v2.json when this was pinned).
        from norn.events import content_hash
        self.assertEqual(content_hash(simulate_records(3, seed=5)),
                         "sha256:830dbbec8dcf15a47d5c965e0efa37a266c103d57f0491bc97b1328669ea9be1")


if __name__ == "__main__":
    unittest.main()

from copy import deepcopy
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from norn.benchmark import default_journal
from norn.cli import main
from norn.events import replay_events
from norn.fast_compare import STOP, _steps, build_decision_request, compare_journal, prepare_experiment


class FakeJev:
    model = "jev-1.13.0"
    provider = "typesafe"
    label = "Jev"
    calls = 0

    def decide(self, state, questions):
        self.calls += 1
        self.state, self.questions = deepcopy(state), deepcopy(questions)
        return {"model": self.model, "elapsed_ms": 123, "provider_calls": 1,
                "usage": {"input_tokens": 100, "output_tokens": 10},
                "answers": {key: {"probabilities": dict.fromkeys(question["criteria"], 1 / len(question["criteria"])),
                                   "confidence": 0} for key, question in questions.items()}}


class FastComparisonTests(unittest.TestCase):
    def test_observation_targets_keep_order_and_skip_forced_stop(self):
        rows = [{"round": 1, "shown": ["a", "b"], "picks": ["b", "a"]},
                {"round": 2, "shown": ["c", "d"], "picks": []},
                {"round": 3, "shown": [], "picks": []}]
        steps = _steps(rows)
        self.assertEqual([(row["candidate_ids"], row["target"]) for row in steps],
                         [(["a", "b"], "b"), (["a"], "a"), (["c", "d"], STOP)])

    def test_rounded_wire_probabilities_share_one_explicit_tolerance(self):
        from norn.fast_thinkers import validate_probabilities
        rounded = {"a": 0.5000001, STOP: 0.5}
        self.assertEqual(validate_probabilities(rounded, ["a"]), rounded)
        with self.assertRaises(ValueError):
            validate_probabilities({"a": 0.50001, STOP: 0.5}, ["a"])

    def test_offline_never_reads_credential_or_calls_remote_and_preserves_input(self):
        journal = default_journal()
        before = deepcopy(journal)
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "do-not-use"}), patch(
                "norn.jev.JevClient", side_effect=AssertionError("Must stay offline")):
            report = compare_journal(journal, provenance="fixture")
        self.assertEqual(journal, before)
        self.assertEqual(report["training"]["rounds"], 19)
        self.assertEqual(report["models"]["decision"]["status"], "unavailable")
        self.assertEqual(report["models"]["decision"]["summary"]["coverage"], 0)
        self.assertIsNone(report["models"]["decision"]["summary"]["all_nll"])
        for key in ("ml", "ml_policy", "heuristic", "stop_only"):
            self.assertEqual(report["models"][key]["summary"]["coverage"], 1)
            self.assertEqual(report["models"][key]["summary"]["provider_calls"], 0)
        self.assertNotIn("do-not-use", json.dumps(report))

    def test_remote_batch_uses_same_numeric_features_and_no_heldout_labels(self):
        client = FakeJev()
        report = compare_journal(default_journal(), decision_client=client)
        self.assertEqual(client.calls, 1)
        self.assertEqual(len(client.state["past_completed_rounds"]), 19)
        encoded = json.dumps({"state": client.state, "questions": client.questions})
        for forbidden in ("NORN_", '"target"', '"duration_ms"', '"reading"', '"profile"', '"scene"', '"motifs"'):
            self.assertNotIn(forbidden, encoded)
        self.assertTrue(all(STOP in q["criteria"] for q in client.questions.values()))
        self.assertEqual(len(client.questions), 5)
        self.assertEqual(report["training"]["target_scope"], "first_kept_or_stop_per_round")
        self.assertEqual(report["models"]["decision"]["summary"]["coverage"], 1)
        self.assertEqual(report["models"]["decision"]["summary"]["provider_calls"], 1)
        self.assertEqual(report["models"]["decision"]["summary"]["elapsed_ms"], 123)

    def test_changing_test_targets_does_not_change_frozen_state_or_first_prediction(self):
        original = default_journal()
        alternate = deepcopy(original)
        for event in alternate["events"]:
            if event.get("round") == 20 and event.get("card_id") == "NORN_0020":
                event["card_id"] = "NORN_0044"
        replay_events(alternate)
        left = compare_journal(original)
        right = compare_journal(alternate)
        self.assertEqual(left["training"], right["training"])
        self.assertNotEqual(left["decisions"][0]["target"], right["decisions"][0]["target"])
        for name in ("heuristic", "ml", "ml_policy", "stop_only"):
            self.assertEqual(left["decisions"][0]["predictions"][name]["probabilities"],
                             right["decisions"][0]["predictions"][name]["probabilities"])
        one = prepare_experiment(original)
        two = prepare_experiment(alternate)
        request1 = build_decision_request(*one[:3])
        request2 = build_decision_request(*two[:3])
        self.assertEqual(request1[0], request2[0])
        self.assertEqual(request1[1]["decision_0001"], request2[1]["decision_0001"])

    def test_guarded_and_original_ml_score_identical_untouched_targets_with_separate_identity(self):
        report = compare_journal(default_journal(), train_rounds=8, provenance="fixture")
        original, guarded = report["models"]["ml"], report["models"]["ml_policy"]
        self.assertEqual(original["metadata"]["source_version"], "norn.choice.seq-mnl.v1")
        self.assertEqual(guarded["metadata"]["source_version"], "norn.choice.guarded-mnl.v1")
        self.assertAlmostEqual(original["summary"]["all_nll"], 2.314665075622904, delta=1e-12)
        self.assertAlmostEqual(report["models"]["heuristic"]["summary"]["all_nll"], 1.7162204189315207, delta=1e-12)
        self.assertEqual(guarded["metadata"]["prefix_hash"], original["metadata"]["prefix_hash"])
        self.assertEqual(guarded["summary"]["decision_count"], 16)
        self.assertEqual(original["summary"]["decision_count"], 16)
        for decision in report["decisions"]:
            self.assertEqual(set(decision["predictions"]["ml"]["probabilities"]),
                             set(decision["predictions"]["ml_policy"]["probabilities"]))
        self.assertIn("training-prefix content evidence", " ".join(report["limits"]))

    def test_heldout_timing_and_display_order_cannot_change_policy_selection(self):
        original = default_journal()
        alternate = deepcopy(original)
        for event in alternate["events"]:
            if event.get("round", 0) > 8:
                if event["type"] == "round_completed":
                    event["duration_ms"] = 45000
                if event["type"] == "round_started":
                    event["card_ids"].reverse()
        left = compare_journal(original, train_rounds=8)
        right = compare_journal(alternate, train_rounds=8)
        self.assertEqual(left["models"]["ml_policy"]["metadata"], right["models"]["ml_policy"]["metadata"])
        for a, b in zip(left["decisions"], right["decisions"]):
            for card, probability in a["predictions"]["ml_policy"]["probabilities"].items():
                self.assertAlmostEqual(probability, b["predictions"]["ml_policy"]["probabilities"][card], delta=1e-12)

    def test_remote_failure_remains_explicit_with_zero_coverage(self):
        from norn.jev import JevError
        client = FakeJev()
        def fail(state, questions):
            client.calls += 1
            raise JevError("timeout")
        client.decide = fail
        report = compare_journal(default_journal(), decision_client=client)
        self.assertEqual(report["models"]["decision"]["status"], "failed")
        self.assertEqual(report["models"]["decision"]["summary"]["coverage"], 0)
        self.assertEqual(report["models"]["decision"]["summary"]["provider_calls"], 1)
        self.assertIsNone(report["models"]["decision"]["summary"]["all_nll"])

    def test_invalid_or_unbounded_experiment_fails_before_remote(self):
        client = FakeJev()
        for kwargs in ({"train_rounds": True}, {"train_rounds": 24}, {"max_decisions": 1},
                       {"max_decisions": 513}, {"input_mode": "fake"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                compare_journal(default_journal(), decision_client=client, **kwargs)
        self.assertEqual(client.calls, 0)

    def test_cli_fixture_writes_offline_report_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            output = Path(directory) / "comparison.json"
            self.assertEqual(main(["compare", "--fixture", "--output", str(output)]), 0)
            saved = output.read_bytes()
            self.assertEqual(json.loads(saved)["provenance"]["kind"], "fixture")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["compare", "--fixture", "--output", str(output)]), 2)
            self.assertEqual(output.read_bytes(), saved)

    def test_real_transport_accepts_the_runner_request_without_network(self):
        from norn.jev import JevClient
        from norn.openai_decisions import OpenAIDecisionsClient
        calls = []
        class Response(io.BytesIO):
            status = 200
            headers = {}
        def opener(request, *, timeout):
            calls.append(request)
            body = json.loads(request.data)
            if "input" in body:
                answers = [{"type": "choice", "name": q["name"],
                            "choice": q["choices"][0]["value"], "confidence": 0,
                            "probabilities": [{"value": c["value"], "probability": 1 / len(q["choices"])}
                                              for c in q["choices"]]} for q in body["questions"]]
            else:
                answers = {key: {"type": "choice", "choice": next(iter(q["criteria"])), "confidence": 0,
                                 "probabilities": dict.fromkeys(q["criteria"], 1 / len(q["criteria"]))}
                           for key, q in body["questions"].items()}
            return Response(json.dumps({"model": body["model"], "answers": answers,
                                        "usage": {"input_tokens": 1, "output_tokens": 1}}).encode())
        for client_class in (JevClient, OpenAIDecisionsClient):
            with self.subTest(provider=client_class.__name__):
                report = compare_journal(default_journal(), decision_client=client_class("fixture-key", opener=opener))
                self.assertEqual(report["models"]["decision"]["status"], "ok")
                self.assertEqual(report["models"]["decision"]["summary"]["coverage"], 1)
                self.assertNotIn("fixture-key", json.dumps(report))
        self.assertEqual(len(calls), 2)

    def test_openai_requires_api_key_only_when_explicitly_selected(self):
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["compare", "--fixture", "--decision-provider", "openai"]), 2)

    def test_invalid_output_destination_fails_before_remote_dispatch(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"OPENAI_API_KEY": "fixture-key"}), \
                patch("norn.openai_decisions.OpenAIDecisionsClient") as constructor, \
                contextlib.redirect_stderr(io.StringIO()):
            for flag in ("--output", "--html"):
                output = Path(directory) / "missing" / "out.json"
                self.assertEqual(main(["compare", "--fixture", "--decision-provider", "openai", flag, str(output)]), 2)
            constructor.assert_not_called()

    def test_exhausted_reused_client_does_not_count_an_old_call_again(self):
        from norn.jev import JevError
        client = FakeJev()
        client.calls = 3
        def exhausted(state, questions):
            raise JevError("call_limit")
        client.decide = exhausted
        report = compare_journal(default_journal(), decision_client=client)
        remote = report["models"]["decision"]
        self.assertEqual(remote["reason"], "call_limit")
        self.assertEqual(remote["summary"]["provider_calls"], 0)


if __name__ == "__main__":
    unittest.main()

"""Guarded profiles use operational parameters while old replays stay exact."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import unittest

from norn.benchmark import default_journal
from norn.context import build_session_context
from norn.core import engine_module
from norn.events import content_hash
from norn.profile import (build_event_profile, build_replay_bundle, build_snapshot_profile, replay_bundle,
                          verify_profile)
from norn.service import NornService, RequestError


def journal(*, open_last=False):
    events = [
        {"type": "round_started", "round": 1, "card_ids": ["NORN_0001", "NORN_0009", "NORN_0017"]},
        {"type": "card_selected", "round": 1, "card_id": "NORN_0001"},
        {"type": "round_completed", "round": 1, "duration_ms": 1400},
    ]
    if open_last:
        events += [
            {"type": "round_started", "round": 2, "card_ids": ["NORN_0002", "NORN_0010", "NORN_0018"]},
            {"type": "card_selected", "round": 2, "card_id": "NORN_0002"},
        ]
    else:
        events.append({"type": "session_completed"})
    return {"schema_version": "norn.events.v1", "events": [
        {"event_id": f"policy-{index:04d}", "sequence": index, **event}
        for index, event in enumerate(events, 1)]}


def rehash(profile):
    profile["profile_hash"] = content_hash({k: v for k, v in profile.items() if k != "profile_hash"})


class ChoicePolicyIntegrationTests(unittest.TestCase):
    def test_default_v5_replay_and_explicit_v4_golden_are_both_supported(self):
        source = journal()
        profile = build_event_profile(source)
        self.assertEqual((profile["schema_version"], profile["algorithm_version"]),
                         ("norn.profile.v5", "norn.scoring.v5"))
        self.assertEqual(profile["analysis"]["attribution"]["algorithm_version"], "norn.scoring.v5")
        self.assertEqual(profile["analysis"]["preference"]["model_version"], "norn.choice.guarded-mnl.v1")
        self.assertTrue(verify_profile(profile, source))
        self.assertEqual(replay_bundle(json.loads(json.dumps(build_replay_bundle(profile, source)))), profile)

        historical = build_event_profile(default_journal(), algorithm_version="norn.scoring.v4")
        self.assertEqual(historical["profile_hash"],
                         "sha256:429ce269da37b7f484d92aa042c6450ef5e644775bac93b7510033324bb78c3f")
        self.assertEqual(build_session_context(historical)["context_hash"],
                         "sha256:5a151714bfc4076152ac6a674b1f1c3ccd9242c744837ba228a4463c8acb47e9")
        self.assertNotIn("guard", historical["analysis"]["preference"])
        self.assertTrue(verify_profile(historical, default_journal()))

    def test_baseline_map_and_contributions_use_effective_not_candidate_parameters(self):
        service = NornService()
        full = service.preview({"journal": journal()})
        preference = full["preference"]
        self.assertEqual(preference["guard"]["status"], "baseline")
        self.assertFalse(preference["effective"]["active"])
        self.assertEqual(preference["effective"]["theta"][:12], [0.0] * 12)
        self.assertTrue(any(value != 0 for value in preference["posterior"]["mean"][:12]),
                        "The candidate can fit a sample without being admitted for personalization")
        self.assertEqual(preference["recommendations"], [])
        self.assertEqual({point["score"] for point in full["preference_map"]["points"]}, {0.0})
        block = preference["contributions"]
        self.assertEqual(block["weights"], preference["effective"]["theta"][:12])
        self.assertTrue(all(row["score"] == 0.0 and all(term == 0.0 for term in row["terms"])
                            for row in block["cards"]))
        self.assertTrue(engine_module("choice_model").verify_contributions(block))

        compact = service.preview({"journal": journal(), "compact": True})
        self.assertEqual(compact["preference"]["guard"]["status"], "baseline")
        self.assertIs(compact["preference"]["effective"]["active"], False)
        self.assertNotIn("theta", compact["preference"]["effective"], "Chart transport stays compact")
        self.assertEqual(compact["preference_map"], full["preference_map"])

    def test_rehashed_effective_parameters_or_admission_cannot_forge_a_profile(self):
        source = journal()
        profile = build_event_profile(source)
        mutations = (
            lambda p: p["analysis"]["preference"]["effective"]["theta"].__setitem__(0, 0.5),
            lambda p: p["analysis"]["preference"]["guard"].__setitem__("status", "active"),
            lambda p: p["analysis"]["preference"]["posterior"]["mean"].__setitem__(0, 0.5),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                altered = deepcopy(profile)
                mutation(altered)
                rehash(altered)
                with self.assertRaisesRegex(ValueError, "Replayed profile differs"):
                    verify_profile(altered, source)

    def test_baseline_context_keeps_recorded_evidence_and_omits_learned_recommendations(self):
        profile = build_event_profile(journal())
        for language in ("zh", "en"):
            with self.subTest(language=language):
                context = build_session_context(profile, language=language)
                block = context["block"]
                preference = block["preference"]
                self.assertEqual(preference["personalization"], "not_established")
                self.assertEqual(preference["drawn_to"], [])
                self.assertEqual(preference["next_to_try"], [])
                self.assertEqual(block["evidence"]["selection_total"], 1)
                self.assertEqual(block["evidence"]["selections"][0]["card_id"], "NORN_0001")
                self.assertIsInstance(preference["evidence_clarity"], str)
                self.assertNotIn("log_evalue", context["canonical_block"])
        historical_prompt = build_session_context(profile, "norn.prompt.v4")
        self.assertEqual(historical_prompt["block"]["preference"]["personalization"], "not_established")
        self.assertIn("不作偏好排序或预测推荐", historical_prompt["block"]["preference_scope"])

    def test_prose_projection_does_not_treat_candidate_posterior_as_operational_utility(self):
        profile = build_event_profile(journal())
        # Context projects an already verified frozen profile. Isolate that
        # projection contract: changing a candidate-only diagnostic must not
        # change baseline language or invent recommendations.
        changed = deepcopy(profile)
        changed["analysis"]["preference"]["posterior"]["mean"] = [99.0] * 13
        self.assertEqual(build_session_context(profile), build_session_context(changed))

    def test_snapshot_without_display_observations_keeps_the_v4_prompt_projection(self):
        profile = build_snapshot_profile({"total_rounds": 1,
                                          "selections": [{"id": "NORN_0001", "round": 1}],
                                          "round_durations": [], "deselection_events": []})
        preference = profile["analysis"]["preference"]
        self.assertFalse(preference["trained"])
        self.assertEqual(preference["guard"]["status"], "baseline")
        context = build_session_context(profile)
        self.assertEqual(context["prompt_version"], "norn.prompt.v4")
        self.assertNotIn("drawn_to", context["block"]["preference"])

    def test_instance_scoring_routes_are_explicit_concurrent_and_not_http_options(self):
        old = NornService(algorithm_version="norn.scoring.v4")
        current = NornService()
        source = journal()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda service: service.analyze({"journal": source}), (old, current)))
        self.assertEqual([r["algorithm_version"] for r in results], ["norn.scoring.v4", "norn.scoring.v5"])
        for service, algorithm, expected_model, schema in (
                (old, "norn.scoring.v4", "norn.choice.seq-mnl.v1", "norn.preview.v4"),
                (current, "norn.scoring.v5", "norn.choice.guarded-mnl.v1", "norn.preview.v5")):
            with self.subTest(algorithm=algorithm):
                complete = service.preview({"journal": source})
                opened = service.preview({"journal": journal(open_last=True)})
                self.assertEqual(complete["profile"]["algorithm_version"], algorithm)
                self.assertEqual(opened["profile"]["schema_version"], schema)
                self.assertEqual(opened["profile"]["algorithm_version"], algorithm)
                self.assertEqual(service.trajectory({"journal": source})["model_version"], expected_model)
                snapshot = service.analyze({"total_rounds": 1, "selections": [{"id": "NORN_0001", "round": 1}]})
                self.assertEqual(snapshot["algorithm_version"], algorithm)
                with self.assertRaises(RequestError):
                    service.analyze({"journal": source, "algorithm_version": "norn.scoring.v5"})
                with self.assertRaises(RequestError):
                    service.preview({"journal": source, "algorithm_version": "norn.scoring.v5"})
        # Instance defaults do not migrate a verified export into another route.
        old_bundle = old.export_session(results[0]["session_id"])
        new_bundle = current.export_session(results[1]["session_id"])
        self.assertEqual(current.trajectory(old_bundle)["model_version"], "norn.choice.seq-mnl.v1")
        self.assertEqual(old.trajectory(new_bundle)["model_version"], "norn.choice.guarded-mnl.v1")
        from norn import profile as profile_module
        self.assertEqual(profile_module.ALGORITHM_VERSION, "norn.scoring.v5")
        for invalid in ("norn.scoring.v1", "norn.scoring.v6", "", False, 4):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                NornService(algorithm_version=invalid)

    def test_preview_and_trajectory_versions_follow_current_or_stored_policy(self):
        service = NornService()
        preview = service.preview({"journal": journal(open_last=True)})
        self.assertEqual(preview["profile"]["schema_version"], "norn.preview.v5")
        self.assertEqual(preview["profile"]["algorithm_version"], "norn.scoring.v5")
        self.assertTrue(preview["profile"]["provisional"])
        self.assertFalse(preview["complete"])
        self.assertEqual(preview["preference"]["guard"]["evidence_rounds"], 1)

        source = journal()
        current = service.trajectory({"journal": source})
        self.assertEqual(current["model_version"], "norn.choice.guarded-mnl.v1")
        self.assertEqual(current["provider_calls"], 0)
        for algorithm, expected_model in (("norn.scoring.v4", "norn.choice.seq-mnl.v1"),
                                          ("norn.scoring.v5", "norn.choice.guarded-mnl.v1")):
            with self.subTest(algorithm=algorithm):
                profile = build_event_profile(source, algorithm_version=algorithm)
                replay = build_replay_bundle(profile, source)
                exported = service.trajectory(replay)
                self.assertTrue(exported["verified"])
                self.assertEqual(exported["profile"]["algorithm_version"], algorithm)
                self.assertEqual(exported["model_version"], expected_model)

        result = service.analyze({"journal": source})
        key = result["session_id"]
        before = service.export_session(key)
        trajectory = service.session_trajectory(key)
        self.assertEqual(trajectory["model_version"], "norn.choice.guarded-mnl.v1")
        self.assertEqual(service.export_session(key), before)
        # Simulate restoring a historical session; its display follows its own
        # archived profile instead of adopting the new default scorer.
        old = build_event_profile(source, algorithm_version="norn.scoring.v4")
        service.sessions.get(key).replay_bundle = build_replay_bundle(old, source)
        self.assertEqual(service.session_trajectory(key)["model_version"], "norn.choice.seq-mnl.v1")
        self.assertEqual(service.limits.snapshot()["model_calls_reserved"], 0)


if __name__ == "__main__":
    unittest.main()

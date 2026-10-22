"""Production profile, prompt, preview and local personal-checkpoint integration."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from norn.cli import main
from norn.context import build_session_context, build_analysis_prompt
from norn.core import engine_module
from norn.events import content_hash, replay_events
from norn.profile import build_event_profile, build_replay_bundle, verify_profile
from norn.replay import replay_document
from norn.service import NornService


FIXTURES = Path(__file__).parent / "fixtures"


def personal_journal(round_count=12, *, offset=0, complete=True, open_last=False):
    events = []
    def add(kind, **fields):
        events.append({"sequence": len(events) + 1, "event_id": f"local-{offset}-{len(events) + 1}", "type": kind, **fields})
    for number in range(1, round_count + 1):
        ids = [f"NORN_{offset + (number - 1) * 3 + index:04}" for index in (1, 2, 3)]
        add("round_started", round=number, card_ids=ids)
        add("card_selected", round=number, card_id=ids[0])
        if not (open_last and number == round_count):
            add("round_completed", round=number, duration_ms=1000)
    if complete:
        add("session_completed")
    return {"schema_version": "norn.events.v1", "events": events}


def rehash_profile(profile):
    profile["profile_hash"] = content_hash({key: value for key, value in profile.items() if key != "profile_hash"})


class LearningIntegrationTests(unittest.TestCase):
    def test_default_profile_and_model_context_use_learned_preferences(self):
        source = personal_journal()
        profile = build_event_profile(source)
        self.assertEqual(profile["schema_version"], "norn.profile.v5")
        self.assertEqual(profile["algorithm_version"], "norn.scoring.v5")
        self.assertEqual(profile["analysis"]["analysis_kind"], "learned_preference")
        self.assertNotIn("preference_model", profile, "The 979-feature ranker is no longer the default")
        preference = profile["analysis"]["preference"]
        self.assertEqual(preference["model_version"], engine_module("choice_policy").MODEL_VERSION)
        self.assertEqual(preference["observation_count"], 12)
        self.assertEqual((preference["pick_count"], preference["stop_count"]), (12, 12))
        older = build_event_profile(source, algorithm_version="norn.scoring.v3")
        self.assertEqual(older["preference_model"]["training"]["observation_count"], 12)
        self.assertEqual(older["preference_model"]["source_hashes"], [content_hash(source)])
        self.assertGreater(older["analysis"]["preference"]["pair_count"], 0)
        context = build_session_context(profile)
        prompt = build_analysis_prompt(context)
        block = context["block"]
        self.assertEqual(block["analysis_kind"], "learned_preference")
        for field in ("behavior_profile", "emotional_texture", "concrete_anchors", "signature_signal"):
            self.assertNotIn(field, block)
        for field in ("coords_avg", "weighted_coords", "summation_delta", '"E_I"', '"weights"', '"feature_transform"'):
            self.assertNotIn(field, json.dumps(prompt, ensure_ascii=False))
        self.assertEqual(len(prompt["messages"]), 1)  # old personality fewshots are absent
        self.assertIn("不作人格、关系或压力诊断", prompt["messages"][0]["content"])
        # Prompt v5 speaks in card motifs; pixel components never reach the prose model.
        if preference["guard"]["status"] == "baseline":
            self.assertEqual(block["preference"]["drawn_to"], [])
            self.assertEqual(block["preference"]["next_to_try"], [])
            self.assertFalse(preference["effective"]["active"])
        else:
            self.assertTrue(block["preference"]["drawn_to"])
        self.assertNotIn("top_features", block["preference"])
        self.assertNotIn("画面视觉成分", json.dumps(prompt, ensure_ascii=False))
        # Type guesses and archetype-axis weights stay in X-Ray, out of the language context.
        for field in ("type_probabilities", '"axes"', "INFP", "ENTJ"):
            self.assertNotIn(field, json.dumps(prompt, ensure_ascii=False))

    def test_three_followups_keep_the_original_native_prefix_and_frozen_model(self):
        service = NornService()
        result = service.analyze({"journal": personal_journal(25)})
        key = result["session_id"]
        inspection = service.inspect_session(key)
        original_profile = deepcopy(inspection["profile"])
        first_prompt = deepcopy(inspection["calls"][0]["prompt"])
        expected_history = first_prompt["messages"] + [{"role": "assistant", "content": result["reading"]}]
        for turn in range(1, 4):
            response = service.dialogue({"session_id": key, "user_message": f"为什么，追问{turn}？",
                                         "turn_count": 999, "analysis": {"reading": "forged-other-user"}})
            self.assertEqual(response["status"], "ok")
            self.assertEqual(response["turn_count"], turn)
            inspected = service.inspect_session(key)
            current = inspected["calls"][-1]["prompt"]
            self.assertEqual(current["system"], first_prompt["system"])
            self.assertEqual(current["messages"][:-1], expected_history)
            self.assertNotIn("forged-other-user", json.dumps(current))
            expected_history = current["messages"] + [{"role": "assistant", "content": response["reply"]}]
            self.assertEqual(inspected["profile"], original_profile)
        self.assertTrue(response["ended"])

    def test_partial_preview_map_uses_authoritative_projection_and_model_scores(self):
        service = NornService()
        source = personal_journal(3, complete=False, open_last=True)
        original = deepcopy(source)
        with patch("norn.providers.urlopen", side_effect=AssertionError("Preview contacted a provider")):
            preview = service.preview({"journal": source})
        self.assertEqual(source, original)
        self.assertFalse(preview["complete"])
        self.assertTrue(preview["profile"]["provisional"])
        self.assertEqual(preview["profile"]["schema_version"], "norn.preview.v5")
        learned = preview["profile"]["analysis"]["preference"]
        self.assertTrue(learned["provisional"])
        self.assertEqual((learned["observation_count"], learned["stop_count"]), (3, 2),
                         "The open round's pick counts; its stop has not happened yet")
        self.assertEqual(preview["calls"], [])
        self.assertEqual(len(service.sessions._sessions), 0)
        points = preview["preference_map"]["points"]
        self.assertEqual(len(points), 256)
        catalog = engine_module("preference_model").load_catalog()
        choice = engine_module("choice_model")
        expected = dict(zip(choice.load_catalog().ids, choice.card_scores(learned["effective"]["theta"])))
        selected = {row["id"] for row in replay_events(source, require_complete=False)["input"]["selections"]}
        for point in points:
            self.assertEqual([point["x"], point["y"]], catalog["projection"]["points"][point["card_id"]])
            self.assertEqual(point["score"], expected[point["card_id"]])
            self.assertEqual(point["selected"], point["card_id"] in selected)
        self.assertIn("not personality probability", preview["preference_map"]["scope"])

    def test_multiple_explicit_recordings_train_and_rank_through_cli(self):
        sources = [personal_journal(6), personal_journal(6, offset=30)]
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index, source in enumerate(sources):
                path = Path(directory) / f"record-{index}.json"
                path.write_text(json.dumps(source), encoding="utf-8")
                paths.append(path)
            checkpoint_path = Path(directory) / "personal-model.json"
            output, errors = io.StringIO(), io.StringIO()
            with redirect_stdout(output), redirect_stderr(errors):
                code = main(["learn", *map(str, paths), "--output", str(checkpoint_path), "--json"])
            self.assertEqual(code, 0, errors.getvalue())
            summary = json.loads(output.getvalue())
            self.assertEqual(summary["source_count"], 2)
            self.assertEqual(summary["observation_count"], 12)
            checkpoint = json.loads(checkpoint_path.read_text())
            self.assertEqual(checkpoint["source_hashes"], [content_hash(source) for source in sources])
            self.assertEqual(len(checkpoint["weights"]), 979)
            for replay_sources in (False, True):
                args = ["rank", str(checkpoint_path), "--top", "3", "--json"]
                if replay_sources:
                    for path in paths:
                        args.extend(["--recording", str(path)])
                output = io.StringIO()
                with redirect_stdout(output), redirect_stderr(errors):
                    code = main(args)
                self.assertEqual(code, 0, errors.getvalue())
                ranking = json.loads(output.getvalue())
                self.assertTrue(ranking["verified"])
                self.assertEqual(ranking["source_replayed"], replay_sources)
                self.assertEqual(len(ranking["predictions"]), 3)
                self.assertEqual(ranking["model_calls"], 0)
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["learn", str(paths[0]), "--output", str(checkpoint_path)]), 1)

    def test_rehashed_model_weights_and_contribution_forgery_fail_replay(self):
        source = personal_journal(12)
        profile = build_event_profile(source, algorithm_version="norn.scoring.v3")
        weight_forgery = deepcopy(profile)
        checkpoint = weight_forgery["preference_model"]
        checkpoint["weights"][0] += 0.125
        checkpoint["checkpoint_hash"] = content_hash({key: value for key, value in checkpoint.items() if key != "checkpoint_hash"})
        rehash_profile(weight_forgery)
        with self.assertRaises(ValueError):
            verify_profile(weight_forgery, source)
        ledger_forgery = deepcopy(profile)
        contribution = ledger_forgery["analysis"]["preference"]["recommendations"][0]["contributions"][0]
        contribution["contribution"] += 0.125
        rehash_profile(ledger_forgery)
        with self.assertRaises(ValueError):
            replay_document({"schema_version": "norn.replay.v1", "journal": source, "profile": ledger_forgery})

    def test_real_legacy_v1_fixture_replays_unchanged_without_learning(self):
        fixture = json.loads((FIXTURES / "legacy-profile-v1.json").read_text())
        frozen = deepcopy(fixture)
        with patch.object(engine_module("preference_model"), "train", side_effect=AssertionError("v1 replay trained ML")):
            replayed = replay_document(fixture)
            self.assertTrue(verify_profile(fixture["profile"], fixture["journal"]))
        self.assertEqual(fixture, frozen)
        self.assertEqual(replayed, frozen)
        self.assertEqual(replayed["profile"]["schema_version"], "norn.profile.v1")
        self.assertEqual(replayed["profile"]["algorithm_version"], "norn.scoring.v1")
        self.assertNotIn("preference_model", replayed["profile"])

    def test_separate_sessions_do_not_implicitly_pool_other_persons_records(self):
        service = NornService()
        a, b = personal_journal(6), personal_journal(6, offset=80)
        first = service.analyze({"journal": a})
        second = service.analyze({"journal": b})
        a_profile = service.export_session(first["session_id"])["profile"]
        b_profile = service.export_session(second["session_id"])["profile"]
        self.assertEqual(a_profile["events_hash"], content_hash(a))
        self.assertEqual(b_profile["events_hash"], content_hash(b))
        self.assertNotEqual(a_profile["analysis"]["preference"]["training_hash"],
                            b_profile["analysis"]["preference"]["training_hash"])
        a_prompt = service.inspect_session(first["session_id"])["prompt"]
        self.assertNotIn("NORN_0081", json.dumps(a_prompt))
        self.assertNotEqual(a_profile["profile_hash"], b_profile["profile_hash"])
        self.assertEqual(a_profile["analysis"]["preference"]["observation_count"], 6)
        self.assertEqual(b_profile["analysis"]["preference"]["observation_count"], 6)


if __name__ == "__main__":
    unittest.main()

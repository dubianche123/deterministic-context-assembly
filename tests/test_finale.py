"""The public finale cannot move its guess after seeing the favourite."""

from copy import deepcopy
import unittest

from norn.actor_state import dump_actor_state, new_actor_service
from norn.events import content_hash
from norn.finale import validate_result, jev_journal
from norn.product import PRODUCT_VERSION
from norn.service import NornService, RequestError
from test_research_deck import DECK, research, session


PRODUCT = {"schema_version": PRODUCT_VERSION, "pacing_policy": "norn.pacing.v1"}


class FinaleTests(unittest.TestCase):
    def setUp(self):
        self.service = NornService(provider="mock", deck="pamela-v1")
        self.journal = session(research.RANDOM, rounds=3)
        self.body = {"journal": self.journal, "product": PRODUCT, "challenge_id": "last-card-test"}

    def test_seals_unseen_candidates_and_retries_without_disclosing_the_guess(self):
        public = self.service.finale_start(self.body)
        self.assertEqual(public, self.service.finale_start(self.body))
        self.assertFalse({"predicted_card_id", "probabilities", "salt", "journal"} & set(public))
        seen = {c for e in self.journal["events"] if e["type"] == "round_started" for c in e["card_ids"]}
        self.assertEqual(len(set(public["candidate_ids"])), 4)
        self.assertFalse(set(public["candidate_ids"]) & seen)
        pick = {"challenge_id": public["challenge_id"], "card_id": public["candidate_ids"][0], "elapsed_ms": 1500}
        result = self.service.finale_choose(pick)
        self.assertEqual(result["commitment"], public["commitment"])
        self.assertEqual(result, self.service.finale_choose(pick))
        self.assertEqual(validate_result(result, self.journal), result)
        with self.assertRaises(RequestError) as changed:
            self.service.finale_choose({**pick, "card_id": public["candidate_ids"][1]})
        self.assertEqual(changed.exception.status, 409)
        broken = deepcopy(result)
        broken["predicted_card_id"] = next(c for c in result["candidate_ids"] if c != result["predicted_card_id"])
        broken["hit"] = broken["predicted_card_id"] == broken["chosen_card_id"]
        with self.assertRaises(ValueError):
            validate_result(broken, self.journal)

    def test_creative_reading_and_three_followups_preserve_the_exploration_profile(self):
        public = self.service.finale_start(self.body)
        chosen = public["candidate_ids"][1]
        result = self.service.finale_choose({"challenge_id": public["challenge_id"], "card_id": chosen, "elapsed_ms": 0})
        for language in ("zh", "en"):
            response = self.service.analyze({"journal": self.journal, "product": PRODUCT,
                "finale_id": public["challenge_id"], "language": language, "request_id": "reading-" + language})
            self.assertEqual(response["profile_hash"], result["profile_hash"])
            self.assertEqual(response["persona"]["card_id"], chosen)
            self.assertEqual(response["max_dialogue_turns"], 3)
            self.assertEqual(response["finale"], result)
            exported = self.service.export_session(response["session_id"])
            self.assertNotIn(chosen, [e.get("card_id") for e in exported["journal"]["events"]])
            reply = self.service.dialogue({"session_id": response["session_id"], "user_message": "why?" if language == "en" else "依据是什么？", "turn_id": "ask-" + language})
            self.assertTrue(reply["reply"])

    def test_actor_eviction_keeps_private_commitment_and_prevents_post_choice_jev(self):
        self.service = new_actor_service(deck="pamela-v1")
        public = self.service.finale_start(self.body)
        restored = new_actor_service(dump_actor_state(self.service), deck="pamela-v1")
        self.assertEqual(restored.finale_start(self.body), public)
        result = restored.finale_choose({"challenge_id": public["challenge_id"], "card_id": public["candidate_ids"][0], "elapsed_ms": 100})
        restored = new_actor_service(dump_actor_state(restored), deck="pamela-v1")
        self.assertEqual(restored._finale_entry("finale-choice", public["challenge_id"]).result, result)
        with self.assertRaises(RequestError) as late:
            restored.finale_jev_plan({"challenge_id": public["challenge_id"]})
        self.assertEqual(late.exception.status, 409)

    def test_jev_question_has_past_only_and_late_response_cannot_regrade_choice(self):
        public = self.service.finale_start({**self.body, "jev_session": "A" * 43})
        plan = self.service.finale_jev_plan({"challenge_id": public["challenge_id"]})
        from norn.jev_live import prepare
        question = prepare(plan["body"]["journal"], DECK)
        self.assertEqual(question["candidate_ids"], public["candidate_ids"])
        self.assertEqual(question["cutoff_rounds"], 3)
        self.assertEqual(question["journal"]["events"][-1]["type"], "round_started")
        result = self.service.finale_choose({"challenge_id": public["challenge_id"], "card_id": public["candidate_ids"][0], "elapsed_ms": 50})
        late = self.service.finale_settle_jev(public["challenge_id"], {"status": "ok", "candidate_ids": public["candidate_ids"], "probabilities": [.9, .05, .03, .02], "provider_calls": 1})
        self.assertEqual(late["status"], "late")
        self.assertEqual(result["jev"]["status"], "pending")
        self.assertNotIn("hit", result["jev"])

    def test_pacing_rebuilds_completed_prefix_and_is_not_a_five_round_gate(self):
        from norn.pacing import trace, validate_trace, trace_extends
        full = trace(self.journal, DECK)
        first_end = next(i for i, e in enumerate(self.journal["events"]) if e["type"] == "round_completed")
        prefix = {"schema_version": self.journal["schema_version"], "events": self.journal["events"][:first_end + 1]}
        earlier = trace(prefix, DECK)
        self.assertTrue(trace_extends(earlier, full))
        self.assertEqual(validate_trace(full, self.journal), full)
        self.assertEqual(len(full["decisions"]), 3)
        changed = deepcopy(full)
        changed["decisions"][0]["offer_probability"] = .99
        with self.assertRaises(ValueError):
            validate_trace(changed, self.journal)
        self.assertEqual(self.service.pacing({"journal": prefix, "product": PRODUCT}), earlier)

    def test_recomputed_hash_cannot_hide_forged_scores_and_observation_is_bounded(self):
        from norn.finale import commitment_for
        public = self.service.finale_start(self.body)
        payload = {"challenge_id": public["challenge_id"], "card_id": public["candidate_ids"][0],
                   "elapsed_ms": 1000, "observation": {"ready_offset_ms": 200, "hidden_ms": 100,
                   "layout": [[0, 0], [0, 1], [1, 0], [1, 1]], "input": "touch"}}
        result = self.service.finale_choose(payload)
        self.assertEqual(validate_result(result, self.journal), result)
        for field, value in (("probabilities", [.25] * 4), ("profile_hash", "sha256:" + "0" * 64),
                             ("candidate_ids", [{}, {}, {}, {}])):
            with self.subTest(field=field):
                changed = deepcopy(result)
                changed[field] = value
                changed["commitment"] = commitment_for(changed)
                with self.assertRaises(ValueError):
                    validate_result(changed, self.journal)
        for mutation in ({"ready_offset_ms": 1001}, {"hidden_ms": 801},
                         {"layout": [[0, 0]] * 4}, {"input": "private-identifier"}):
            with self.subTest(observation=mutation):
                changed = deepcopy(result)
                changed["observation"].update(mutation)
                with self.assertRaises(ValueError):
                    validate_result(changed, self.journal)

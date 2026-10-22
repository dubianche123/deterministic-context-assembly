"""The game format must not overwrite research cohorts or sealed choices."""

from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from norn.actor_state import dump_actor_state, new_actor_service, next_expiry
from norn.collection import SQLiteCollector, build_partial, build_record, visitor_id
from norn.events import canonical_json, content_hash
from norn.finale import create as create_finale, finish as finish_finale
from norn.pacing import trace as pacing_trace
from norn.product import (MAX_EXPLORATION_ROUNDS, PACING_POLICY_VERSION, PRODUCT_VERSION,
                          validate_product)
from norn.research_inspection import build_bundle
from norn.service import RequestError
from tests.test_partial_records import KEY, VISITOR, prefix
from tests.test_research_deck import DECK, research, session


PRODUCT = {"schema_version": PRODUCT_VERSION, "pacing_policy": PACING_POLICY_VERSION}


def feedback(mode="off"):
    return {"schema_version": "norn.experience.v1", "round_feedback": mode, "exposures": []}


class LastCardArchiveTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = SQLiteCollector(Path(directory.name) / "records.sqlite3")
        self.addCleanup(self.store.close)
        self.journal = session(research.RANDOM, rounds=3, keep=lambda _: False)
        self.bundle = build_bundle(self.journal, DECK)
        self.fields = {"visitor": visitor_id(VISITOR), "deployment": "cloudflare",
                       "experiments": {"session_key": KEY, "deck": DECK.name}}

    def partial(self, journal=None, **fields):
        return build_partial(journal or prefix(self.journal, 1), session=KEY,
                             **{**self.fields, "product": PRODUCT, **fields})

    def full(self, **fields):
        return build_record(self.bundle, {"text": "A fictional character reading", "simulated": True},
                            **{**self.fields, "product": PRODUCT, **fields})

    def test_product_is_an_explicit_policy_not_a_claim_of_five_rounds(self):
        self.assertEqual(MAX_EXPLORATION_ROUNDS, 12)
        value = validate_product(PRODUCT, self.journal)
        self.assertEqual(value, PRODUCT)
        self.assertIsNot(value, PRODUCT)
        self.assertNotIn("exploration_rounds", value)
        for invalid in (None, {}, {**PRODUCT, "exploration_rounds": 5},
                        {**PRODUCT, "pacing_policy": "norn.pacing.future"},
                        {**PRODUCT, "schema_version": "norn.last-card.future"},
                        {**PRODUCT, "pacing_policy": True}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_product(invalid, self.journal)
        with self.assertRaises(ValueError):
            validate_product(PRODUCT, {"events": None})

    def test_full_and_partial_route_product_separately_without_changing_engine_facts(self):
        original = deepcopy(self.bundle)
        for mode in (None, "off", "on"):
            experience = feedback(mode) if mode is not None else None
            expected = "cloudflare.last-card.v1" + (".round-feedback.v1" if mode == "on" else "")
            for kind, record in (("partial", self.partial(experience=experience)),
                                 ("full", self.full(experience=experience))):
                with self.subTest(kind=kind, feedback=mode):
                    self.assertEqual(record["product"], PRODUCT)
                    self.assertEqual(record["deployment"], expected)
                    self.assertEqual(record["source_deployment"], "cloudflare")
                    if kind == "full":
                        self.assertEqual(record["journal"], original["journal"])
                        self.assertEqual(record["profile_hash"], original["profile"]["profile_hash"])
                        self.assertEqual(record["events_hash"], original["profile"]["events_hash"])
        self.assertEqual(self.bundle, original)

    def test_legacy_builders_retain_their_field_shape_and_original_cohorts(self):
        for mode in (None, "off", "on"):
            experience = feedback(mode) if mode is not None else None
            for record in (self.partial(product=None, experience=experience),
                           self.full(product=None, experience=experience)):
                with self.subTest(feedback=mode, schema=record["schema_version"]):
                    self.assertNotIn("product", record)
                    self.assertNotIn("finale", record)
                    self.assertEqual(record["deployment"], "cloudflare.round-feedback.v1" if mode == "on"
                                     else "cloudflare")
                    self.assertEqual("source_deployment" in record, mode == "on")

    def test_shared_archive_boundary_rejects_cohort_forgery_and_invalid_product(self):
        source = self.partial()
        for changes in ({"deployment": "cloudflare"}, {"source_deployment": "other-host"},
                        {"source_deployment": None}, {"product": None},
                        {"product": {**PRODUCT, "pacing_policy": "unsupported"}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.store.submit_partial({**source, **changes})
        self.assertEqual(self.store.page(), [])

    def test_terminal_and_pacing_sidecars_require_a_product_version(self):
        for fields in ({"finale": {}}, {"pacing": {}}):
            with self.subTest(fields=fields):
                with self.assertRaises(ValueError):
                    self.partial(product=None, **fields)
                with self.assertRaises(ValueError):
                    self.full(product=None, **fields)

    def test_product_cannot_appear_disappear_or_change_source_during_a_visit(self):
        older = self.partial()
        self.assertTrue(self.store.submit_partial(older))
        for later in (self.partial(prefix(self.journal, 2), product=None),
                      self.partial(prefix(self.journal, 2), deployment="different-host")):
            self.assertFalse(self.store.submit_partial(later))
        self.assertEqual(self.store.page()[0]["journal"], older["journal"])
        self.assertTrue(self.store.withdraw(KEY, visitor_id(VISITOR)))
        # A distinct visit that starts without a format cannot be silently
        # reclassified to the new game after data has already been archived.
        other = "a" * 32
        legacy = build_partial(prefix(self.journal, 1), session=other, **self.fields)
        self.assertTrue(self.store.submit_partial(legacy))
        changed = build_partial(prefix(self.journal, 2), session=other, product=PRODUCT, **self.fields)
        self.assertFalse(self.store.submit_partial(changed))
        self.assertNotIn("product", self.store.page()[0])

    def test_sealed_exploration_is_still_partial_until_reading_replaces_same_row(self):
        pending = self.partial(self.journal)
        self.assertEqual(pending["journal"]["events"][-1]["type"], "session_completed")
        self.assertTrue(self.store.submit_partial(pending))
        self.assertEqual(self.store.stats()["partial_records"], 1)
        self.assertIsNotNone(self.store.submit(self.full()))
        [saved] = self.store.page()
        self.assertEqual((saved["schema_version"], saved["record_id"], saved["visit"]),
                         ("norn.record.v2", KEY, 1))
        self.assertEqual(saved["product"], PRODUCT)
        self.assertEqual(self.store.stats()["partial_records"], 0)
        self.assertFalse(self.store.submit_partial(pending))

    def test_finale_sidecar_cannot_be_erased_rewritten_or_lost_on_a_same_journal_retry(self):
        private = create_finale(self.journal, DECK, "archive-game")
        one = finish_finale(private, private["candidate_ids"][0], 1200)
        two = finish_finale(private, private["candidate_ids"][1], 1300)
        initial = self.partial(self.journal)
        settled = self.partial(self.journal, finale=one)
        self.assertTrue(self.store.submit_partial(initial))
        self.assertTrue(self.store.submit_partial(settled))
        self.assertEqual(self.store.page()[0]["finale"], one)
        self.assertTrue(self.store.submit_partial(settled))
        self.assertFalse(self.store.submit_partial(initial))
        self.assertFalse(self.store.submit_partial(self.partial(self.journal, finale=two)))
        self.assertIsNone(self.store.submit(self.full()))
        self.assertIsNotNone(self.store.submit(self.full(finale=one)))
        [saved] = self.store.page()
        self.assertEqual(saved["finale"], one)
        self.assertEqual(saved["visit"], 1)
        self.assertFalse(self.store.submit_partial(settled))

    def test_pacing_facts_follow_the_same_history_and_survive_finale_and_reading(self):
        earlier_journal, later_journal = prefix(self.journal, 1), prefix(self.journal, 2)
        earlier_trace, later_trace = pacing_trace(earlier_journal), pacing_trace(later_journal)
        before = self.partial(earlier_journal, pacing=earlier_trace)
        after = self.partial(later_journal, pacing=later_trace)
        self.assertTrue(self.store.submit_partial(before))
        self.assertTrue(self.store.submit_partial(after))
        self.assertFalse(self.store.submit_partial(before))
        self.assertFalse(self.store.submit_partial(self.partial(later_journal)))
        forged = deepcopy(after)
        forged["pacing"]["decisions"][0]["offer_probability"] = .123
        with self.assertRaises(ValueError):
            self.store.submit_partial(forged)
        private = create_finale(self.journal, DECK, "pacing-game")
        result = finish_finale(private, private["candidate_ids"][0], 1000)
        complete_trace = pacing_trace(self.journal)
        terminal = self.partial(self.journal, finale=result, pacing=complete_trace)
        self.assertTrue(self.store.submit_partial(terminal))
        self.assertIsNone(self.store.submit(self.full(finale=result)))
        self.assertIsNotNone(self.store.submit(self.full(finale=result, pacing=complete_trace)))
        [saved] = self.store.page()
        self.assertEqual(saved["pacing"], complete_trace)
        self.assertEqual(saved["finale"], result)
        self.assertEqual(saved["visit"], 1)

    def test_withdrawal_tombstones_cover_product_finale_and_future_partials(self):
        for forget in (False, True):
            with self.subTest(forget=forget):
                identity = "b" * 32 if forget else KEY
                record = build_partial(prefix(self.journal, 1), session=identity, product=PRODUCT, **self.fields)
                self.assertTrue(self.store.submit_partial(record))
                if forget:
                    self.assertTrue(self.store.forget(visitor_id(VISITOR)))
                else:
                    self.assertTrue(self.store.withdraw(identity, visitor_id(VISITOR)))
                self.assertFalse(self.store.submit_partial(record))
                self.assertEqual(self.store.page(), [])

    def test_frozen_live_reader_excludes_the_game_with_every_feedback_setting(self):
        root = Path(__file__).resolve().parents[1]
        checkout = root.parent / "norn-research"
        if not (checkout / "research/norn_live/evaluate.py").is_file() or importlib.util.find_spec("numpy") is None:
            self.skipTest("Optional read-only research checkout and numpy are required")
        code = r'''
import json,sys
sys.path[:0]=[sys.argv[1],sys.argv[2]]
from research.norn_live import evaluate as live
from norn.collection import build_partial,build_record
from norn.research_inspection import build_bundle
from tests.test_last_card_archive import PRODUCT,feedback
from tests.test_partial_records import prefix
from tests.test_research_deck import DECK,research,session
journal=session(research.RANDOM,rounds=3,keep=lambda _:False)
bundle=build_bundle(journal,DECK)
records=[]
for kind in ['full','partial']:
    for variant,mode in [('game',None),('game','off'),('game','on'),('legacy','off')]:
        identity=f'{len(records)+1:032x}'
        fields={'visitor':identity,'deployment':'cloudflare','experiments':{'deck':'pamela-v1'},
                'product':PRODUCT if variant=='game' else None,
                'experience':feedback(mode) if mode is not None else None}
        records.append(build_record(bundle,{},**fields) if kind=='full' else
                       build_partial(prefix(journal,2),session=identity,**fields))
full,partial,reasons=live.eligible(records,decks={DECK.label:DECK})
print(json.dumps({'full':len(full),'partial':len(partial),'reasons':reasons}))
'''
        result = subprocess.run([sys.executable, "-c", code, str(root), str(checkout)],
                                cwd=root, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"full": 1, "partial": 1,
                                                   "reasons": {"other_deployment": 6}})


class LastCardActorTests(unittest.TestCase):
    def test_actual_challenge_and_first_choice_survive_eviction_before_any_reading(self):
        clock = lambda: 1000
        journal = session(research.RANDOM, rounds=3, keep=lambda _: False)
        request = {"journal": journal, "challenge_id": "evicted-game", "product": PRODUCT}
        service = new_actor_service(clock=clock, deck="pamela-v1")
        with patch.object(service, "generate", side_effect=AssertionError("No reading model should run")):
            public = service.finale_start(request)
        self.assertNotIn("predicted_card_id", public)
        self.assertNotIn("salt", public)
        self.assertNotIn("probabilities", public)
        state = dump_actor_state(service)
        self.assertEqual(state["sessions"], [])
        restored = new_actor_service(json.loads(canonical_json(state)), clock=clock, deck="pamela-v1")
        self.assertEqual(restored.finale_start(request), public)
        choice = {"challenge_id": public["challenge_id"], "card_id": public["candidate_ids"][0], "elapsed_ms": 1250}
        with patch.object(restored, "generate", side_effect=AssertionError("No reading model should run")):
            result = restored.finale_choose(choice)
        self.assertEqual(result["commitment"], public["commitment"])
        self.assertEqual(result["chosen_card_id"], choice["card_id"])
        self.assertEqual(result["hit"], result["chosen_card_id"] == result["predicted_card_id"])
        state = dump_actor_state(restored)
        self.assertEqual(state["sessions"], [])
        restored = new_actor_service(json.loads(canonical_json(state)), clock=clock, deck="pamela-v1")
        self.assertEqual(restored.finale_choose(choice), result)
        other = {**choice, "card_id": public["candidate_ids"][1]}
        with self.assertRaises(RequestError) as mismatch:
            restored.finale_choose(other)
        self.assertEqual(mismatch.exception.status, 409)
        self.assertEqual(restored.health()["limits"]["model_calls_reserved"], 0)

    def test_private_commit_and_first_choice_survive_eviction_without_a_reading_session(self):
        now = [1000]
        clock = lambda: now[0]
        service = new_actor_service(clock=clock, deck="pamela-v1")
        private = {"predicted_card_id": "PAM_0001", "salt": "server-only-until-reveal",
                   "candidate_ids": ["PAM_0001", "PAM_0002", "PAM_0003", "PAM_0004"]}
        result = {"chosen_card_id": "PAM_0002", "predicted_card_id": "PAM_0001", "hit": False}
        service.operations.execute("finale", "game-1", content_hash({"warmup": 1}), lambda: private)
        service.operations.execute("finale-choice", "game-1", content_hash({"choice": 2}), lambda: result)
        original = dump_actor_state(service)
        self.assertEqual(original["sessions"], [])
        now[0] += 60
        restored = new_actor_service(json.loads(canonical_json(original)), clock=clock, deck="pamela-v1")
        self.assertEqual(dump_actor_state(restored), original)
        self.assertEqual(next_expiry(original), 2800)
        with patch.object(restored, "generate", side_effect=AssertionError("No model should be called")):
            for scope, fingerprint, expected in (("finale", {"warmup": 1}, private),
                                                 ("finale-choice", {"choice": 2}, result)):
                self.assertEqual(restored.operations.execute(scope, "game-1", content_hash(fingerprint),
                                                            lambda: self.fail("sealed operation reran")), expected)
        with self.assertRaises(RequestError) as mismatch:
            restored.operations.execute("finale-choice", "game-1", content_hash({"choice": 3}), lambda: {})
        self.assertEqual(mismatch.exception.status, 409)
        now[0] = 2801
        expired = dump_actor_state(restored)
        self.assertEqual(expired["operations"], [])
        self.assertIsNone(next_expiry(expired))


if __name__ == "__main__":
    unittest.main()

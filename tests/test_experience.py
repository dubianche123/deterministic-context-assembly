"""UI feedback observations cannot rewrite visits or influence frozen learning."""

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

from norn.actor_state import new_actor_service
from norn.collection import CONSENT_VERSION, SQLiteCollector, build_partial, visitor_id
from norn.events import MAX_DURATION_MS
from norn.experience import (EXPERIENCE_VERSION, classify_round_feedback,
                             filter_explicit_feedback_off, validate_experience)
from norn.service import RequestError
from tests.test_partial_records import KEY, VISITOR, prefix
from tests.test_research_deck import research, session


def experience(mode="on", *rounds):
    return {"schema_version": EXPERIENCE_VERSION, "round_feedback": mode,
            "exposures": [{"after_round": number, "feedback": "unscored", "elapsed_ms": number * 1000}
                          for number in rounds]}


class ExperienceValidationTests(unittest.TestCase):
    def setUp(self):
        self.journal = session(research.RANDOM, rounds=3, keep=lambda _: False)

    def test_exposure_is_a_bounded_observation_not_a_quality_claim(self):
        value = experience("on", 1, 2)
        value["exposures"][0]["feedback"] = "hit"  # no kept card: labels describe displayed UI, not quality
        saved = validate_experience(value, self.journal)
        self.assertEqual(saved, value)
        value["exposures"].clear()
        self.assertEqual(len(saved["exposures"]), 2)
        self.assertEqual(validate_experience(experience("off"), self.journal), experience("off"))

    def test_invalid_modes_unclosed_rounds_duplicates_and_clock_rewinds_are_rejected(self):
        valid = experience("on", 1, 2)
        invalid = [None, {**valid, "schema_version": "future"}, {**valid, "extra": True},
                   {**valid, "round_feedback": "unspecified"}, {**valid, "round_feedback": "off"},
                   {**valid, "exposures": [valid["exposures"][0]] * 2}, experience("on", 4)]
        for changes in ({"after_round": True}, {"after_round": 0}, {"feedback": "unknown"},
                        {"elapsed_ms": True}, {"elapsed_ms": -1}, {"elapsed_ms": MAX_DURATION_MS + 1},
                        {"extra": "unsupported"}):
            row = {**valid["exposures"][0], **changes}
            invalid.append({**valid, "exposures": [row]})
        invalid.append({**valid, "exposures": [valid["exposures"][0],
                                               {**valid["exposures"][1], "elapsed_ms": 999}]})
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_experience(value, self.journal)
        with self.assertRaises(ValueError):
            validate_experience(experience("on", 1), prefix(self.journal, 0))
        with self.assertRaises(ValueError):
            validate_experience(experience("off"), None)

    def test_analysis_preprocessing_never_treats_unknown_or_malformed_visits_as_off(self):
        rows = [{"journal": self.journal},
                {"journal": self.journal, "experience": experience("off")},
                {"journal": self.journal, "experience": experience("on", 1)},
                {"journal": self.journal, "experience": None},
                {"journal": self.journal, "experience": experience("off", 1)}]
        original = deepcopy(rows)
        self.assertEqual([classify_round_feedback(row) for row in rows],
                         ["unspecified", "off", "on", "invalid", "invalid"])
        kept, excluded = filter_explicit_feedback_off(rows)
        self.assertEqual(kept, [rows[1]])
        self.assertEqual(excluded, {"on": 1, "unspecified": 1, "invalid": 2})
        self.assertEqual(rows, original)


class ExperienceCollectionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = SQLiteCollector(Path(directory.name) / "records.sqlite3")
        self.addCleanup(self.store.close)
        self.service = new_actor_service(clock=lambda: 1000, deck="pamela-v1")
        self.service.collection = self.store
        self.journal = session(research.RANDOM, rounds=3, keep=lambda _: False)

    def body(self, journal, **fields):
        return {"consent": CONSENT_VERSION, "visitor": VISITOR, "session_key": KEY,
                "journal": journal, **fields}

    def test_same_journal_accepts_exposure_append_but_rejects_erasure_and_mode_changes(self):
        body = self.body(prefix(self.journal, 2), experience=experience())
        self.assertEqual(self.service.record_partial(body), {"stored": True})
        for rounds in ((1,), (1, 2)):
            newer = {**body, "experience": experience("on", *rounds)}
            self.assertEqual(self.service.record_partial(newer), {"stored": True})
        self.assertEqual(self.service.record_partial(newer), {"stored": True})
        for stale in (body, {**body, "experience": experience("off")},
                      self.body(prefix(self.journal, 2)), {**body, "experience": experience("on", 1)}):
            self.assertEqual(self.service.record_partial(stale), {"stored": False})
        changed = deepcopy(newer)
        changed["experience"]["exposures"][0]["feedback"] = "hit"
        self.assertEqual(self.service.record_partial(changed), {"stored": False})
        [record] = self.store.page()
        self.assertEqual(record["experience"], experience("on", 1, 2))
        self.assertEqual((record["visit"], record["journal"]), (1, body["journal"]))
        self.assertEqual((record["deployment"], record["source_deployment"]), ("local.round-feedback.v1", "local"))

    def test_unspecified_is_compatible_with_old_clients_but_cannot_be_reclassified_as_off(self):
        body = self.body(prefix(self.journal, 1))
        self.assertEqual(self.service.record_partial(body), {"stored": True})
        self.assertNotIn("experience", self.store.page()[0])
        self.assertEqual(self.store.page()[0]["deployment"], "local")
        self.assertNotIn("source_deployment", self.store.page()[0])
        for mode in ("on", "off"):
            self.assertEqual(self.service.record_partial({**body, "experience": experience(mode)}), {"stored": False})
        with patch("norn.service.NornService._render", side_effect=AssertionError("Must not call a model")):
            result = self.service.analyze(self.body(self.journal, request_id="old-client-full"))
        self.assertEqual(result["record"], {"record_id": KEY})
        self.assertNotIn("experience", self.store.page()[0])

    def test_full_upgrade_must_cover_prior_exposures_and_preserve_model_and_hashes(self):
        partial = self.body(prefix(self.journal, 2), experience=experience("on", 1, 2))
        self.assertEqual(self.service.record_partial(partial), {"stored": True})
        plain = self.service._prepare_analysis({"journal": self.journal})
        explicit = self.service._prepare_analysis({"journal": self.journal, "experience": experience("on", 1, 2, 3)})
        for key in ("bundle", "analysis", "context", "prompt", "generation"):
            self.assertEqual(plain[key], explicit[key], key)
        with patch("norn.service.NornService._render", side_effect=AssertionError("Must not call a model")):
            rejected = self.service.analyze(self.body(self.journal, request_id="missing-sidecar"))
            self.assertIsNone(rejected["record"])
            self.assertEqual(self.store.page()[0]["schema_version"], "norn.partial.v1")
            full = self.service.analyze(self.body(self.journal, experience=experience("on", 1, 2, 3),
                                                  request_id="complete-sidecar"))
        [record] = self.store.page()
        self.assertEqual((record["schema_version"], record["visit"], full["record"]),
                         ("norn.record.v2", 1, {"record_id": KEY}))
        self.assertEqual(record["experience"], experience("on", 1, 2, 3))
        self.assertEqual((record["deployment"], record["source_deployment"]), ("local.round-feedback.v1", "local"))
        self.assertEqual(record["profile_hash"], plain["bundle"]["profile"]["profile_hash"])
        self.assertEqual(record["events_hash"], plain["bundle"]["profile"]["events_hash"])
        self.assertEqual(self.service.record_partial(partial), {"stored": False})

    def test_invalid_sidecar_is_rejected_before_model_and_revocation_still_blocks_late_writes(self):
        for value in (None, experience("off", 1), experience("on", 4)):
            with self.subTest(value=value), patch("norn.service.NornService._render", side_effect=AssertionError("model")):
                with self.assertRaises(RequestError):
                    self.service.analyze(self.body(self.journal, experience=value))
                with self.assertRaises(RequestError):
                    self.service.record_partial(self.body(prefix(self.journal, 2), experience=value))
        body = self.body(prefix(self.journal, 2), experience=experience("on", 1, 2))
        self.assertEqual(self.service.record_partial(body), {"stored": True})
        self.service.withdraw_record({"visitor": VISITOR, "all": True})
        self.assertEqual(self.service.record_partial(body), {"stored": False})
        self.assertEqual(self.store.page(), [])

    def test_shared_store_transaction_validates_direct_sidecar_operations(self):
        record = build_partial(prefix(self.journal, 2), visitor=visitor_id(VISITOR), session=KEY,
                               experience=experience("on", 1))
        record["experience"]["exposures"][0]["after_round"] = 3  # not yet completed in the actual journal
        with self.assertRaises(ValueError):
            self.store.submit_partial(record)
        self.assertEqual(self.store.page(), [])

    def test_on_cohort_cannot_be_forged_back_to_the_original_host_label(self):
        record = build_partial(prefix(self.journal, 2), visitor=visitor_id(VISITOR), session=KEY,
                               deployment="cloudflare", experience=experience("on", 1))
        self.assertEqual((record["deployment"], record["source_deployment"]),
                         ("cloudflare.round-feedback.v1", "cloudflare"))
        for changes in ({"deployment": "cloudflare"}, {"source_deployment": "another-host"},
                        {"source_deployment": None}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.store.submit_partial({**record, **changes})
        off = build_partial(prefix(self.journal, 2), visitor=visitor_id(VISITOR), session=KEY,
                            deployment="cloudflare", experience=experience("off"))
        self.assertEqual(off["deployment"], "cloudflare")
        self.assertNotIn("source_deployment", off)
        self.assertEqual(self.store.page(), [])

    def test_unchanged_live_v3_reader_excludes_new_on_cohort_when_checkout_is_available(self):
        root = Path(__file__).resolve().parents[1]
        checkout = root.parent / "norn-research"
        if not (checkout / "research/norn_live/evaluate.py").is_file() or importlib.util.find_spec("numpy") is None:
            self.skipTest("Optional sibling research checkout and numpy are required for frozen-reader compatibility")
        # Read/import only. No freeze/analysis command, lock rewrite, bytecode
        # file or research data write is allowed in this compatibility probe.
        script = r'''
import json,sys
sys.path[:0]=[sys.argv[1],sys.argv[2]]
from research.norn_live import evaluate as live
from norn.collection import build_partial,build_record
from norn.research_inspection import build_bundle
from tests.test_experience import experience
from tests.test_partial_records import prefix
from tests.test_research_deck import DECK,research,session
journal=session(research.RANDOM,rounds=3,keep=lambda _:False)
bundle=build_bundle(journal,DECK)
records=[]
for kind in ['full','partial']:
    for mode in ['on','off',None]:
        identity=f'{len(records)+1:032x}'
        fields={'visitor':identity,'deployment':'cloudflare','experiments':{'deck':'pamela-v1'},
                'experience':experience(mode) if mode is not None else None}
        record=(build_record(bundle,{},**fields) if kind=='full' else
                build_partial(prefix(journal,2),session=identity,**fields))
        records.append(record)
full,partial,reasons=live.eligible(records,decks={DECK.label:DECK})
mode=lambda row:row['record'].get('experience',{}).get('round_feedback','unspecified')
print(json.dumps({'full':[mode(row) for row in full], 'partial':[mode(row) for row in partial],
                  'reasons':reasons}))
'''
        result = subprocess.run([sys.executable, "-c", script, str(root), str(checkout)], cwd=root,
                                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"full": ["off", "unspecified"],
            "partial": ["off", "unspecified"], "reasons": {"other_deployment": 2}})


if __name__ == "__main__":
    unittest.main()

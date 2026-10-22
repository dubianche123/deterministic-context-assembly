"""Exact numerical contracts; golden hashes were checked in native and WASM Python."""

import asyncio
from copy import deepcopy
from decimal import localcontext, ROUND_DOWN
import json
import math
from pathlib import Path
import unittest

from norn.benchmark import default_journal
from norn.core import engine_module
from norn.events import content_hash
from norn.profile import build_event_profile, verify_profile
from norn.replay import replay_document


numeric = engine_module("numeric")
model = engine_module("preference_model")


def edge_journal(total, edge_round):
    """Includes a round-decay argument where native and WASM libm disagree."""
    events = []
    def add(kind, **fields):
        events.append({"sequence": len(events) + 1, "event_id": "portable-" + str(len(events) + 1),
                       "type": kind, **fields})
    for number in range(1, total + 1):
        ids = [f"NORN_{(number - 1) * 3 + index:04}" for index in (1, 2, 3)]
        add("round_started", round=number, card_ids=ids)
        if number in (1, edge_round, total):
            add("card_selected", round=number, card_id=ids[0])
        add("round_completed", round=number, duration_ms=1000 + number * 131)
    add("session_completed")
    return {"schema_version": "norn.events.v1", "events": events}


class NumericalPortabilityTests(unittest.TestCase):
    def test_portable_transcendentals_ignore_ambient_decimal_settings(self):
        expected = [("exp", 25 / 59, "0x1.8713e40f2add7p+0"),
                    ("exp", 19 / 75, "0x1.49cedb7bec68ep+0"),
                    ("sqrt", 2, "0x1.6a09e667f3bcdp+0"),
                    ("log", 2, "0x1.62e42fefa39efp-1"),
                    ("log1p", 0.093, "0x1.6c3de383f4917p-4")]
        with localcontext() as ambient, numeric.use(numeric.PORTABLE_CONTRACT):
            ambient.prec, ambient.rounding = 4, ROUND_DOWN
            for operation, value, hexadecimal in expected:
                self.assertEqual(getattr(numeric, operation)(value).hex(), hexadecimal)

    def test_nested_scope_and_exception_restore_the_numerical_contract(self):
        self.assertIsNone(numeric.current_contract())
        with numeric.use(numeric.PORTABLE_CONTRACT):
            with self.assertRaises(RuntimeError):
                with numeric.use(numeric.NATIVE_CONTRACT):
                    self.assertEqual(numeric.exp(25 / 59), math.exp(25 / 59))
                    raise RuntimeError("leave historical scope")
            self.assertEqual(numeric.current_contract(), numeric.PORTABLE_CONTRACT)
        self.assertIsNone(numeric.current_contract())

    def test_portable_profile_covers_both_model_and_reference_ledger(self):
        # Scoring v3 keeps its golden hash; v4 is covered in test_choice_model.
        profile = build_event_profile(default_journal(), algorithm_version="norn.scoring.v3")
        self.assertEqual(profile["schema_version"], "norn.profile.v3")
        self.assertEqual(profile["algorithm_version"], "norn.scoring.v3")
        self.assertEqual(profile["numerical_contract"], numeric.PORTABLE_CONTRACT)
        self.assertEqual(profile["preference_model"]["model_version"], model.MODEL_VERSION)
        attribution = profile["analysis"]["attribution"]
        self.assertEqual(attribution["schema_version"], "norn.attribution.v2")
        self.assertEqual(attribution["numerical_contract"], numeric.PORTABLE_CONTRACT)
        self.assertEqual(profile["profile_hash"], "sha256:a4069f784f83ca0cdaa9799c0a6734cc13c9a2f9fe9302a7f44c5b4a39aaf274")
        self.assertTrue(verify_profile(profile, default_journal()))

    def test_legacy_profiles_and_model_versions_keep_native_math_dispatch(self):
        source = default_journal()
        old = build_event_profile(source, algorithm_version="norn.scoring.v2")
        self.assertEqual(old["schema_version"], "norn.profile.v2")
        self.assertEqual(old["preference_model"]["model_version"], model.LEGACY_MODEL_VERSION)
        self.assertNotIn("numerical_contract", old)
        with numeric.use(numeric.PORTABLE_CONTRACT):
            self.assertTrue(verify_profile(old, source))
            self.assertTrue(model.verify_checkpoint(old["preference_model"], journals=[source]))
        fixture = Path(__file__).parent / "fixtures" / "legacy-profile-v1.json"
        original = json.loads(fixture.read_text())
        self.assertEqual(replay_document(original), original)

    def test_round_decay_edges_have_shared_native_and_wasm_golden_hashes(self):
        cases = [(59, 25, "sha256:733ef03d20dca0debfa63846fb87bf0ee44bcae621b83b95a25dd4e8641497fe"),
                 (75, 19, "sha256:73c30e1bb00962f78b7d509e385ec9ac2f7f6358fa14c673d20d428062febf2b")]
        for total, edge_round, expected_hash in cases:
            with self.subTest(total=total, edge_round=edge_round):
                source = edge_journal(total, edge_round)
                profile = build_event_profile(source, algorithm_version="norn.scoring.v3")
                self.assertEqual(profile["profile_hash"], expected_hash)
                self.assertTrue(verify_profile(profile, source))

    def test_contract_or_rehashed_fitted_weight_tampering_still_fails(self):
        source = default_journal()
        profile = build_event_profile(source, algorithm_version="norn.scoring.v3")
        broken = deepcopy(profile)
        broken["numerical_contract"] = numeric.NATIVE_CONTRACT
        broken["profile_hash"] = content_hash({key: value for key, value in broken.items() if key != "profile_hash"})
        with self.assertRaisesRegex(ValueError, "numerical contract"):
            verify_profile(broken, source)
        checkpoint = deepcopy(profile["preference_model"])
        checkpoint["weights"][0] += 0.001
        checkpoint["checkpoint_hash"] = content_hash({key: value for key, value in checkpoint.items() if key != "checkpoint_hash"})
        with self.assertRaisesRegex(ValueError, "sources or fitted"):
            model.verify_checkpoint(checkpoint, journals=[source])


class NumericalTaskIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_tasks_keep_separate_contracts(self):
        async def observe(contract):
            with numeric.use(contract):
                await asyncio.sleep(0)
                return numeric.current_contract()
        observed = await asyncio.gather(observe(numeric.NATIVE_CONTRACT), observe(numeric.PORTABLE_CONTRACT))
        self.assertEqual(observed, [numeric.NATIVE_CONTRACT, numeric.PORTABLE_CONTRACT])
        self.assertIsNone(numeric.current_contract())

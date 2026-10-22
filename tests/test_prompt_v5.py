"""Prompt v5: card motifs instead of pixel components, chosen per session."""

import json
import unittest
from unittest.mock import patch

from norn.context import (PROMPT_V4, PROMPT_V5, PROMPT_VERSION, build_analysis_prompt, build_dialogue_prompt,
                          build_session_context)
from norn.core import engine_module
from norn.profile import export_replay
from norn.service import NornService
from norn.simulate import simulate_records
from tests.test_collection import CONSENT, render


def profile(rounds=12, seed=7):
    return export_replay(simulate_records(1, visits=(1, 1), rounds=(rounds, rounds), seed=seed)[0]["journal"])["profile"]


class PromptV5Tests(unittest.TestCase):
    def test_v5_speaks_in_motifs_grounded_in_picks(self):
        versioned = profile()
        cards = engine_module("fast_thinker")._load_cards()
        picked = {row["card_id"] for row in versioned["analysis"]["attribution"]["selections"]}
        picked_motifs = {motif for card in picked for motif in cards[card]["motifs"]}
        context = build_session_context(versioned, PROMPT_V5)
        self.assertEqual((context["prompt_version"], context["block"]["prompt_version"]), (PROMPT_V5, PROMPT_V5))
        preference = context["block"]["preference"]
        self.assertTrue(preference["drawn_to"])
        for row in preference["drawn_to"]:
            self.assertIn(row["motif"], picked_motifs)
            self.assertGreaterEqual(row["picked"], 1)
        self.assertNotIn("passed_over", preference)  # unseen cards cannot be a contrast
        picked_scenes = {cards[card]["scene"] for card in picked}
        self.assertFalse({row["scene"] for row in preference["next_to_try"]} & picked_scenes)
        self.assertIsInstance(preference["evidence_clarity"], str)
        for word in ("随机", "验证", "基线", "模型"):
            self.assertNotIn(word, preference["evidence_clarity"])
        self.assertNotIn("画面视觉成分", context["canonical_block"])
        prompt = build_analysis_prompt(context)
        self.assertIn("drawn_to", prompt["messages"][-1]["content"])
        self.assertEqual(prompt["cache"]["prompt_version"], PROMPT_V5)
        history = prompt["messages"] + [{"role": "assistant", "content": "一段判词。"}]
        build_dialogue_prompt(context, history, "为什么？", 1, 2)

    def test_default_is_v5_v4_stays_selectable_and_unknown_versions_fail(self):
        versioned = profile()
        self.assertEqual(PROMPT_VERSION, PROMPT_V5)
        self.assertEqual(build_session_context(versioned)["context_hash"],
                         build_session_context(versioned, PROMPT_V5)["context_hash"])
        old = build_session_context(versioned, PROMPT_V4)
        self.assertEqual(old["prompt_version"], PROMPT_V4)
        self.assertIn("top_features", old["block"]["preference"])
        with self.assertRaises(ValueError):
            build_session_context(versioned, "norn.prompt.v9")

    def test_v5_is_deterministic(self):
        versioned = profile(seed=8)
        self.assertEqual(build_session_context(versioned, PROMPT_V5)["context_hash"],
                         build_session_context(versioned, PROMPT_V5)["context_hash"])

    def test_reading_prompt_arm_picks_the_projection_and_is_recorded(self):
        from norn.collection import OutboxCollector
        service = NornService(generate=render, collector=OutboxCollector())
        journal = simulate_records(1, visits=(1, 1), rounds=(8, 8), seed=4)[0]["journal"]
        arms = {"reading_prompt": {"arms": (PROMPT_V5, PROMPT_V5 + "-twin"), "weights": (1, 1e-12)}}
        with patch.dict("norn.experiments.EXPERIMENTS", arms, clear=True):
            result = service.analyze({"journal": journal, **CONSENT})
        session = service.sessions.get(result["session_id"])
        self.assertEqual(session.context["prompt_version"], PROMPT_V5)
        [put] = service.collection.drain()
        self.assertEqual(put["record"]["generation"]["prompt_version"], PROMPT_V5)
        self.assertEqual(put["record"]["experiments"], {"reading_prompt": PROMPT_V5})
        self.assertNotIn("画面视觉成分", json.dumps(session.context, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()

"""Language is frozen with the reading, independent of the scored journal."""

from copy import deepcopy
import json
from pathlib import Path
import re
import unittest

from norn.actor_state import dump_actor_state, new_actor_service
from norn.context import build_analysis_prompt, build_session_context
from norn.profile import build_event_profile
from norn.service import NornService, RequestError


JOURNAL = json.loads((Path(__file__).parent / "fixtures" / "replay-session.json").read_text())


class LanguageTests(unittest.TestCase):
    def test_context_language_changes_render_identity_but_not_profile(self):
        profile = build_event_profile(JOURNAL)
        default = build_session_context(profile)
        self.assertEqual(default, build_session_context(profile, language="zh"))
        self.assertNotIn("language", default["block"])
        english = build_session_context(profile, language="en")
        self.assertEqual(default["profile_hash"], english["profile_hash"])
        self.assertNotEqual(default["context_hash"], english["context_hash"])
        prompt = build_analysis_prompt(english)
        self.assertEqual(prompt["language"], "en")
        self.assertIn("English", prompt["system"])
        self.assertIn("English words", english["block"]["style"])
        for invalid in (None, "fr", "en-US", 1, [], {}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                build_session_context(profile, language=invalid)

    def test_english_mock_reading_and_followup_use_frozen_language(self):
        service = NornService()
        result = service.analyze({"journal": JOURNAL, "language": "en", "request_id": "reading-en"})
        self.assertEqual(result["language"], "en")
        self.assertEqual(result["provider_calls"], 0)
        self.assertFalse(re.search(r"[\u3400-\u9fff]", result["reading"]))
        session = service.sessions.get(result["session_id"])
        initial = deepcopy(session.prompt)
        question = "Why do you think these pictures caught my attention? " * 3
        reply = service.dialogue({"session_id": result["session_id"], "user_message": question,
                                  "language": "zh", "turn_id": "followup-en"})
        self.assertEqual(reply["language"], "en")
        self.assertFalse(re.search(r"[\u3400-\u9fff]", reply["reply"]))
        self.assertEqual(session.prompt["system"], initial["system"])
        self.assertEqual(session.prompt["cache"], initial["cache"])
        self.assertEqual(session.prompt["messages"][:len(initial["messages"])], initial["messages"])
        self.assertEqual(service.analyze({"journal": JOURNAL, "language": "en", "request_id": "reading-en"}), result)
        with self.assertRaises(RequestError):
            service.analyze({"journal": JOURNAL, "language": "zh", "request_id": "reading-en"})

    def test_english_session_survives_actor_restore(self):
        service = new_actor_service(clock=lambda: 1000)
        result = service.analyze({"journal": JOURNAL, "language": "en", "request_id": "reading-en"})
        restored = new_actor_service(dump_actor_state(service), clock=lambda: 1001)
        reply = restored.dialogue({"session_id": result["session_id"], "user_message": "Which choice supports this?"})
        self.assertEqual(reply["language"], "en")
        self.assertIn("One checkable clue", reply["reply"])

    def test_collected_english_reading_preserves_render_language(self):
        from norn.collection import OutboxCollector
        from tests.test_collection import CONSENT
        store = OutboxCollector()
        service = NornService(collector=store)
        service.analyze({"journal": JOURNAL, "language": "en", **CONSENT})
        [operation] = store.drain()
        self.assertEqual(operation["record"]["generation"]["language"], "en")

    def test_empty_english_reading_skips_generation_and_invalid_language_is_rejected(self):
        service = NornService()
        empty = {"total_rounds": 1, "selections": [], "language": "en"}
        result = service.analyze(empty)
        self.assertTrue(result["empty_selection"])
        self.assertEqual(result["provider_calls"], 0)
        self.assertIn("did not keep any pictures", result["reading"])
        for invalid in (None, "fr", "en-US", 1, [], {}):
            with self.subTest(invalid=invalid), self.assertRaises(RequestError):
                service.analyze({"journal": JOURNAL, "language": invalid})

    def test_english_length_limit_and_localized_generation_failure(self):
        service = NornService()
        result = service.analyze({"journal": JOURNAL, "language": "en"})
        with self.assertRaisesRegex(RequestError, "400 characters"):
            service.dialogue({"session_id": result["session_id"], "user_message": "x" * 401})

        def unavailable(*args, **kwargs):
            return {"text": "暂时不可用", "status": "provider_unavailable", "usage": {}}

        failed = NornService(generate=unavailable).analyze({"journal": JOURNAL, "language": "en"})
        self.assertEqual(failed["status"], "provider_unavailable")
        self.assertFalse(re.search(r"[\u3400-\u9fff]", failed["reading"]))
        self.assertNotIn("session_id", failed)

    def test_english_guardrails_preserve_clean_sentences_and_honest_denial(self):
        from norn.guardrails import validate_output
        safe = "These pictures caught your attention this time."
        result = validate_output(safe + " Your mbti_type is INTJ.")
        self.assertEqual(result["status"], "sanitized")
        self.assertEqual(result["text"], safe)
        denial = "I cannot compare your choices with other users. " + safe
        self.assertEqual(validate_output(denial)["text"], denial)
        self.assertIn("internal_axis_values", validate_output("Your confidence score is 0.8.")["reason_codes"])


class HostedLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_hosted_language_and_long_question_survive_snapshot_and_retry(self):
        from tests.test_cloud_host import AsyncQuota, AsyncRenderer, Browser, Storage, selection
        quota_storage, actor_storage = Storage(), Storage()
        for storage in (quota_storage, actor_storage):
            self.addCleanup(storage.connection.close)
        renderer = AsyncRenderer()
        browser = Browser(actor_storage, AsyncQuota(quota_storage, lambda: 1000), renderer)
        request = {**selection(), "language": "en"}
        first = await browser.host().analyze(request)
        self.assertEqual(first["body"]["language"], "en")
        self.assertEqual(renderer.calls[0]["language"], "en")
        self.assertEqual(await browser.host().analyze(request), first)
        followup = await browser.host().dialogue({
            "session_id": first["body"]["session_id"], "turn_id": "question-en",
            "user_message": "Which pictures support your interpretation, and how much can those choices really tell me? " * 2})
        self.assertEqual(followup["body"]["language"], "en")
        self.assertEqual(followup["body"]["max_question_chars"], 400)
        self.assertEqual(renderer.calls[1]["system"], renderer.calls[0]["system"])
        self.assertEqual(len(renderer.calls), 2)


if __name__ == "__main__":
    unittest.main()

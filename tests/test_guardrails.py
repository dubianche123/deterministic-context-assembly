"""Precise output contracts keep metaphors and remove unsupported claims."""

import io
import json
import unittest
from unittest.mock import patch

from norn.core import engine_module
from norn.guardrails import validate_output, output_reason_codes, FALLBACK_TEXT


class GuardrailTests(unittest.TestCase):
    def test_normal_metaphors_and_generic_comparisons_are_allowed(self):
        for text in ("你可以慢慢寻找自己的坐标，不必比别人更快。",
                     "生活没有统一均值，你可以照自己的节奏走。",
                     "你的理想与现实有些差值，留一点空间去校准。",
                     "你比其他人想象的更有耐心，也可以允许自己停下来。",
                     "我没有读取其他用户的数据，也不会拿你和其他玩家比较。"):
            with self.subTest(text=text):
                self.assertEqual(validate_output(text)["status"], "ok")

    def test_explicit_internal_fields_and_personality_types_are_blocked(self):
        for text, reason in (("你的 E_I 为 8。", "internal_field"),
                             ("内部 coords_avg 是一个结果。", "internal_field"),
                             ("你的 T/F 差值很明显。", "internal_field"),
                             ("你是 INFJ，所以你会这样。", "internal_type"),
                             ("你的 MBTI 是 ENTP。", "internal_type"),
                             ("你的四轴分别为 8、3、-2、9。", "internal_axis_values"),
                             ("你的置信度是80%。", "internal_axis_values"),
                             ("坐标值：8.0。", "internal_axis_values")):
            with self.subTest(text=text):
                result = validate_output(text)
                self.assertEqual(result["status"], "invalid_output")
                self.assertTrue(result["blocked"])
                self.assertIn(reason, result["reason_codes"])
                self.assertEqual(result["text"], FALLBACK_TEXT)

    def test_cross_user_data_assertions_are_distinct_from_rhetoric(self):
        for text in ("你的节奏比上一位要快。", "其他玩家的数据都说明你更急。",
                     "你超过了95%的用户。", "别的测试者通常没有这种表现。",
                     "我不会拿你和其他玩家比较，但你的分数比上一位更高。"):
            self.assertIn("cross_user_data", output_reason_codes(text))
            self.assertTrue(validate_output(text)["blocked"])

    def test_clean_sentences_survive_with_original_order(self):
        before = "你在做决定之前，会先检查这件事是否仍然属于自己。"
        after = "给下一步留一点呼吸空间，也给自己一个温和的截止点。"
        result = validate_output(before + "你的 E_I 为8。" + after)
        self.assertEqual(result["status"], "sanitized")
        self.assertFalse(result["blocked"])
        self.assertEqual(result["text"], before + after)
        self.assertEqual(result["reason_codes"], ["internal_field"])

    def test_english_denial_does_not_exempt_a_later_comparison(self):
        safe = "These pictures held your attention during this visit."
        for contrast in ("but", "HOWEVER", "yet", "But"):
            claim = (f"I cannot access other users' data {contrast} "
                     "other users scored 80% lower than you.")
            with self.subTest(contrast=contrast):
                self.assertIn("cross_user_data", output_reason_codes(claim))
                self.assertTrue(validate_output(claim)["blocked"])
                cleaned = validate_output(safe + " " + claim)
                self.assertEqual(cleaned["status"], "sanitized")
                self.assertEqual(cleaned["text"], safe)

    def test_english_contrast_and_temporal_denials_remain_valid(self):
        for text in ("I cannot access other users' data but I can explain your recorded choices.",
                     "I cannot yet access other users' data.",
                     "I do not yet compare your choices with other users.",
                     "I cannot access other users' data yet."):
            with self.subTest(text=text):
                self.assertEqual(validate_output(text)["status"], "ok")

    def test_short_remainder_and_empty_output_use_deterministic_fallback(self):
        for text in ("你是INTJ。好。", "", "  ", None):
            self.assertEqual(validate_output(text)["status"], "invalid_output")
            self.assertEqual(validate_output(text)["text"], FALLBACK_TEXT)

    def test_legacy_predicate_uses_same_precise_implementation(self):
        legacy = engine_module("slow_thinker")._violates_output_guardrail
        self.assertFalse(legacy("慢慢找到自己的坐标，不必比别人更快。"))
        self.assertTrue(legacy("你在混乱中寻找秩序。你的 J/P 为3。"))

    def test_fewshot_answers_satisfy_current_guardrails(self):
        for mode, template in engine_module("template_router")._load_templates().items():
            for example in template.get("few_shot", []):
                self.assertEqual(validate_output(example["assistant"])["status"], "ok", mode)

    def test_bedrock_sanitizes_once_and_retains_actual_usage(self):
        engine = engine_module("slow_thinker")
        response = {"content": [{"text": "你是INTJ。你在推进之前会反复确认方向，也值得给下一步留些余地。"}],
                    "usage": {"input_tokens": 19, "output_tokens": 12}}
        class Client:
            calls = 0
            def invoke_model(self, **kwargs):
                self.calls += 1
                return {"body": io.BytesIO(json.dumps(response).encode())}
        client = Client()
        with patch.object(engine, "_get_client", return_value=client):
            result = engine._invoke_bedrock({"system": "fixed", "messages": [], "max_tokens": 20,
                                              "temperature": 0.5}, "fallback")
        self.assertEqual(result["status"], "sanitized")
        self.assertEqual(result["usage"], response["usage"])
        self.assertEqual(client.calls, 1)

    def test_bedrock_policy_and_transport_failures_have_different_codes(self):
        engine = engine_module("slow_thinker")
        class PolicyClient:
            def invoke_model(self, **kwargs):
                return {"body": io.BytesIO(json.dumps({"stop_reason": "guardrail_intervened",
                                                       "usage": {"input_tokens": 3}}).encode())}
        with patch.object(engine, "_get_client", return_value=PolicyClient()):
            policy = engine._invoke_bedrock({"system": "fixed", "messages": [], "max_tokens": 20,
                                               "temperature": 0.5}, "fallback")
        with patch.object(engine, "_get_client", side_effect=RuntimeError("secret-not-for-logs")), \
                patch("builtins.print") as printed:
            error = engine._invoke_bedrock({"system": "fixed", "messages": [], "max_tokens": 20,
                                              "temperature": 0.5}, "fallback")
        self.assertEqual(policy["status"], "policy_blocked")
        self.assertEqual(policy["usage"]["input_tokens"], 3)
        self.assertEqual(error["status"], "provider_unavailable")
        self.assertNotIn("secret-not-for-logs", str(printed.call_args))


if __name__ == "__main__":
    unittest.main()

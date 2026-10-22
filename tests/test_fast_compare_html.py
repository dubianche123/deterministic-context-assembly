"""Offline delivery and untrusted journal boundaries of the comparison viewer."""

from html.parser import HTMLParser
import json
import math
import unittest

from norn.fast_compare_html import render_comparison


class DocumentParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.resources = []
        self.scripts = []
        self.data = []
        self.in_data = False
        self.details = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "img", "iframe", "link"}:
            self.resources.extend(attrs[name] for name in ("src", "href") if name in attrs)
        if tag == "script":
            self.scripts.append(attrs)
            self.in_data = attrs.get("id") == "comparison-data"
        if tag == "details":
            self.details.append(attrs)

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_data = False

    def handle_data(self, data):
        if self.in_data:
            self.data.append(data)


def fixture():
    return {
        "schema_version": "norn.fast-comparison.v1",
        "provenance": {"kind": "fixture", "note": "Synthetic example"},
        "training": {"rounds": 3, "prefix_hash": "abc123"},
        "models": {
            "heuristic": {"label": "Heuristic", "status": "ok", "summary": {
                "all_nll": .4, "cards_nll": .3, "top1_accuracy": 1.0,
                "brier": .2, "decision_count": 1, "coverage": 1.0,
                "provider_calls": 0, "elapsed_ms": .12,
            }},
            "ml": {"label": "ML", "status": "ok", "summary": {}},
            "decision": {"label": "OpenAI Decisions", "status": "unavailable", "metadata": {"reason": "Missing key"}},
            "stop_only": {"label": "Reference", "status": "ok", "summary": {}},
        },
        "decisions": [{
            "id": "decision-1", "round": 4, "candidate_ids": ["NORN_0001", "NORN_0002"],
            "target": "NORN_0001", "predictions": {
                "heuristic": {"probabilities": {"NORN_0001": .7, "NORN_0002": .2, "__STOP__": .1},
                              "elapsed_ms": .12, "status": "ok"},
            },
        }],
        "limits": ["A single fixture does not measure real-world accuracy."],
    }


class FastComparisonHtmlTests(unittest.TestCase):
    def test_report_is_embedded_losslessly_without_closing_script(self):
        report = fixture()
        attack = '</script><img src="https://invalid.example/leak" onerror="alert(1)">&\u2028\u2029'
        report["provenance"]["note"] = attack
        report["models"]["decision"]["label"] = attack
        report["decisions"][0]["candidate_ids"].append(attack)
        html = render_comparison(report)
        parser = DocumentParser()
        parser.feed(html)
        self.assertEqual(json.loads("".join(parser.data)), report)
        self.assertEqual(len(parser.scripts), 2)
        self.assertEqual(parser.resources, [])
        self.assertNotIn(attack, html)
        self.assertIn("\\u003c/script\\u003e", html)
        self.assertIn("\\u2028\\u2029", html)
        self.assertIn("&lt;/script&gt;&lt;img", html)

    def test_template_tokens_in_user_content_stay_literal(self):
        report = fixture()
        token = "__REPORT_DATA____LANES____LANGUAGE____TITLE__"
        report["models"]["decision"]["label"] = token
        report["provenance"]["note"] = token
        parser = DocumentParser()
        html = render_comparison(report)
        parser.feed(html)
        self.assertEqual(json.loads("".join(parser.data)), report)
        self.assertIn(f"<h2>{token}</h2>", html)

    def test_standalone_document_has_no_resources_or_network_bootstrap(self):
        html = render_comparison(fixture(), language="en")
        parser = DocumentParser()
        parser.feed(html)
        self.assertEqual(parser.resources, [])
        self.assertNotIn("fetch(", html)
        self.assertNotIn("XMLHttpRequest", html)
        self.assertNotIn("@import", html)
        self.assertNotIn("<table", html)
        self.assertIn('<html lang="en">', html)
        self.assertEqual(len(parser.details), 2)
        self.assertTrue(all("open" not in details for details in parser.details))
        self.assertEqual(parser.details[0].get("id"), "limits-details")

    def test_unavailable_lane_has_explicit_state_and_no_substitute_data(self):
        report = fixture()
        html = render_comparison(report, language="en")
        decision_lane = html.split('data-model="decision"', 1)[1].split("</article>", 1)[0]
        self.assertIn('data-status="unavailable"', decision_lane)
        self.assertIn("<h2>OpenAI Decisions</h2>", decision_lane)
        self.assertIn("No predictions available", decision_lane)
        self.assertIn("No scores or probabilities have been substituted", decision_lane)
        self.assertNotIn("bar-fill", decision_lane)
        self.assertNotIn("0.0%", decision_lane)
        self.assertNotIn('data-model="stop_only"', html)
        parser = DocumentParser()
        parser.feed(html)
        embedded = json.loads("".join(parser.data))
        self.assertNotIn("summary", embedded["models"]["decision"])
        self.assertNotIn("decision", embedded["decisions"][0]["predictions"])

    def test_nonfinite_payload_is_rejected_instead_of_invalid_json(self):
        report = fixture()
        report["models"]["heuristic"]["summary"]["all_nll"] = math.nan
        with self.assertRaises(ValueError):
            render_comparison(report)

    def test_default_language_is_chinese_and_english_can_be_selected(self):
        self.assertIn('<html lang="zh">', render_comparison(fixture()))
        self.assertIn("三种直觉，同一组选择", render_comparison(fixture()))
        self.assertIn("One history. Three fast thinkers.", render_comparison(fixture(), language="en-US"))

    def test_local_model_names_and_first_kept_round_methodology(self):
        chinese = render_comparison(fixture())
        english = render_comparison(fixture(), language="en")
        self.assertIn("<h2>固定规则</h2>", chinese)
        self.assertIn("<h2>个体学习</h2>", chinese)
        self.assertIn("<h2>Fixed rules</h2>", english)
        self.assertIn("<h2>Personal ML</h2>", english)
        self.assertIn("<h2>OpenAI Decisions</h2>", chinese)
        self.assertIn("首张最终保留的卡片", chinese)
        self.assertIn("SAME HISTORY · SAME OPTIONS", english)
        self.assertNotIn("SAME INPUT", english)
        self.assertNotIn("相同输入", chinese)

    def test_policy_switch_keeps_three_lanes_and_both_model_records(self):
        report = fixture()
        report["models"]["ml_policy"] = {
            "label": "Guarded personal ML", "status": "ok", "summary": {"all_nll": 0.5},
            "metadata": {"guard": {"status": "baseline", "reason": "insufficient_content_evidence"}},
        }
        html = render_comparison(report, language="en")
        parser = DocumentParser()
        parser.feed(html)
        embedded = json.loads("".join(parser.data))
        self.assertEqual(embedded["models"]["ml"], report["models"]["ml"])
        self.assertEqual(embedded["models"]["ml_policy"], report["models"]["ml_policy"])
        self.assertEqual(html.count('<article class="lane '), 3)
        self.assertNotIn('data-model="ml_policy"', html)
        self.assertIn('id="ml-original"', html)
        self.assertIn('id="ml-guarded"', html)
        self.assertIn('models.ml_policy ? "ml_policy" : "ml"', html)
        self.assertIn('activeId(id)', html)
        self.assertIn("图片偏好证据不足", html)
        self.assertIn("Gain vs reference", html)
        self.assertIn('line.id === "stop_only" ? "5 5"', html)
        self.assertTrue(all("open" not in details for details in parser.details))


if __name__ == "__main__":
    unittest.main()

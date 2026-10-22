import argparse
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from norn.benchmark import (ARMS, _cost, benchmark_main, default_journal,
                            run_benchmark)


class BenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The real mock boundary executes scoring-derived prose but may never
        # make an HTTP request, even with ambient paid-provider variables.
        with patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
            cls.report = run_benchmark()

    def test_offline_experiment_is_complete_and_truthful(self):
        report = self.report
        self.assertEqual(report["schema_version"], "norn.benchmark.v1")
        self.assertEqual(report["mode"], "offline_structural")
        self.assertEqual(len(report["calls"]), 20)
        self.assertEqual(report["limits"]["actual_provider_calls"], 0)
        self.assertEqual({row["arm"] for row in report["arms"]}, set(ARMS))
        self.assertTrue(all(row["status"] == "ok" for row in report["calls"]))
        self.assertTrue(all(row["retry_count"] == 0 for row in report["calls"]))
        self.assertEqual(report["cache_observation"]["status"], "unavailable")
        self.assertIsNone(report["cache_observation"]["cache_read_input_tokens"])
        self.assertIsNone(report["total_cost_usd"])
        self.assertTrue(all(row["cost_usd"] is None for row in report["calls"]))
        self.assertTrue(all(row["measured_input_tokens"] is None for row in report["arms"]))
        json.dumps(report, allow_nan=False)

    def test_real_evidence_and_complete_native_history_are_retained(self):
        checks = self.report["context_checks"]
        self.assertTrue(checks["profile_hash_retained"])
        self.assertTrue(checks["selection_budget_respected"])
        self.assertTrue(checks["revision_budget_respected"])
        self.assertTrue(checks["evidence_has_source_events"])
        rows = [row for row in self.report["calls"] if row["arm"] == "frozen_native_session"]
        self.assertEqual({row["turn"] for row in rows}, {0, 1, 2, 3})
        for row in rows:
            self.assertTrue(row["structural_checks"]["profile_unchanged"])
            self.assertTrue(row["structural_checks"]["frozen_profile_hash_sent"])
            if row["turn"]:
                self.assertTrue(row["structural_checks"]["history_retained_as_native_prefix"])
        legacy = [row for row in self.report["calls"] if row["arm"] == "legacy_summary_session" and row["turn"]]
        self.assertTrue(all(not row["structural_checks"]["frozen_profile_hash_sent"] for row in legacy))

    def test_raw_overhead_and_repeat_prefixes_are_measured_separately(self):
        summaries = {row["arm"]: row for row in self.report["arms"]}
        self.assertGreater(summaries["raw_single"]["serialized_input_utf8_bytes"],
                           summaries["bounded_single"]["serialized_input_utf8_bytes"])
        self.assertEqual(len(self.report["shared_prefixes"]), 10)
        self.assertTrue(all(row["identical_prompt_hashes"] for row in self.report["shared_prefixes"]))
        self.assertEqual({row["phase"] for row in self.report["calls"]}, {"cold", "warm"})

    def test_real_provider_needs_explicit_live_and_dispatch_limit(self):
        for kwargs in ({"provider": "openai-compatible"}, {"live": True},
                       {"provider": "openai-compatible", "live": True},
                       {"live": True, "provider": "openai-compatible", "max_calls": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                run_benchmark(**kwargs)

    def _live(self, **kwargs):
        return run_benchmark(live=True, provider="openai-compatible", max_calls=3,
                             config={"base_url": "http://127.0.0.1:9999/v1", "model": "test-model"}, **kwargs)

    def test_live_hard_dispatch_limit_and_raw_usage(self):
        calls = []

        def fake(provider, prompt, **kwargs):
            calls.append(deepcopy(prompt))
            return {"text": "测试输出，不代表模型质量。", "status": "ok",
                    "model": "test-model-revision", "request_id": "vendor-request-123",
                    "usage": {"input_tokens": 100, "output_tokens": 20,
                              "cache_read_input_tokens": 40, "cache_creation_input_tokens": 10,
                              "raw": {"vendor_counter": 123}}}

        report = self._live(generate=fake)
        self.assertEqual(len(calls), 3)
        self.assertEqual(report["limits"]["actual_provider_calls"], 3)
        self.assertEqual(report["limits"]["stopped"], "max_calls")
        self.assertEqual(report["cache_observation"]["cache_read_input_tokens"], 120)
        self.assertEqual(report["calls"][0]["raw_usage"], {"vendor_counter": 123})
        self.assertEqual(report["calls"][0]["model"], "test-model-revision")
        self.assertEqual(report["calls"][0]["requested_model"], "test-model")
        self.assertEqual(report["calls"][0]["provider_request_id"], "vendor-request-123")
        self.assertIsNone(report["total_cost_usd"])

    def test_prices_include_read_and_write_and_missing_is_unknown(self):
        snapshot = {"provider": "openai-compatible", "model": "test-model", "as_of": "2026-10-20",
                    "usd_per_million_tokens": {"input": 1, "output": 2, "cache_read": 0.1, "cache_write": 1.25}}
        usage = {"input_tokens": 100, "output_tokens": 20,
                 "cache_read_input_tokens": 40, "cache_creation_input_tokens": 10}
        self.assertAlmostEqual(_cost(usage, snapshot), (50 + 40 + 4 + 12.5) / 1_000_000)
        del usage["cache_creation_input_tokens"]
        self.assertIsNone(_cost(usage, snapshot))
        self.assertIsNone(_cost(usage, None))
        with self.assertRaises(ValueError):
            self._live(price_snapshot={**snapshot, "model": "different"})

    def test_observed_cost_threshold_stops_when_usage_is_unknown(self):
        snapshot = {"provider": "openai-compatible", "model": "test-model", "as_of": "2026-10-20",
                    "usd_per_million_tokens": {"input": 1, "output": 2, "cache_read": 0.1, "cache_write": 1.25}}
        report = self._live(price_snapshot=snapshot, max_cost_usd=1,
                            generate=lambda *args, **kwargs: {"status": "ok", "text": "示例", "usage": {}})
        self.assertEqual(report["limits"]["actual_provider_calls"], 1)
        self.assertEqual(report["limits"]["stopped"], "usage_unavailable")
        self.assertIsNone(report["total_cost_usd"])

    def test_observed_cost_threshold_includes_cached_read_and_write(self):
        snapshot = {"provider": "openai-compatible", "model": "test-model", "as_of": "2026-10-20",
                    "usd_per_million_tokens": {"input": 1, "output": 2, "cache_read": 0.1, "cache_write": 1.25}}
        usage = {"input_tokens": 100, "output_tokens": 20,
                 "cache_read_input_tokens": 40, "cache_creation_input_tokens": 10}
        report = self._live(price_snapshot=snapshot, max_cost_usd=0.00001,
                            generate=lambda *args, **kwargs: {"status": "ok", "text": "示例", "usage": usage})
        self.assertEqual(report["limits"]["actual_provider_calls"], 1)
        self.assertEqual(report["limits"]["stopped"], "observed_cost_threshold")
        self.assertAlmostEqual(report["total_cost_usd"], 0.0001065)

    def test_failures_are_recorded_without_secret_exceptions_or_retry(self):
        def failed(*args, **kwargs):
            raise RuntimeError("SECRET_CREDENTIAL_and_endpoint")

        report = self._live(generate=failed)
        self.assertEqual(len(report["calls"]), 3)
        self.assertTrue(all(row["status"] == "generation_exception" for row in report["calls"]))
        self.assertNotIn("SECRET", json.dumps(report))
        self.assertTrue(all(row["retry_count"] == 0 for row in report["calls"]))

    def test_optional_consecutive_failure_stop_preserves_partial_report(self):
        statuses = iter(("provider_unavailable", "ok", "provider_unavailable",
                         "provider_unavailable", "provider_unavailable"))
        report = run_benchmark(live=True, provider="openai-compatible", max_calls=20,
                               stop_after_failures=3,
                               config={"base_url": "http://127.0.0.1:9999/v1", "model": "test-model"},
                               generate=lambda *args, **kwargs: {"status": next(statuses),
                                                                  "text": "测试", "usage": {}})
        self.assertEqual(report["limits"]["actual_provider_calls"], 5)
        self.assertEqual(report["limits"]["stopped"], "consecutive_failures")
        self.assertEqual(len(report["calls"]), 5)
        self.assertEqual(report["calls"][1]["status"], "ok")

    def test_short_fixture_rejected_before_generation(self):
        fixture = default_journal()
        fixture["events"] = fixture["events"][:3] + [
            {"event_id": "last", "sequence": 4, "type": "session_completed"}]
        with patch("norn.providers.generate") as render, self.assertRaises(ValueError):
            run_benchmark(fixture=fixture)
        render.assert_not_called()

    def test_output_refuses_overwrite_before_any_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text("keep", encoding="utf-8")
            args = argparse.Namespace(output=path)
            with patch("norn.benchmark.run_benchmark") as render, patch("sys.stderr", io.StringIO()):
                self.assertEqual(benchmark_main(args), 1)
            render.assert_not_called()
            self.assertEqual(path.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()

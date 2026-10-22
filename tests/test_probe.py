"""The pilot's new-image probe: frozen catalog, strict validation, consented storage, evaluation."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

try:
    import numpy  # noqa: F401
except ImportError:  # pragma: no cover - numpy is optional
    numpy = None

from norn.collection import SQLiteCollector
from norn.probe import load_probe_catalog, probe_rows, validate_probe
from norn.service import NornService, RequestError
from norn.simulate import simulate_records
from tests.test_cloudflare_archive import CloudflareArchiveTests, data
from tests.test_collection import CONSENT, journal, render

ROOT = Path(__file__).resolve().parents[1]


def probe(seed=1):
    return simulate_records(1, visits=(1, 1), rounds=(8, 10), probe="new-material-v1", seed=seed)[0]["probe"]


class ProbeCatalogTests(unittest.TestCase):
    def test_catalog_is_derived_from_the_frozen_pack_and_carries_no_labels(self):
        import subprocess
        import sys
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "prepare_probe_catalog.py"), "--check"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        catalog = load_probe_catalog("new-material-v1")
        manifest = json.loads((ROOT / "benchmarks/generalization/v1/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(catalog["pack_version"], manifest["pack_version"])
        self.assertEqual(catalog["projection_version"], manifest["projection"]["version"])
        self.assertEqual(len(catalog["cards"]), 16)
        self.assertTrue(all(set(card) == {"scene", "style", "pixel8", "pixel64"} for card in catalog["cards"].values()))
        page = json.loads((ROOT / "the-norn-machine/frontend/data/probe-new-material-v1.json").read_text())
        self.assertEqual(page["catalog_version"], catalog["catalog_version"])
        self.assertEqual({card["id"] for card in page["cards"]}, set(catalog["cards"]))


class ProbeValidationTests(unittest.TestCase):
    def test_a_simulated_pilot_probe_is_valid_and_reads_as_choice_rows(self):
        checked, _ = validate_probe(probe())
        rows = probe_rows(checked)
        self.assertEqual([len(row["shown"]) for row in rows], [4, 4, 4, 4])
        self.assertTrue(all(row["stopped"] for row in rows))

    def test_impossible_probes_are_rejected(self):
        base = probe()
        starts = [index for index, event in enumerate(base["events"]) if event["type"] == "round_started"]
        catalog = load_probe_catalog("new-material-v1")
        by_scene = {}
        for card, row in catalog["cards"].items():
            by_scene.setdefault(row["scene"], []).append(card)

        def mutated(change):
            value = deepcopy(base)
            change(value)
            return value

        first = base["events"][starts[0]]["card_ids"]
        second = base["events"][starts[1]]["card_ids"]
        lonely = sorted(by_scene)[0]
        cases = {
            "unknown schema": lambda p: p.update(schema_version="norn.probe.v0"),
            "unknown protocol": lambda p: p.update(protocol="new-material-v9"),
            "protocol array": lambda p: p.update(protocol=[]),
            "protocol object": lambda p: p.update(protocol={}),
            "protocol null": lambda p: p.update(protocol=None),
            "protocol boolean": lambda p: p.update(protocol=True),
            "other catalog": lambda p: p.update(catalog_version="sha256:" + "0" * 64),
            "extra field": lambda p: p.update(extra=1),
            "main-deck card": lambda p: p["events"][starts[0]].update(card_ids=["NORN_0001"] + first[1:]),
            "one style only": lambda p: p["events"][starts[0]].update(
                card_ids=[card for card in first if catalog["cards"][card]["style"] == "photograph"]
                + [card for card in second if catalog["cards"][card]["style"] == "photograph"]),
            "image shown twice": lambda p: p["events"][starts[1]].update(card_ids=list(first)),
            "three scenes": lambda p: p["events"][starts[0]].update(card_ids=list(first) + by_scene[lonely]),
        }
        for label, change in cases.items():
            with self.subTest(label), self.assertRaises(ValueError):
                validate_probe(mutated(change))


class ProbeServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SQLiteCollector(Path(self.tmp.name) / "records.sqlite3")
        self.service = NornService(generate=render, collector=self.store)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_a_consented_reading_stores_the_probe_beside_the_journal(self):
        sent = probe()
        result = self.service.analyze({"journal": journal(), **CONSENT, "probe": sent})
        self.assertTrue(result["record"]["record_id"])
        [record] = self.store.page()
        self.assertEqual(record["probe"], sent)
        # The probe never enters the replay bundle the visitor can export.
        self.assertNotIn("HOLDOUT_", json.dumps(self.service.export_session(result["session_id"])))

    def test_probe_needs_consent_a_real_journal_and_validity(self):
        for body in ({"journal": journal(), "probe": probe()},
                     {"journal": journal(), **CONSENT, "probe": {**probe(), "protocol": "new-material-v9"}}):
            with self.subTest(keys=sorted(body)), self.assertRaises(RequestError):
                self.service.analyze(body)
        self.assertEqual(self.store.page(), [])
        plain = self.service.analyze({"journal": journal(), **CONSENT})
        self.assertNotIn("probe", self.store.page()[0])
        self.assertTrue(plain["reading"])

    def test_invalid_protocol_types_are_client_errors_without_generation_or_storage(self):
        from unittest.mock import Mock
        generate = Mock(side_effect=AssertionError("Invalid probe must fail before generation"))
        service = NornService(generate=generate, collector=self.store)
        for value in ([], {}, None, True, 1):
            with self.subTest(protocol_type=type(value).__name__), self.assertRaises(RequestError) as caught:
                service.analyze({"journal": journal(), **CONSENT, "probe": {**probe(), "protocol": value}})
            self.assertEqual(caught.exception.status, 400)
        generate.assert_not_called()
        self.assertEqual(self.store.page(), [])


class ProbeWorkerTests(CloudflareArchiveTests):
    async def test_the_worker_accepts_the_pages_language_and_probe_fields(self):
        # The page always sends `language`; a probe rides along in pilot mode.
        sent = probe()
        response = await self.fetch("/api/analyze", "POST", {"journal": self.journal(), "request_id": "pilot-1",
                                                              "language": "en", **CONSENT, "probe": sent})
        self.assertEqual(response.status, 200, response.body)
        self.assertTrue(data(response)["record"]["record_id"])
        export = await self.fetch("/api/archive/export", headers={"Authorization": "Bearer operator-secret-token"})
        [line] = export.body.splitlines()
        self.assertEqual(json.loads(line)["probe"], sent)


for name in [name for name in dir(ProbeWorkerTests) if name.startswith("test_")
             and name != "test_the_worker_accepts_the_pages_language_and_probe_fields"]:
    setattr(ProbeWorkerTests, name, None)  # run only the new test here; the archive suite runs on its own
del CloudflareArchiveTests


@unittest.skipUnless(numpy, "norn records evaluate needs the optional numpy")
class NewMaterialEvaluationTests(unittest.TestCase):
    def test_pixel_taste_transfers_and_taste_the_images_cannot_carry_does_not(self):
        from norn.evaluate import build_evaluation
        cases = {"pixel": "pixel_taste_transfers", "motif": "transfer_not_shown"}
        for truth, verdict in cases.items():
            with self.subTest(truth):
                records = simulate_records(60, visits=(1, 1), rounds=(10, 14), truth=truth, scale=1.5,
                                           probe="new-material-v1", seed=3)
                new = build_evaluation(records, draws=100)["new_material"]
                self.assertEqual(new["decision"]["verdict"], verdict)
                self.assertEqual(new["records"], 60)
                uniform = new["models"]["uniform"]["within_scene"]["per_choice"]["value"]
                self.assertAlmostEqual(uniform, 0.6931471805599453, places=12)  # a coin flip between two styles
                self.assertEqual(set(new["unsupported"]), {"axes", "type16", "hybrid", "motif"})

    def test_few_visitors_broken_probes_and_records_without_probes(self):
        from norn.evaluate import build_evaluation, format_evaluation
        records = simulate_records(5, visits=(1, 1), rounds=(8, 10), probe="new-material-v1", seed=4)
        records[0]["probe"]["catalog_version"] = "sha256:" + "0" * 64
        result = build_evaluation(records, draws=20)
        new = result["new_material"]
        self.assertEqual((new["records"], new["skipped_record_reasons"]), (4, {"probe_validation": 1}))
        self.assertEqual(new["decision"]["verdict"], "insufficient_data")
        self.assertIn("new-image decision: insufficient_data", format_evaluation(result))
        self.assertNotIn("new_material", build_evaluation(simulate_records(3, visits=(1, 1), seed=4), draws=10))

    def test_records_below_the_fixed_coverage_are_counted_not_scored(self):
        from norn.evaluate import NEW_MATERIAL_RULE, build_evaluation
        short_main = simulate_records(2, visits=(1, 1), rounds=(3, 4), probe="new-material-v1", seed=5)
        one_round = simulate_records(2, visits=(1, 1), rounds=(8, 10), probe="new-material-v1", seed=6)
        for record in one_round:
            events = record["probe"]["events"]
            cut = next(index for index, event in enumerate(events) if event["type"] == "round_completed") + 1
            kept = events[:cut] + [{"type": "session_completed"}]
            record["probe"]["events"] = [{**event, "event_id": f"probe-{n:04}", "sequence": n}
                                         for n, event in enumerate(kept, 1)]
        new = build_evaluation([*short_main, *one_round], draws=10)["new_material"]
        self.assertEqual(new["skipped_record_reasons"], {"main_journal_too_short": 2, "probe_too_short": 2})
        self.assertEqual(new["coverage"], {"probe_records": 4, "usable": 0})
        self.assertIn("all", NEW_MATERIAL_RULE["primary"])  # the frozen protocol's estimand, not cards-only


if __name__ == "__main__":
    unittest.main()

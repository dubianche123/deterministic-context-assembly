"""Holdout provenance and frozen transforms remain separate from the live deck."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from norn import feature_projection as projection
from scripts import prepare_generalization_pack as packer


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PACK = ROOT / "benchmarks/generalization/v1"
HAVE_PILLOW = importlib.util.find_spec("PIL") is not None
HAVE_NUMPY = importlib.util.find_spec("numpy") is not None


def prompts():
    return json.loads((SOURCE_PACK / "prompts.json").read_text())


def rehash(document):
    document["pack_version"] = packer.content_hash({key: value for key, value in document.items()
                                                    if key != "pack_version"})


class HoldoutDesignTests(unittest.TestCase):
    def test_predeclared_design_has_crossed_styles_and_balanced_batches(self):
        self.assertEqual(len(packer.validate_design(prompts())), 16)
        for change in (lambda doc: doc["rows"][0].update(style="photograph"),
                       lambda doc: doc["rows"][0].update(batch="batch_4"),
                       lambda doc: doc["rows"][0].update(axes=[0, 0, 0, 0]),
                       lambda doc: doc["rows"][0].update(asset="../outside.png"),
                       lambda doc: doc["rows"].pop()):
            forged = prompts()
            change(forged)
            with self.subTest(change=change), self.assertRaises(ValueError):
                packer.validate_design(forged)

    def test_receipt_cannot_name_another_prompt_or_repeat_a_source(self):
        rows = prompts()["rows"]
        receipts = {"schema_version": "norn.generalization.generation.v1", "tool": "built-in image_gen",
                    "rows": [{"id": row["id"], "asset": row["asset"],
                              "prompt_sha256": packer.text_hash(row["prompt"]),
                              "sha256": "sha256:" + "0" * 64,
                              "generated_at": "2026-10-21T00:00:00Z", "source": "builtin_image_gen"}
                             for row in rows]}
        self.assertEqual(len(packer._receipts(receipts, rows)), 16)
        for change in (lambda doc: doc["rows"][0].update(prompt_sha256="sha256:" + "1" * 64),
                       lambda doc: doc["rows"][0].update(id="HOLDOUT_0002"),
                       lambda doc: doc["rows"][0].update(source="original_card_crop")):
            forged = deepcopy(receipts)
            change(forged)
            with self.subTest(change=change), self.assertRaises(ValueError):
                packer._receipts(forged, rows)

    def test_output_write_policy_rejects_existing_pack_before_any_fit(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "manifest.json").write_text("preserve me")
            with patch.object(projection, "fit_projection", side_effect=AssertionError("must not fit")):
                with self.assertRaisesRegex(ValueError, "already exist"):
                    packer.prepare(target)
            self.assertEqual((target / "manifest.json").read_text(), "preserve me")


@unittest.skipUnless(HAVE_PILLOW and (HAVE_NUMPY or (SOURCE_PACK / "frozen_projection.json").is_file()),
                     "Pillow and a frozen projection (or build-time NumPy) are optional")
class MeasuredHoldoutPackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = SOURCE_PACK / "frozen_projection.json"
        cls.frozen = json.loads(path.read_text()) if path.is_file() else projection.fit_projection()

    def setUp(self):
        from PIL import Image
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.pack = Path(self.directory.name)
        (self.pack / "assets").mkdir()
        # Tiny deterministic images exercise the importer, not the actual
        # human-facing test set. Real holdout images come only from generation.
        for name in ("prompts.json", "protocol.json"):
            (self.pack / name).write_bytes((SOURCE_PACK / name).read_bytes())
        self.receipts = {"schema_version": "norn.generalization.generation.v1", "tool": "built-in image_gen", "rows": []}
        for number, row in enumerate(prompts()["rows"], 1):
            asset = self.pack / row["asset"]
            Image.new("RGB", (32 + number, 48), (number * 11, number * 5, 255 - number * 7)).save(asset)
            self.receipts["rows"].append({"id": row["id"], "asset": row["asset"],
                                         "prompt_sha256": packer.text_hash(row["prompt"]),
                                         "sha256": packer.file_hash(asset), "generated_at": "2026-10-21T00:00:00Z",
                                         "source": "builtin_image_gen"})
        self.save_receipts()

    def save_receipts(self):
        (self.pack / "generation.json").write_text(json.dumps(self.receipts))

    def build(self):
        with patch.object(projection, "fit_projection", return_value=deepcopy(self.frozen)):
            return packer.prepare(self.pack)

    def test_build_and_check_apply_saved_transform_without_refitting_or_touching_live_deck(self):
        before = packer._source_identity()[0]
        result = self.build()
        self.assertEqual(result["image_count"], 16)
        self.assertEqual(result["predictive_validation"], "pending_human_choices")
        self.assertTrue(all(row["features"]["axes"] is None and row["features"]["type"] is None
                            and row["features"]["motifs"] is None for row in result["rows"]))
        self.assertEqual(result["candidate_support"]["hybrid_axes_pixels"]["status"], "unsupported")
        outputs = {name: (self.pack / name).read_bytes() for name in ("manifest.json", "frozen_projection.json")}
        with patch.object(projection, "fit_projection", side_effect=AssertionError("check must never refit")):
            self.assertEqual(packer.prepare(self.pack, check=True), result)
        self.assertEqual(before, packer._source_identity()[0])
        self.assertEqual(outputs, {name: (self.pack / name).read_bytes() for name in outputs})

    def test_changed_image_and_generation_receipt_cannot_pass_as_original_pack(self):
        self.build()
        asset = self.pack / prompts()["rows"][0]["asset"]
        asset.write_bytes(asset.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "receipt"):
            packer.prepare(self.pack, check=True)
        self.receipts["rows"][0]["sha256"] = packer.file_hash(asset)
        self.save_receipts()
        with self.assertRaisesRegex(ValueError, "differs from its sources"):
            packer.prepare(self.pack, check=True)

    def test_duplicate_and_escaped_assets_fail_without_writing_outputs(self):
        rows = prompts()["rows"]
        (self.pack / rows[1]["asset"]).write_bytes((self.pack / rows[0]["asset"]).read_bytes())
        self.receipts["rows"][1]["sha256"] = packer.file_hash(self.pack / rows[1]["asset"])
        self.save_receipts()
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.build()
        self.assertFalse((self.pack / "manifest.json").exists())
        self.assertFalse((self.pack / "frozen_projection.json").exists())
        (self.pack / rows[0]["asset"]).unlink()
        (self.pack / rows[0]["asset"]).symlink_to(self.pack / rows[1]["asset"])
        with self.assertRaisesRegex(ValueError, "inside its pack"):
            self.build()

    def test_rehashed_fabricated_axes_or_wrong_features_are_rejected(self):
        result = self.build()
        for change in (lambda doc: doc["rows"][0]["features"].update(axes=[0, 0, 0, 0]),
                       lambda doc: doc["rows"][0]["features"]["pixel8"].__setitem__(0, 999.0)):
            forged = deepcopy(result)
            change(forged)
            rehash(forged)
            (self.pack / "manifest.json").write_text(json.dumps(forged))
            with self.subTest(change=change), self.assertRaises(ValueError):
                packer.prepare(self.pack, check=True)

    def test_original_deck_copy_and_undeclared_images_are_rejected(self):
        row = prompts()["rows"][0]
        asset = self.pack / row["asset"]
        asset.write_bytes((packer.ORIGINAL_ASSETS / "NORN_0001.webp").read_bytes())
        self.receipts["rows"][0]["sha256"] = packer.file_hash(asset)
        self.save_receipts()
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.build()
        (self.pack / "assets/extra.png").write_bytes(asset.read_bytes())
        with self.assertRaisesRegex(ValueError, "exactly the 16"):
            self.build()

    def test_amended_protocol_or_projection_identity_is_rejected(self):
        self.build()
        path = self.pack / "frozen_projection.json"
        frozen = json.loads(path.read_text())
        frozen["pca"]["means"][0] += 0.1
        path.write_text(json.dumps(frozen))
        with self.assertRaisesRegex(ValueError, "projection_version"):
            packer.prepare(self.pack, check=True)
        # An edited transform can have a valid new content hash and retain a
        # fabricated compatibility claim. Source-coordinate reconstruction is
        # required independently of that claim and of the manifest identity.
        frozen["projection_version"] = projection.content_hash({key: value for key, value in frozen.items()
                                                               if key != "projection_version"})
        path.write_text(json.dumps(frozen))
        with self.assertRaisesRegex(ValueError, "original choice coordinates"):
            packer.prepare(self.pack, check=True)
        path.write_text(json.dumps(self.frozen))
        (self.pack / "protocol.json").write_bytes((self.pack / "protocol.json").read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "protocol"):
            packer.prepare(self.pack, check=True)

    def test_rehashed_normalization_and_remeasured_manifest_cannot_replace_source_transform(self):
        result = self.build()
        for field, change in (
                ("means", lambda config: config["means"].__setitem__(0, config["means"][0] + 0.1)),
                ("scales", lambda config: config["scales"].__setitem__(0, config["scales"][0] * 2)),
                ("groups", lambda config: config["groups"][0].update(name="renamed_group"))):
            frozen = deepcopy(self.frozen)
            change(frozen["normalization"])
            frozen["projection_version"] = projection.content_hash({key: value for key, value in frozen.items()
                                                                     if key != "projection_version"})
            # The edit is structurally valid and normalized source rows still
            # reconstruct. Rehashing freshly measured holdout coordinates must
            # not turn that edit into a source-compatible normalization.
            projection.verify_projection(frozen)
            forged = deepcopy(result)
            forged["projection"]["version"] = frozen["projection_version"]
            for row in forged["rows"]:
                row["features"]["pixel8"] = projection.project_pixels(row["features"]["pixel64"], frozen)
            rehash(forged)
            (self.pack / "frozen_projection.json").write_text(json.dumps(frozen))
            (self.pack / "manifest.json").write_text(json.dumps(forged))
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "original source normalization"):
                packer.prepare(self.pack, check=True)


if __name__ == "__main__":
    unittest.main()

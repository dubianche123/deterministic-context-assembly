"""Admission checks bind the pilot catalogs and WebPs to declared frozen inputs."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import prepare_probe_assets as assets
from scripts import prepare_probe_catalog as catalog


ROOT = Path(__file__).resolve().parents[1]
HAVE_PILLOW = importlib.util.find_spec("PIL") is not None


class ProbeCatalogIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((catalog.PACK / "manifest.json").read_text(encoding="utf-8"))

    def _build(self, document, *, rehash=False):
        document = deepcopy(document)
        if rehash:
            document["pack_version"] = catalog._hash({key: value for key, value in document.items()
                                                       if key != "pack_version"})
        with tempfile.TemporaryDirectory() as directory:
            pack = Path(directory)
            (pack / "manifest.json").write_text(json.dumps(document), encoding="utf-8")
            with patch.object(catalog, "PACK", pack):
                return catalog.build()

    def test_untouched_manifest_derives_the_existing_catalogs_without_changing_outputs(self):
        # The derivative identity also binds the exact manifest file bytes;
        # retain its original formatting for this compatibility check.
        engine, page = catalog.build()
        self.assertEqual(json.dumps(engine, ensure_ascii=False, indent=1) + "\n", catalog.ENGINE.read_text())
        self.assertEqual(json.dumps(page, ensure_ascii=False, indent=1) + "\n", catalog.PAGE.read_text())

    def test_changed_pixels_cannot_keep_the_old_pack_identity(self):
        changed = deepcopy(self.manifest)
        changed["rows"][0]["features"]["pixel8"][0] += 0.125
        with self.assertRaisesRegex(ValueError, "content hash"):
            self._build(changed)

    def test_rehashed_bad_design_missing_labels_and_malformed_features_are_rejected(self):
        cases = {
            "unsupported schema": lambda m: m.update(schema_version="norn.generalization.pack.v9"),
            "duplicate IDs": lambda m: m["rows"][1].update(id=m["rows"][0]["id"]),
            "missing cell": lambda m: m["rows"].pop(),
            "wrong scene": lambda m: m["rows"][0].update(scene_id="unfrozen-scene"),
            "one style": lambda m: m["rows"][1].update(style="illustration"),
            "shifted batch": lambda m: m["rows"][0].update(batch="batch_4"),
            "escaped asset": lambda m: m["rows"][0].update(asset="../elsewhere.png"),
            "fake axes": lambda m: m["rows"][0]["features"].update(axes=[0, 0, 0, 0]),
            "fake row type": lambda m: m["rows"][0].update(type="INFP"),
            "extra features": lambda m: m["rows"][0]["features"].update(traits=[]),
            "missing explicit unknown": lambda m: m["rows"][0]["features"].pop("type"),
            "short pixel64": lambda m: m["rows"][0]["features"]["pixel64"].pop(),
            "short pixel8": lambda m: m["rows"][0]["features"]["pixel8"].pop(),
            "string pixel": lambda m: m["rows"][0]["features"]["pixel8"].__setitem__(0, "nan"),
            "boolean pixel": lambda m: m["rows"][0]["features"]["pixel64"].__setitem__(0, True),
            "invalid projection": lambda m: m["projection"].update(version="not-a-hash"),
        }
        for label, change in cases.items():
            document = deepcopy(self.manifest)
            change(document)
            with self.subTest(label), self.assertRaises(ValueError):
                self._build(document, rehash=True)
        nonfinite = deepcopy(self.manifest)
        nonfinite["rows"][0]["features"]["pixel8"][0] = float("inf")
        with self.assertRaisesRegex(ValueError, "Nonfinite"):
            self._build(nonfinite)

    def test_catalog_admission_and_derivation_do_not_import_numpy_or_pillow(self):
        code = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in ('numpy', 'PIL'):
        raise AssertionError('Optional image/eval dependency imported')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from scripts.prepare_probe_catalog import build
engine, page = build()
assert len(engine['cards']) == len(page['cards']) == 16
"""
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


@unittest.skipUnless(HAVE_PILLOW, "Probe WebP integrity checks need the optional Pillow")
class ProbeAssetIntegrityTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        self.tmp = tempfile.TemporaryDirectory()
        self.pack, self.target = Path(self.tmp.name) / "pack", Path(self.tmp.name) / "target"
        source = self.pack / "assets" / "HOLDOUT_0001.png"
        source.parent.mkdir(parents=True)
        Image.new("RGB", (48, 64), (80, 110, 140)).save(source, "PNG")
        self.source = source
        self.manifest = {"rows": [{"id": "HOLDOUT_0001", "asset": "assets/HOLDOUT_0001.png",
                                   "sha256": "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest(),
                                   "image": {"width": 48, "height": 64}}]}
        encoded, self.height = assets._encode(source)
        self.target.mkdir()
        self.image = self.target / "HOLDOUT_0001.webp"
        self.image.write_bytes(encoded)

    def tearDown(self):
        self.tmp.cleanup()

    def _check(self):
        out, err = io.StringIO(), io.StringIO()
        # The single-row asset fixture isolates byte conversion; the catalog
        # tests separately exercise admission of the complete frozen 16-cell manifest.
        with patch.multiple(assets, PACK=self.pack, TARGET=self.target), \
                patch.object(assets, "read_manifest", return_value=(deepcopy(self.manifest), b"")), \
                redirect_stdout(out), redirect_stderr(err):
            result = assets.main(["--check"])
        return result, out.getvalue(), err.getvalue()

    def test_source_derived_webp_passes_without_writing(self):
        before = self.image.read_bytes()
        source = self.source.read_bytes()
        self.assertEqual(self._check()[0], 0)
        self.assertEqual(self.image.read_bytes(), before)
        self.assertEqual(self.source.read_bytes(), source)

    def test_a_different_same_size_webp_is_rejected_without_replacement(self):
        from PIL import Image
        Image.new("RGB", (assets.WIDTH, self.height), "red").save(self.image, "WEBP", quality=80)
        wrong = self.image.read_bytes()
        result, _, error = self._check()
        self.assertEqual(result, 1)
        self.assertIn("different source-derived bytes", error)
        self.assertEqual(self.image.read_bytes(), wrong)

    def test_undeclared_file_is_rejected(self):
        extra = self.target / "extra.txt"
        extra.write_text("unlisted")
        result, _, error = self._check()
        self.assertEqual(result, 1)
        self.assertIn("undeclared files", error)
        self.assertTrue(extra.is_file())

    def test_changed_png_cannot_use_the_old_source_hash(self):
        from PIL import Image
        Image.new("RGB", (48, 64), "green").save(self.source, "PNG")
        result, _, error = self._check()
        self.assertEqual(result, 1)
        self.assertIn("source differs from the frozen manifest", error)

    def test_missing_target_directory_is_not_created_by_check(self):
        self.image.unlink()
        self.target.rmdir()
        self.assertEqual(self._check()[0], 1)
        self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()

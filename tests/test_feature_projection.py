"""Out-of-sample projection never fits on unseen images or supplies type axes."""

from copy import deepcopy
import importlib.util
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from norn import feature_projection as projection


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "the-norn-machine/backend/config"
ARTIFACT = ROOT / "benchmarks/generalization/v1/frozen_projection.json"
HAVE_NUMPY = importlib.util.find_spec("numpy") is not None
HAVE_PILLOW = importlib.util.find_spec("PIL") is not None


def frozen():
    if ARTIFACT.is_file():
        return json.loads(ARTIFACT.read_text(encoding="utf-8"))
    if HAVE_NUMPY:
        return projection.fit_projection()
    raise unittest.SkipTest("Frozen artifact or NumPy needed for a projection fixture")


def rehash(document):
    document["projection_version"] = projection.content_hash(
        {key: value for key, value in document.items() if key != "projection_version"})


class FrozenProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frozen = frozen()
        cls.source = json.loads((CONFIG / "preference_features.json").read_text())
        cls.choice = json.loads((CONFIG / "choice_features.json").read_text())

    def test_frozen_projection_reproduces_all_old_choice_pixel_coordinates(self):
        self.assertTrue(projection.verify_projection(self.frozen))
        self.assertEqual(self.frozen["source"]["feature_version"], self.source["feature_version"])
        self.assertEqual(self.frozen["source"]["choice_feature_version"], self.choice["feature_version"])
        worst = 0.0
        for card in sorted(self.source["cards"]):
            with self.subTest(card=card):
                actual = projection.project_normalized_pixels(self.source["cards"][card][:64], self.frozen)
                worst = max(worst, max(abs(a - b) for a, b in zip(actual, self.choice["cards"][card]["pixel"])))
        self.assertLessEqual(worst, projection.RECONSTRUCTION_TOLERANCE)

    def test_projection_works_without_importing_optional_build_dependencies(self):
        # Applying an existing artifact has no NumPy/Pillow import boundary.
        import builtins
        original = builtins.__import__

        def guarded(name, *args, **kwargs):
            if name.split(".")[0] in ("numpy", "PIL", "scripts"):
                raise AssertionError("Projection attempted to load a build-time dependency")
            return original(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=guarded):
            values = projection.project_pixels([0.0] * 64, self.frozen)
        self.assertEqual(len(values), 8)
        self.assertTrue(all(math.isfinite(value) for value in values))
        self.assertEqual(self.frozen["output"]["axes"], "unsupported_without_independent_metadata")
        self.assertEqual(self.frozen["output"]["feature_names"], [f"pixel.pc{i}" for i in range(1, 9)])

    def test_frozen_source_stats_are_not_updated_by_novel_samples(self):
        before = deepcopy(self.frozen)
        black = projection.project_pixels([0.0] * 64, self.frozen)
        white = projection.project_pixels([1.0] * 64, self.frozen)
        self.assertNotEqual(black, white)
        self.assertEqual(self.frozen, before)
        self.assertEqual(projection.project_pixels([0.0] * 64, self.frozen), black)

    def test_invalid_measurements_and_projection_mutations_are_rejected(self):
        invalid = ([0.0] * 63, [float("nan")] * 64, [float("inf")] * 64,
                   [True] * 64, [None] * 64, [-0.1] * 64, [1.1] * 64)
        for values in invalid:
            with self.subTest(values=values[:1]), self.assertRaises(ValueError):
                projection.project_pixels(values, self.frozen)
        for mutate in (lambda p: p["pca"]["loadings"][0].__setitem__(0, 9.0),
                       lambda p: p["normalization"]["means"].__setitem__(0, 0.0),
                       lambda p: p["source"]["fit_card_ids"].append("NOVEL_0001")):
            forged = deepcopy(self.frozen)
            mutate(forged)
            with self.assertRaisesRegex(ValueError, "projection_version"):
                projection.verify_projection(forged)
        # Rehashing a structurally invalid document does not bypass shape or
        # output-contract checks. Hashes alone are not authentication.
        for mutate in (lambda p: p["pca"]["loadings"][0].pop(),
                       lambda p: p["normalization"]["scales"].__setitem__(0, 0.0),
                       lambda p: p["source"].update(fit_card_ids=["NOVEL_0001"]),
                       lambda p: p["output"].update(axes=[0, 0, 0, 0])):
            forged = deepcopy(self.frozen)
            mutate(forged)
            rehash(forged)
            with self.assertRaises(ValueError):
                projection.verify_projection(forged)


@unittest.skipUnless(HAVE_NUMPY, "NumPy is an optional transform-generation dependency")
class ProjectionFitTests(unittest.TestCase):
    def test_fit_is_deterministic_and_does_not_modify_sources(self):
        source = json.loads((CONFIG / "preference_features.json").read_text())
        choice = json.loads((CONFIG / "choice_features.json").read_text())
        before = deepcopy((source, choice))
        first = projection.fit_projection(source, choice)
        self.assertEqual(first, projection.fit_projection(source, choice))
        self.assertEqual((source, choice), before)
        self.assertLess(first["compatibility"]["max_absolute_error"], 1e-10)

    def test_additional_cards_and_unlinked_sources_cannot_enter_the_fit(self):
        source = json.loads((CONFIG / "preference_features.json").read_text())
        choice = json.loads((CONFIG / "choice_features.json").read_text())
        source["cards"]["NOVEL_0001"] = source["cards"]["NORN_0001"]
        source["feature_version"] = projection.content_hash({k: v for k, v in source.items() if k != "feature_version"})
        with self.assertRaises(ValueError):
            projection.fit_projection(source, choice)
        source = json.loads((CONFIG / "preference_features.json").read_text())
        source["source_assets_hash"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(ValueError, "feature_version"):
            projection.fit_projection(source, choice)


@unittest.skipUnless(HAVE_PILLOW, "Pillow is an optional image-measurement dependency")
class MeasuredImageProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frozen = frozen()

    def test_all_original_images_use_the_frozen_transform_without_annotations(self):
        from scripts.prepare_preference_features import pixel_features
        source = json.loads((CONFIG / "preference_features.json").read_text())
        choice = json.loads((CONFIG / "choice_features.json").read_text())
        worst = 0.0
        for card in projection.EXPECTED_IDS:
            with self.subTest(card=card):
                raw = pixel_features(ROOT / "the-norn-machine/frontend/assets/cards" / (card + ".webp"))
                normalized = projection.normalize_pixels(raw, self.frozen)
                self.assertEqual(normalized, source["cards"][card][:64])
                actual = projection.project_pixels(raw, self.frozen)
                worst = max(worst, max(abs(a - b) for a, b in zip(actual, choice["cards"][card]["pixel"])))
        self.assertLess(worst, projection.RECONSTRUCTION_TOLERANCE)

    def test_new_png_and_degenerate_solid_colours_project_finitely(self):
        from PIL import Image, ImageDraw
        from scripts.prepare_preference_features import pixel_features
        before = deepcopy(self.frozen)
        with tempfile.TemporaryDirectory() as directory:
            outputs = []
            for index, colour in enumerate(((0, 0, 0), (255, 255, 255), (0, 128, 255))):
                image = Image.new("RGB", (91, 53), colour)
                if index == 2:
                    ImageDraw.Draw(image).rectangle((10, 5, 43, 31), fill=(255, 60, 0))
                path = Path(directory) / f"unseen-{index}.png"
                image.save(path)
                actual = projection.project_pixels(pixel_features(path), self.frozen)
                self.assertTrue(all(math.isfinite(value) for value in actual))
                outputs.append(actual)
            self.assertNotEqual(outputs[0], outputs[1])
            self.assertNotEqual(outputs[1], outputs[2])
        self.assertEqual(self.frozen, before)


if __name__ == "__main__":
    unittest.main()

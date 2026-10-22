from copy import deepcopy
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("prepare_preference_features", ROOT / "scripts" / "prepare_preference_features.py")
features = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(features)
try:
    from PIL import Image
except ImportError:
    Image = None


class ShippedPreferenceFeaturesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads(features.DEFAULT_OUTPUT.read_text(encoding="utf-8"))
        cls.metadata = json.loads(features.DEFAULT_METADATA.read_text(encoding="utf-8"))

    def test_complete_finite_unit_vectors_and_self_verifying_version(self):
        catalog = self.catalog
        self.assertEqual(catalog["schema_version"], "norn.features.v1")
        self.assertEqual(set(catalog["cards"]), features.EXPECTED_IDS)
        width = len(catalog["feature_names"])
        self.assertEqual(width, 979)
        self.assertEqual(len(set(catalog["feature_names"])), width)
        self.assertEqual(catalog["feature_version"], features.content_hash(
            {key: value for key, value in catalog.items() if key != "feature_version"}))
        for row in catalog["cards"].values():
            self.assertEqual(len(row), width)
            self.assertTrue(all(type(value) is float and math.isfinite(value) for value in row))
            self.assertAlmostEqual(math.fsum(value * value for value in row), 1, delta=1e-10)

    def test_semantics_are_sparse_exact_annotation_support(self):
        catalog = self.catalog
        names = catalog["feature_names"]
        self.assertEqual(names[:64], features.pixel_feature_names())
        self.assertTrue(all(name.startswith(("trait.", "motif.")) for name in names[64:]))
        self.assertEqual(catalog["normalization"]["means"][64:], [0.0] * (len(names) - 64))
        self.assertEqual(catalog["normalization"]["scales"][64:], [1.0] * (len(names) - 64))
        used = features.used_metadata(self.metadata)
        for card_id, row in catalog["cards"].items():
            expected = {"trait." + word for word in used[card_id]["traits"]}
            expected.update("motif." + word for word in used[card_id]["motifs"])
            observed = {name for name, value in zip(names[64:], row[64:]) if value != 0.0}
            self.assertEqual(observed, expected)
            self.assertTrue(all(value >= 0.0 for value in row[64:]))
            for name, value in zip(names[64:], row[64:]):
                if name not in expected:
                    self.assertEqual(value, 0.0)

    def test_provenance_uses_actual_assets_and_only_used_annotations(self):
        asset_hashes = {card_id: "sha256:" + hashlib.sha256(
            (features.DEFAULT_ASSETS / (card_id + ".webp")).read_bytes()).hexdigest()
                        for card_id in sorted(self.catalog["cards"])}
        self.assertEqual(self.catalog["source_assets_hash"], features.content_hash(asset_hashes))
        used = features.used_metadata(self.metadata)
        self.assertEqual(self.catalog["source_metadata_hash"], features.content_hash(used))
        changed = deepcopy(self.metadata)
        for row in changed.values():
            row.update(coords={"secret_personality_axis": 999}, type="UNUSED_LABEL",
                       group="unused", rationale="ignored prose", scene="ignored unique scene")
        self.assertEqual(features.used_metadata(changed), used)
        self.assertFalse(any("NORN_" in name or "coords" in name or "UNUSED_LABEL" in name
                             for name in self.catalog["feature_names"]))

    def test_group_boundaries_and_saved_normalization_can_be_inspected(self):
        stats = self.catalog["normalization"]
        self.assertEqual(stats["continuous_end"], 64)
        self.assertEqual([(group["start"], group["end"]) for group in stats["groups"][:6]],
                         [(0, 12), (12, 18), (18, 24), (24, 40), (40, 56), (56, 64)])
        self.assertAlmostEqual(math.fsum(group["weight"] for group in stats["groups"]), 1)
        self.assertEqual(stats["groups"][-2]["weight"], 0.25)
        self.assertEqual(stats["groups"][-1]["weight"], 0.25)
        self.assertEqual(set(stats["card_group_norms"]), features.EXPECTED_IDS)
        self.assertTrue(all(len(norms) == 8 for norms in stats["card_group_norms"].values()))
        self.assertTrue(all(scale > 0 and math.isfinite(scale) for scale in stats["scales"]))

    def test_projection_is_centered_finite_and_reconstructs_from_real_features(self):
        projection = self.catalog["projection"]
        self.assertEqual(projection["method"], "pca2")
        self.assertEqual(set(projection["points"]), features.EXPECTED_IDS)
        self.assertEqual(projection["iterations"], 48)
        self.assertEqual(len(projection["components"]), 2)
        ratios = projection["explained_variance_ratio"]
        self.assertTrue(all(0 <= ratio <= 1 for ratio in ratios))
        self.assertLessEqual(math.fsum(ratios), 1.0 + 1e-10)
        self.assertGreater(ratios[0], 0)
        self.assertGreater(projection["total_variance"], 0)
        self.assertGreater(projection["coordinate_scale"], 0)
        for axis in range(2):
            self.assertAlmostEqual(math.fsum(point[axis] for point in projection["points"].values()), 0, delta=1e-9)
            vector = projection["components"][axis]
            self.assertEqual(len(vector), len(self.catalog["feature_names"]))
            self.assertAlmostEqual(math.fsum(value * value for value in vector), 1, delta=1e-10)
            pivot = min(range(len(vector)), key=lambda index: (-abs(vector[index]), index))
            self.assertGreater(vector[pivot], 0)
        self.assertAlmostEqual(math.fsum(a * b for a, b in zip(*projection["components"])), 0, delta=1e-10)
        for card_id, point in projection["points"].items():
            self.assertTrue(all(math.isfinite(value) and -1.0 <= value <= 1.0 for value in point))
            for axis in range(2):
                raw = math.fsum((value - mean) * loading for value, mean, loading in zip(
                    self.catalog["cards"][card_id], projection["feature_means"], projection["components"][axis]))
                self.assertAlmostEqual(point[axis], raw / projection["coordinate_scale"], delta=1e-10)


@unittest.skipIf(Image is None, "Pillow is optional and needed only to regenerate feature images")
class PreferenceFeatureGenerationTests(unittest.TestCase):
    def test_pixel_features_measure_color_saturation_brightness_and_edges(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "red.webp"
            Image.new("RGB", (64, 64), (255, 0, 0)).save(path, lossless=True)
            vector = features.pixel_features(path)
            self.assertEqual(len(vector), 64)
            self.assertEqual(vector[:12], [0, 0, 0, 1, 1, 0, 0, 0, 1, 0, 0, 0])
            self.assertEqual(vector[12:18], [0, 0, 0, 0, 0, 1])
            self.assertEqual(vector[18:24], [0, 1, 0, 0, 0, 0])
            self.assertTrue(all(math.isclose(value, 0.2126, abs_tol=1e-12) for value in vector[24:40]))
            self.assertEqual(vector[40:56], [1.0] * 16)
            self.assertEqual(vector[56:], [0.0] * 8)
            image = Image.new("RGB", (64, 64), (0, 0, 0))
            for col in range(32, 64):
                for row in range(64):
                    image.putpixel((col, row), (255, 255, 255))
            image.save(path, lossless=True)
            edge_vector = features.pixel_features(path)
            self.assertAlmostEqual(math.fsum(edge_vector[56:]), 1.0)
            self.assertAlmostEqual(edge_vector[56], 1.0)

    def test_deterministic_generation_ignores_personality_fields_and_reconstructs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = {}
            for index, color in enumerate(((255, 0, 0), (0, 0, 255)), 1):
                card_id = f"NORN_{index:04d}"
                Image.new("RGB", (64, 64), color).save(root / (card_id + ".webp"), lossless=True)
                metadata[card_id] = {"traits": ["shared", "trait-" + str(index)],
                                     "motifs": ["motif-" + str(index)], "type": "ignored", "coords": {"axis": index}}
            path = root / "metadata.json"
            path.write_text(json.dumps(metadata), encoding="utf-8")
            first = features.build_catalog(root, path, require_256=False)
            second = features.build_catalog(root, path, require_256=False)
            self.assertEqual(features.canonical_json(first), features.canonical_json(second))
            for row in metadata.values():
                row.update(type="different", coords={"different-axis": 1e10}, scene="ignored scene")
            path.write_text(json.dumps(metadata), encoding="utf-8")
            ignored = features.build_catalog(root, path, require_256=False)
            self.assertEqual(first["feature_version"], ignored["feature_version"])
            names, stats = first["feature_names"], first["normalization"]
            for card_id, row in metadata.items():
                raw = features.pixel_features(root / (card_id + ".webp"))
                raw += [float(name[6:] in row["traits"]) for name in names[64:] if name.startswith("trait.")]
                raw += [float(name[6:] in row["motifs"]) for name in names[64:] if name.startswith("motif.")]
                rebuilt = [(value - stats["means"][index]) / stats["scales"][index] for index, value in enumerate(raw)]
                for group, norm in zip(stats["groups"], stats["card_group_norms"][card_id]):
                    multiplier = math.sqrt(group["weight"]) / norm if norm else 0.0
                    for index in range(group["start"], group["end"]):
                        rebuilt[index] *= multiplier
                rebuilt = [round(value / stats["card_final_norms"][card_id], 12) if value else 0.0 for value in rebuilt]
                self.assertEqual(rebuilt, first["cards"][card_id])
            Image.new("RGB", (64, 64), (0, 255, 0)).save(root / "NORN_0001.webp", lossless=True)
            changed = features.build_catalog(root, path, require_256=False)
            self.assertNotEqual(changed["source_assets_hash"], first["source_assets_hash"])
            self.assertNotEqual(changed["feature_version"], first["feature_version"])

    def test_production_regeneration_covers_all_256_real_images(self):
        generated = features.build_catalog()
        shipped = json.loads(features.DEFAULT_OUTPUT.read_text(encoding="utf-8"))
        self.assertEqual(features.canonical_json(generated), features.canonical_json(shipped))


if __name__ == "__main__":
    unittest.main()

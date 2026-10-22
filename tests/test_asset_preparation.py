"""Optional source-asset rebuild checks; install .[assets] to run them."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prepare_offline_assets.py"


@unittest.skipUnless(importlib.util.find_spec("PIL"), "Asset rebuild tests need optional Pillow")
class AssetPreparationTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.originals = self.root / "originals"
        self.originals.mkdir()
        self.manifest = self.root / "frontend" / "data" / "cards.json"
        self.manifest.parent.mkdir(parents=True)
        self.output = self.root / "frontend" / "assets" / "cards"
        self.cards = [{"id": f"NORN_{number:04}", "url": f"https://old.invalid/{number}.png",
                       "coords": {"E_I": number}, "type": "INTJ"} for number in (1, 2)]
        self.original_manifest = json.dumps(self.cards).encode()
        self.manifest.write_bytes(self.original_manifest)
        for card in self.cards:
            Image.new("RGB", (600, 800), color=(30, 40, 50)).save(self.originals / f"{card['id']}.png")

    def run_script(self, *source):
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, source),
                               "--manifest", str(self.manifest), "--output-dir", str(self.output)],
                              capture_output=True, text=True)

    def test_directory_rebuild_is_repeatable_and_preserves_card_metadata(self):
        from PIL import Image

        first = self.run_script("--source-dir", self.originals)
        self.assertEqual(first.returncode, 0, first.stderr)
        expected = [{**card, "url": f"./assets/cards/{card['id']}.webp"} for card in self.cards]
        self.assertEqual(json.loads(self.manifest.read_text()), expected)
        images = {path.name: path.read_bytes() for path in self.output.glob("*.webp")}
        self.assertEqual(len(images), 2)
        for name in images:
            with Image.open(self.output / name) as image:
                self.assertEqual(image.size, (360, 480))
                image.load()
        second = self.run_script("--source-dir", self.originals)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(images, {path.name: path.read_bytes() for path in self.output.glob("*.webp")})
        self.assertFalse(list(self.manifest.parent.glob("*.tmp")))

    def test_incomplete_archive_keeps_manifest_until_every_card_exists(self):
        archive_path = self.root / "originals.tar.gz"
        with tarfile.open(archive_path, "w:gz") as archive:
            archive.add(self.originals / "NORN_0001.png", arcname="project/images_final/NORN_0001.png")
        missing = self.run_script("--archive", archive_path)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("Missing 1 original PNGs", missing.stderr)
        self.assertEqual(self.manifest.read_bytes(), self.original_manifest)
        with tarfile.open(archive_path, "w:gz") as archive:
            for card in self.cards:
                archive.add(self.originals / f"{card['id']}.png", arcname=f"project/images_final/{card['id']}.png")
        complete = self.run_script("--archive", archive_path)
        self.assertEqual(complete.returncode, 0, complete.stderr)
        self.assertTrue(all(card["url"].startswith("./assets/cards/")
                            for card in json.loads(self.manifest.read_text())))


if __name__ == "__main__":
    unittest.main()

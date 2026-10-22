"""The cloud bundle survives snapshots that lose temporary filesystem files."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class CloudflareBundleTests(unittest.TestCase):
    def test_allowlisted_bundle_restores_files_when_memory_ready_flag_survives(self):
        spec = importlib.util.spec_from_file_location("norn_prepare_test", ROOT / "scripts/prepare_cloudflare_bundle.py")
        prepare = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(prepare)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "generated"
            with patch.object(prepare, "TARGET", target):
                prepare.prepare()
                stale = target / "assets" / "private-recording.json"
                stale.write_text("must not be deployed")
                prepare.prepare()
            self.assertFalse(stale.exists())
            self.assertEqual(len(list((target / "assets/assets/decks/pamela-v1").glob("*.webp"))), 256)
            self.assertTrue((target / "assets/assets/decks/pamela-v1/ATTRIBUTION.md").is_file())
            self.assertTrue((target / "assets/data/decks/pamela-v1.json").is_file())
            self.assertFalse((target / "assets/assets/cards").exists())
            self.assertFalse((target / "assets/data/probe-new-material-v1.json").exists())
            config = (target / "assets/runtime-config.js").read_text().split(" = ", 1)[1].rstrip(";\n")
            self.assertEqual(json.loads(config)["deck"], "pamela-v1")
            self.assertIn('NORN_DECK: bindings.text("pamela-v1")',
                          (ROOT / "cloudflare/cloudflare.config.ts").read_text())
            frontend = ROOT / "the-norn-machine/frontend"
            self.assertEqual((target / "assets/i18n.js").read_bytes(), (frontend / "i18n.js").read_bytes())
            self.assertRegex((target / "assets/index.html").read_text(),
                             r'<script\s+src="(?:\./)?i18n\.js(?:\?[^\"]*)?">')
            headers = (target / "assets/_headers").read_text()
            self.assertNotIn("Cache-Control: no-cache", headers.split("\n/", 1)[0])
            self.assertIn("/assets/cards/*\n  Cache-Control: public, max-age=604800, immutable", headers)
            self.assertEqual([path.name for path in (target / "worker").iterdir()], ["entry.py"])
            entry_source = (target / "worker/entry.py").read_text()
            self.assertNotIn("from norn_bundle import", entry_source)
            self.assertEqual(entry_source.count('_DATA = """'), 1)
            # Run the actual entrypoint prelude without any helper module on
            # sys.path. SDK class definitions follow the first bootstrap call.
            source, remainder = entry_source.split("\nbootstrap()\n", 1)
            self.assertIn("class NornSession", remainder)
            bundle = {}
            exec(compile(source, "entry.py", "exec"), bundle)
            runtime = Path(directory) / "runtime"
            bundle["Path"] = lambda value: runtime
            old_path = list(sys.path)
            try:
                bundle["bootstrap"]()
                resource = runtime / "norn/engine/config/preference_features.json"
                expected = resource.read_bytes()
                resource.unlink()  # Python objects survived, filesystem did not.
                self.assertTrue(bundle["_ready"])
                bundle["bootstrap"]()
                self.assertEqual(resource.read_bytes(), expected)
                self.assertEqual(sys.path.count(str(runtime)), 1)
                self.assertFalse((runtime / ".git").exists())
                self.assertTrue((runtime / "norn/engine/numeric.py").is_file())
                # The Worker validates pilot probes itself, against the same frozen catalog.
                self.assertTrue((runtime / "norn/probe.py").is_file())
                self.assertTrue((runtime / "norn/engine/config/probe_new_material_v1.json").is_file())
            finally:
                sys.path[:] = old_path

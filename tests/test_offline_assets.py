"""Verify the complete shipped experience has no CDN or image dependency.

Uses the standard library so these checks run on the same bare Python install
as the default mock service. Inspect each WebP container and its encoded
dimensions without adding a runtime image-library dependency.
"""

from html.parser import HTMLParser
import json
from pathlib import Path
import re
import struct
import unittest
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "the-norn-machine" / "frontend"


class ResourceParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.resources = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name == "src" or (tag == "link" and name == "href"):
                self.resources.append(value)


def webp_dimensions(data):
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError("Not a WebP RIFF container")
    if struct.unpack_from("<I", data, 4)[0] + 8 != len(data):
        raise ValueError("Truncated or invalid WebP container")
    offset = 12
    while offset + 8 <= len(data):
        kind = data[offset:offset + 4]
        length = struct.unpack_from("<I", data, offset + 4)[0]
        chunk = data[offset + 8:offset + 8 + length]
        if len(chunk) != length:
            raise ValueError("Truncated WebP chunk")
        if kind == b"VP8 ":
            if chunk[3:6] != b"\x9d\x01\x2a":
                raise ValueError("Invalid VP8 frame header")
            width, height = struct.unpack_from("<HH", chunk, 6)
            return width & 0x3fff, height & 0x3fff
        if kind == b"VP8X":
            return 1 + int.from_bytes(chunk[4:7], "little"), 1 + int.from_bytes(chunk[7:10], "little")
        if kind == b"VP8L" and chunk[0] == 0x2f:
            bits = int.from_bytes(chunk[1:5], "little")
            return (bits & 0x3fff) + 1, ((bits >> 14) & 0x3fff) + 1
        offset += 8 + length + (length % 2)
    raise ValueError("Missing WebP image frame")


class OfflineAssetsTests(unittest.TestCase):
    def test_all_original_cards_have_local_webp_images(self):
        cards = json.loads((FRONTEND / "data" / "cards.json").read_text())
        expected = {f"NORN_{number:04}" for number in range(1, 257)}
        self.assertEqual({card["id"] for card in cards}, expected)
        self.assertEqual(len(cards), len(expected))
        metadata = json.loads((ROOT / "the-norn-machine" / "backend" / "config" / "cards_metadata.json").read_text())
        for card in cards:
            with self.subTest(card=card["id"]):
                self.assertEqual(card["coords"], metadata[card["id"]]["coords"])
                self.assertEqual(card["type"], metadata[card["id"]]["type"])
                parsed = urlsplit(card["url"])
                self.assertFalse(parsed.scheme or parsed.netloc)
                self.assertEqual(card["url"], f"./assets/cards/{card['id']}.webp")
                path = (FRONTEND / parsed.path).resolve()
                self.assertTrue(path.is_relative_to(FRONTEND.resolve()))
                width, height = webp_dimensions(path.read_bytes())
                self.assertGreater(min(width, height), 0)
                self.assertEqual(max(width, height), 480)

    def test_html_and_css_resources_are_bundled(self):
        parser = ResourceParser()
        parser.feed((FRONTEND / "index.html").read_text())
        css = (FRONTEND / "style.css").read_text()
        references = parser.resources + re.findall(r"url\(['\"]?([^)'\"]+)", css)
        self.assertGreater(len(references), 3)
        for reference in references:
            with self.subTest(reference=reference):
                parsed = urlsplit(reference)
                self.assertFalse(parsed.scheme or parsed.netloc)
                self.assertTrue((FRONTEND / parsed.path).is_file())
        self.assertNotRegex(css, r"@import\s")

    def test_frontend_never_bootstraps_an_external_config(self):
        app = (FRONTEND / "app.js").read_text()
        # The SVG namespace is local DOM vocabulary. The one public invitation
        # URL is copied to the clipboard (covered by the Last Card lifecycle),
        # never used to load config, images or a provider from the local game.
        local_resources = app.replace("http://www.w3.org/2000/svg", "")
        local_resources = local_resources.replace("https://norn-machine.dubianche123.workers.dev/", "")
        self.assertNotRegex(local_resources,
                            r"https?://|cloudfront\.net|fonts\.googleapis")


if __name__ == "__main__":
    unittest.main()

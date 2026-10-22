"""The acceptance utility fails closed before any paid or mutating route."""

from contextlib import redirect_stderr
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.verify_cloudflare import VerificationError
from scripts.verify_xray_comparison import ReadOnlyClient, main, read_document


class VerifyXrayComparisonTests(unittest.TestCase):
    def test_generation_and_mutation_routes_cannot_reach_transport(self):
        client = ReadOnlyClient("http://127.0.0.1:18878", retries=0)
        with patch("scripts.verify_cloudflare.Client.request", side_effect=AssertionError("transport used")) as transport:
            for method, route in (("POST", "/api/analyze"), ("POST", "/api/dialogue"),
                                  ("POST", "/api/record/withdraw"), ("GET", "/api/archive/export")):
                with self.subTest(route=route), self.assertRaises(VerificationError):
                    client.request(method, route, {})
            transport.assert_not_called()

    def test_inputs_are_strict_and_existing_reports_never_get_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            for source in ('{"a":1,"a":2}', '{"a":NaN}', '[]'):
                path.write_text(source)
                with self.subTest(source=source), self.assertRaises(ValueError):
                    read_document(path)
            output = Path(directory) / "report.json"
            output.write_text("keep this report")
            with patch("scripts.verify_xray_comparison.verify", side_effect=AssertionError("HTTP used")), \
                    redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as result:
                main(["--url", "http://127.0.0.1:18878", "--journal", str(path),
                      "--comparison-report", str(path), "--output", str(output)])
            self.assertEqual(result.exception.code, 2)
            self.assertEqual(output.read_text(), "keep this report")


if __name__ == "__main__":
    unittest.main()

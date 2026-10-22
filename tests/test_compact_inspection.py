"""Chart transport remains small while source facts and exports stay exact."""

from copy import deepcopy
import http.client
import json
from pathlib import Path
import threading
import unittest

from norn.inspection import COMPACT_INSPECTION_VERSION, compact_inspection
from norn.server import make_server
from norn.service import NornService, RequestError


FIXTURES = Path(__file__).parent / "fixtures"


def journal():
    return json.loads((FIXTURES / "legacy-profile-v1.json").read_text())["journal"]


class CompactInspectionTests(unittest.TestCase):
    def test_compact_preview_retains_exact_graphs_but_omits_canonical_duplicates(self):
        source = journal()
        untouched = deepcopy(source)
        service = NornService()
        full = service.preview({"journal": source})
        compact = service.preview({"journal": source, "compact": True})
        self.assertEqual(compact["transport"], COMPACT_INSPECTION_VERSION)
        self.assertEqual(compact["preference_map"], full["preference_map"])
        self.assertEqual(compact["coords_avg"], full["coords_avg"])
        self.assertEqual(compact["profile_hash"], full["profile_hash"])
        self.assertEqual(compact["preference"]["top_features"], full["preference"]["top_features"][:5])
        self.assertEqual(compact["profile"]["analysis"]["total_selections"], full["profile"]["analysis"]["total_selections"])
        self.assertEqual(compact["profile"]["analysis"]["choice_revision"]["total"], 6)
        for omitted in ("journal", "attribution", "round_weights", "context", "prompt"):
            self.assertNotIn(omitted, compact)
        self.assertNotIn("preference_model", compact["profile"])
        self.assertNotIn("attribution", compact["profile"]["analysis"])
        for recommendation in compact["preference"]["recommendations"]:
            self.assertNotIn("contributions", recommendation)
        self.assertEqual(compact["preference"]["guard"], {
            key: value for key, value in full["preference"]["guard"].items()
            if key in compact["preference"]["guard"]})
        # v4's full document no longer carries the heuristic ledger, so the margin is smaller.
        self.assertLess(len(json.dumps(compact)), len(json.dumps(full)) * .5)
        self.assertEqual(source, untouched)
        self.assertEqual(len(service.sessions._sessions), 0)
        self.assertEqual(service.limits.snapshot()["model_calls_reserved"], 0)

    def test_compact_frozen_inspection_never_changes_export_or_uses_model(self):
        service = NornService()
        result = service.analyze({"journal": journal()})
        key = result["session_id"]
        original = service.export_session(key)
        full = service.inspect_session(key)
        compact = service.inspect_session(key, compact=True)
        self.assertEqual(compact["calls"], compact_inspection(full)["calls"])
        self.assertEqual(compact["turn_count"], full["turn_count"])
        self.assertEqual(compact["max_turns"], full["max_turns"])
        self.assertNotIn("prompt", compact["calls"][0])
        self.assertEqual(service.export_session(key), original)
        compact["preference_map"]["points"][0]["x"] = 999
        self.assertNotEqual(service.inspect_session(key)["preference_map"]["points"][0]["x"], 999)
        self.assertEqual(service.limits.snapshot()["model_calls_reserved"], 0)

    def test_normalized_cache_zero_and_unknown_survive_without_vendor_raw(self):
        full = {"profile": {"analysis": {}}, "calls": [{"usage": {
            "cache_read_input_tokens": 0, "cache_read_input_tokens_observed": True,
            "output_tokens": None, "output_tokens_observed": False,
            "raw": {"vendor": "large raw data"}}, "prompt": {"secret": "not transport"}}]}
        compact = compact_inspection(full)
        usage = compact["calls"][0]["usage"]
        self.assertEqual(usage["cache_read_input_tokens"], 0)
        self.assertIs(usage["cache_read_input_tokens_observed"], True)
        self.assertIsNone(usage["output_tokens"])
        self.assertIs(usage["output_tokens_observed"], False)
        self.assertNotIn("raw", usage)
        self.assertNotIn("prompt", compact["calls"][0])

    def test_preview_compact_flag_is_explicit_and_strict(self):
        service = NornService()
        empty = {"schema_version": "norn.events.v1", "events": []}
        self.assertNotIn("transport", service.preview({"journal": empty, "compact": False}))
        for fields in ({"compact": "true"}, {"compact": 1}, {"compact": None}, {"extra": True}):
            with self.subTest(fields=fields), self.assertRaises(RequestError):
                service.preview({"journal": empty, **fields})


class CompactInspectionHTTPTests(unittest.TestCase):
    def test_inspection_compact_query_and_invalid_parameters(self):
        service = NornService()
        session = service.analyze({"total_rounds": 1, "selections": [{"id": "NORN_0001", "round": 1}]})
        server = make_server(port=0, service=service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(suffix):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                connection.request("GET", f"/api/sessions/{session['session_id']}/inspect{suffix}")
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()
        try:
            status, full = request("")
            self.assertEqual(status, 200)
            self.assertIn("context", full)
            status, compact = request("?compact=1")
            self.assertEqual(status, 200)
            self.assertEqual(compact["transport"], COMPACT_INSPECTION_VERSION)
            self.assertNotIn("context", compact)
            for query in ("?compact=0", "?compact=true", "?compact=1&compact=1", "?other=1", "?compact="):
                self.assertEqual(request(query)[0], 400, query)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)


if __name__ == "__main__":
    unittest.main()

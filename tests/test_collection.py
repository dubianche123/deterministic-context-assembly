"""Consented anonymous collection, withdrawal, actor snapshots and logs."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import http.client
import io
import json
import logging
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from norn.actor_state import dump_actor_state, new_actor_service
from norn.cli import main
from norn.collection import (CONSENT_VERSION, DisabledCollector, OutboxCollector, SCHEMA_SQL,
                             SQLiteCollector, apply_operation, open_collector, requested_consent, visitor_id)
from norn.logs import configure
from norn.service import NornService, RequestError
from norn.server import make_server


def journal():
    rows = [
        {"type": "round_started", "round": 1, "card_ids": ["NORN_0001", "NORN_0008", "NORN_0003"]},
        {"type": "card_selected", "round": 1, "card_id": "NORN_0001"},
        {"type": "round_completed", "round": 1, "duration_ms": 1000},
        {"type": "round_started", "round": 2, "card_ids": ["NORN_0004", "NORN_0002"]},
        {"type": "card_selected", "round": 2, "card_id": "NORN_0002"},
        {"type": "round_completed", "round": 2, "duration_ms": 2000},
        {"type": "session_completed"},
    ]
    return {"schema_version": "norn.events.v1", "events": [
        {"event_id": f"evt-{number:06}", "sequence": number, **row}
        for number, row in enumerate(rows, 1)]}


VISITOR = "Zm9yLXRlc3RzLW9ubHktYS1yYW5kb20ta2V5"
OTHER_VISITOR = "b3RoZXItYnJvd3Nlci1yYW5kb20ta2V5LTEyMw"
CONSENT = {"consent": CONSENT_VERSION, "visitor": VISITOR}


def render(provider, prompt, **kwargs):
    return {"text": "一段模拟判词。", "status": "ok", "provider": provider,
            "simulated": True, "usage": {}, "provider_calls": 0}


class CollectionServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SQLiteCollector(Path(self.tmp.name) / "records.sqlite3")
        self.service = NornService(generate=render, collector=self.store)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def analyze(self, **extra):
        return self.service.analyze({"journal": journal(), **extra})

    def test_consent_marker_and_visitor_key_are_exact(self):
        self.assertEqual(requested_consent({"journal": 1}), ({"journal": 1}, None))
        self.assertEqual(requested_consent({"journal": 1, **CONSENT}), ({"journal": 1}, visitor_id(VISITOR)))
        self.assertRegex(visitor_id(VISITOR), r"^[0-9a-f]{32}$")
        for value in (True, "yes", "norn.consent.v1", None):
            with self.assertRaises(ValueError):
                requested_consent({"journal": 1, "consent": value, "visitor": VISITOR})
        for key in (None, "short", "x" * 65, "has space in it and is long", 12345678901234567890123):
            with self.assertRaises(ValueError):
                requested_consent({"journal": 1, "consent": CONSENT_VERSION, "visitor": key})
        with self.assertRaises(ValueError):
            requested_consent({"journal": 1, "visitor": VISITOR})
        with self.assertRaises(RequestError):
            self.analyze(consent=True)

    def test_nothing_is_stored_without_consent(self):
        result = self.analyze()
        self.assertIsNone(result["record"])
        self.assertEqual(self.store.stats()["records"], 0)
        self.assertIsNone(self.service.sessions.get(result["session_id"]).record_id)

    def test_consented_record_holds_journal_and_no_identity(self):
        result = self.analyze(**CONSENT)
        receipt = result["record"]
        self.assertEqual(set(receipt), {"record_id"})
        self.assertRegex(receipt["record_id"], r"^[0-9a-f]{32}$")
        [record] = self.store.page()
        self.assertEqual(record["journal"], journal())
        self.assertEqual(record["consent_version"], CONSENT_VERSION)
        self.assertEqual((record["visitor_id"], record["visit"]), (visitor_id(VISITOR), 1))
        self.assertEqual(record["reading"], "一段模拟判词。")
        self.assertEqual(record["profile_hash"], result["profile_hash"])
        self.assertTrue(record["generation"]["prompt_version"].startswith("norn.prompt."))
        self.assertRegex(record["created_day"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(record["annotations"], {})
        text = json.dumps(record, ensure_ascii=False)
        for forbidden in ("session_id", "visitor", "ip", "user_agent"):
            self.assertNotIn(f'"{forbidden}"', text)
        self.assertNotIn(VISITOR, text)
        with sqlite3.connect(self.store.path) as db:
            self.assertNotIn(VISITOR, " ".join(str(value) for row in db.execute("SELECT * FROM records") for value in row))

    def test_visits_from_one_browser_are_linked_and_numbered(self):
        self.analyze(**CONSENT, request_id="visit-1")
        self.analyze(consent=CONSENT_VERSION, visitor=OTHER_VISITOR, request_id="other-1")
        self.analyze(**CONSENT, request_id="visit-2")
        self.analyze(request_id="private-visit")
        visits = sorted((row["visitor_id"] == visitor_id(VISITOR), row["visit"]) for row in self.store.page())
        self.assertEqual(visits, [(False, 1), (True, 1), (True, 2)])
        self.assertEqual(self.store.stats()["visitors"], 2)

    def test_retry_with_same_request_id_stores_once(self):
        first = self.analyze(**CONSENT, request_id="reading-1")
        second = self.analyze(**CONSENT, request_id="reading-1")
        self.assertEqual(first["record"], second["record"])
        self.assertEqual(self.store.stats()["records"], 1)

    def test_rating_annotates_record_and_dialogue_text_is_never_stored(self):
        result = self.analyze(**CONSENT)
        self.service.dialogue({"session_id": result["session_id"], "user_message": "我的秘密问题"})
        self.service.record_feedback({"session_id": result["session_id"], "reading_rating": 4,
                                      "closest_card_id": "NORN_0002"})
        [record] = self.store.page()
        self.assertEqual(record["annotations"]["post_session"]["reading_rating"], 4)
        self.assertNotIn("我的秘密问题", json.dumps(record, ensure_ascii=False))

    def test_withdraw_requires_the_same_browser_and_is_idempotent(self):
        receipt = self.analyze(**CONSENT)["record"]
        mine = {"visitor": VISITOR, **receipt}
        with self.assertRaises(RequestError) as caught:
            self.service.withdraw_record({**mine, "visitor": OTHER_VISITOR})
        self.assertEqual(caught.exception.status, 403)
        self.assertEqual(self.store.stats()["records"], 1)
        self.assertEqual(self.service.withdraw_record(mine), {"withdrawn": True})
        self.assertFalse(self.store.annotate(receipt["record_id"], {"post_session": {}}))
        self.assertEqual(self.store.stats()["records"], 0)
        self.assertEqual(self.service.withdraw_record(mine), {"withdrawn": True})
        for bad in ({"record_id": receipt["record_id"]}, {"visitor": VISITOR, "all": False},
                    {"visitor": VISITOR, "all": True, "record_id": receipt["record_id"]}):
            with self.assertRaises(RequestError):
                self.service.withdraw_record(bad)

    def test_forget_removes_every_record_of_one_browser_only(self):
        for number in range(3):
            self.analyze(**CONSENT, request_id=f"mine-{number}")
        self.analyze(consent=CONSENT_VERSION, visitor=OTHER_VISITOR)
        self.assertEqual(self.service.withdraw_record({"visitor": VISITOR, "all": True}), {"withdrawn": True})
        [left] = self.store.page()
        self.assertEqual(left["visitor_id"], visitor_id(OTHER_VISITOR))

    def test_empty_pre_v2_table_is_replaced_and_full_one_refused(self):
        path = Path(self.tmp.name) / "old.sqlite3"
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE records (record_id TEXT PRIMARY KEY, payload TEXT)")
        SQLiteCollector(path).close()
        with sqlite3.connect(path) as db:
            db.execute("DROP TABLE records")
            db.execute("CREATE TABLE records (record_id TEXT PRIMARY KEY, payload TEXT)")
            db.execute("INSERT INTO records VALUES ('a', '{}')")
        with self.assertRaises(sqlite3.DatabaseError):
            SQLiteCollector(path)

    def test_store_failure_keeps_the_reading(self):
        self.store.close()
        result = self.analyze(**CONSENT)
        self.assertEqual(result["reading"], "一段模拟判词。")
        self.assertIsNone(result["record"])
        self.store = SQLiteCollector(Path(self.tmp.name) / "records.sqlite3")


class DisabledAndOutboxTests(unittest.TestCase):
    def test_disabled_collection_ignores_consent(self):
        service = NornService(generate=render)
        self.assertFalse(service.health()["collection"])
        self.assertIsNone(service.analyze({"journal": journal(), **CONSENT})["record"])
        with self.assertRaises(RequestError):
            service.withdraw_record({"record_id": "0" * 32, "visitor": VISITOR})

    def test_unwritable_data_dir_disables_instead_of_failing(self):
        with tempfile.NamedTemporaryFile() as blocker:
            self.assertIsInstance(open_collector(Path(blocker.name) / "nested"), DisabledCollector)
        self.assertIsInstance(open_collector(None), DisabledCollector)

    def test_outbox_operations_apply_with_the_same_sql(self):
        outbox = OutboxCollector()
        service = NornService(generate=render, collector=outbox)
        result = service.analyze({"journal": journal(), **CONSENT})
        service.record_feedback({"session_id": result["session_id"], "reading_rating": 2,
                                 "closest_card_id": None})
        service.withdraw_record({"visitor": VISITOR, **result["record"]})
        service.withdraw_record({"visitor": VISITOR, "all": True})
        db = sqlite3.connect(":memory:")
        for statement in SCHEMA_SQL:
            db.execute(statement)
        execute = lambda sql, *params: db.execute(sql, params).fetchall()
        operations = outbox.drain()
        self.assertEqual([op["op"] for op in operations], ["put", "annotate", "withdraw", "forget"])
        apply_operation(execute, operations[0])
        apply_operation(execute, operations[1])
        notes = json.loads(execute("SELECT annotations FROM records")[0][0])
        self.assertEqual(notes["post_session"]["reading_rating"], 2)
        apply_operation(execute, operations[2])
        self.assertEqual(execute("SELECT COUNT(*) FROM records")[0][0], 0)
        self.assertTrue(apply_operation(execute, operations[3]))
        self.assertEqual(outbox.drain(), [])

    def test_actor_snapshot_keeps_record_link(self):
        service = new_actor_service()
        service.collection = OutboxCollector()
        result = service.analyze({"journal": journal(), **CONSENT})
        state = dump_actor_state(service)
        self.assertEqual(state["sessions"][0]["record_id"], result["record"]["record_id"])
        restored = new_actor_service(deepcopy(state))
        self.assertEqual(restored.sessions.get(result["session_id"]).record_id, result["record"]["record_id"])
        bad = deepcopy(state)
        bad["sessions"][0]["record_id"] = "not-hex"
        with self.assertRaises(ValueError):
            new_actor_service(bad)


class HttpAndCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SQLiteCollector(Path(self.tmp.name) / "records.sqlite3")
        self.server = make_server(port=0, service=NornService(generate=render, collector=self.store))
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.store.close()
        self.tmp.cleanup()

    def request(self, method, path, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {"Host": f"127.0.0.1:{self.server.server_port}"}
        payload = None
        if body is not None:
            payload = json.dumps(body).encode()
            headers.update({"Content-Type": "application/json", "Content-Length": str(len(payload))})
        connection.request(method, path, payload, headers)
        response = connection.getresponse()
        data = response.read().decode()
        connection.close()
        return response.status, data

    def test_runtime_config_offers_choice_and_withdraw_route_works(self):
        status, script = self.request("GET", "/runtime-config.js")
        self.assertEqual(status, 200)
        self.assertIn('"collect": true', script)
        self.assertIn(CONSENT_VERSION, script)
        status, body = self.request("POST", "/api/analyze", {"journal": journal(), **CONSENT})
        receipt = json.loads(body)["record"]
        self.assertEqual(self.store.stats()["records"], 1)
        status, body = self.request("POST", "/api/record/withdraw", {"visitor": VISITOR, **receipt})
        self.assertEqual((status, json.loads(body)), (200, {"withdrawn": True}))
        self.assertEqual(self.store.stats()["records"], 0)

    def test_access_and_event_logs_carry_no_content(self):
        stream = io.StringIO()
        configure("info", "json", stream)
        try:
            self.request("POST", "/api/analyze", {"journal": journal(), **CONSENT})
            self.request("GET", "/api/sessions/secret-session-id/inspect")
        finally:
            logging.getLogger("norn").handlers.clear()
        lines = [json.loads(line) for line in stream.getvalue().splitlines()]
        events = {line["event"] for line in lines}
        self.assertTrue({"http.request", "analysis.completed", "record.stored"} <= events)
        analysis = next(line for line in lines if line["event"] == "analysis.completed")
        self.assertEqual((analysis["rounds"], analysis["selections"], analysis["collected"]), (2, 2, True))
        routes = {line.get("route") for line in lines if line["event"] == "http.request"}
        self.assertIn("/api/sessions/:id/inspect", routes)
        text = stream.getvalue()
        for secret in ("secret-session-id", "一段模拟判词", "NORN_0001", VISITOR, visitor_id(VISITOR)):
            self.assertNotIn(secret, text)

    def test_records_cli_stats_and_export(self):
        self.request("POST", "/api/analyze", {"journal": journal(), **CONSENT})
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            self.assertEqual(main(["records", "stats", "--data-dir", self.tmp.name]), 0)
        self.assertEqual(json.loads(out.getvalue())["records"], 1)
        target = Path(self.tmp.name) / "export.jsonl"
        with redirect_stderr(io.StringIO()):
            self.assertEqual(main(["records", "export", "--data-dir", self.tmp.name, "--output", str(target)]), 0)
        [line] = target.read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(line)["journal"], journal())


if __name__ == "__main__":
    unittest.main()

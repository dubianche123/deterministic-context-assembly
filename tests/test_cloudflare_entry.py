"""Adapter contract tests; real Python/workerd integration runs separately.

The fake boundary uses actual SQLite transactions and recreates the DO class on
each RPC. This proves persistence rather than accidentally relying on a warm
Python instance. Workers SDK streams/RPC conversion still need workerd tests.
"""

from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class Headers:
    def __init__(self, values=None):
        self.values = {name.lower(): value for name, value in (values or {}).items()}

    def get(self, name):
        return self.values.get(name.lower())


class Reader:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.reads = 0
        self.cancelled = False
        self.released = False

    async def read(self):
        self.reads += 1
        if not self.chunks:
            return SimpleNamespace(done=True, value=None)
        return SimpleNamespace(done=False, value=self.chunks.pop(0))

    async def cancel(self):
        self.cancelled = True

    def releaseLock(self):
        self.released = True


class Request:
    def __init__(self, path, method="GET", body=None, headers=None, chunks=None):
        self.method = method
        self.url = "https://norn.example" + path
        raw = body if isinstance(body, bytes) else json.dumps(body).encode() if body is not None else b""
        self.reader = Reader(chunks if chunks is not None else [raw] if raw else [])
        self.body = SimpleNamespace(getReader=lambda: self.reader)
        self.headers = Headers({"Content-Type": "application/json", **(headers or {})})


class Response:
    def __init__(self, body=None, *, status=200, headers=None):
        self.body, self.status = body, status
        self.headers = Headers(headers)


class Entrypoint:
    def __init__(self, ctx, env):
        self.ctx, self.env = ctx, env


def load_entry():
    workers = ModuleType("workers")
    workers.WorkerEntrypoint = Entrypoint
    workers.DurableObject = Entrypoint
    workers.Response = Response
    bundle = ModuleType("norn_bundle")
    bundle.bootstrap = lambda: None
    spec = importlib.util.spec_from_file_location("norn_cloudflare_entry", ROOT / "cloudflare/src/entry.py")
    entry = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"workers": workers, "norn_bundle": bundle}):
        spec.loader.exec_module(entry)
    return entry


ENTRY = load_entry()


class SQL:
    def __init__(self, connection):
        self.connection = connection
        self.fail_insert = False

    def exec(self, query, *bindings):
        if self.fail_insert and query.startswith("INSERT"):
            raise RuntimeError("injected storage failure")
        cursor = self.connection.execute(query, bindings)
        if cursor.description:
            names = [column[0] for column in cursor.description]
            rows = [dict(zip(names, row)) for row in cursor.fetchall()]
        else:
            rows = []
        return SimpleNamespace(toArray=lambda: rows)


class Storage:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:", isolation_level=None)
        self.sql = SQL(self.connection)
        self.alarm = None
        self.transactions = 0

    def transactionSync(self, callback):
        self.transactions += 1
        self.connection.execute("BEGIN")
        try:
            result = callback()
            self.connection.commit()
            return result
        except BaseException:
            self.connection.rollback()
            raise

    async def setAlarm(self, timestamp):
        self.alarm = timestamp

    async def deleteAlarm(self):
        self.alarm = None


class Namespace:
    def __init__(self):
        self.storages = {}
        self.names = []
        self.pre_rpc_reads = []
        self.env = SimpleNamespace()

    def getByName(self, token):
        self.names.append(token)
        if token not in self.storages:
            self.storages[token] = Storage()
        storage = self.storages[token]
        async def handle(request):
            self.pre_rpc_reads.append(request.reader.reads)
            # Every RPC recreates the class, with only SQLite surviving.
            actor = ENTRY.NornSession(SimpleNamespace(storage=storage), self.env)
            return await actor.handle(request)
        return SimpleNamespace(handle=handle)


def data(response):
    return json.loads(response.body)


class CloudflareEntryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from norn.actor_state import new_actor_service
        self.now = 1000
        self.clock_patch = patch("norn.actor_state.new_actor_service",
                                 side_effect=lambda state=None: new_actor_service(state, clock=lambda: self.now))
        self.clock_patch.start()
        self.addCleanup(self.clock_patch.stop)
        self.output = io.StringIO()
        self.logs = redirect_stdout(self.output)
        self.logs.__enter__()
        self.addCleanup(lambda: self.logs.__exit__(None, None, None))
        self.namespace = Namespace()
        self.addCleanup(lambda: [storage.connection.close() for storage in self.namespace.storages.values()])
        self.env = SimpleNamespace(NORN_SESSION=self.namespace,
                                   ASSETS=SimpleNamespace(fetch=self.asset_fetch))
        self.gateway = ENTRY.Default(SimpleNamespace(), self.env)
        self.cookie = None

    async def asset_fetch(self, request):
        return Response("asset", headers={"Content-Type": "text/plain"})

    async def fetch(self, path, method="GET", body=None, headers=None, chunks=None, cookie=True):
        values = {**({"Cookie": self.cookie} if cookie and self.cookie else {}), **(headers or {})}
        response = await self.gateway.fetch(Request(path, method, body, values, chunks))
        if cookie and response.headers.get("Set-Cookie"):
            self.cookie = response.headers.get("Set-Cookie").split(";", 1)[0]
        return response

    def journal(self):
        return json.loads((ROOT / "tests/fixtures/legacy-profile-v1.json").read_text())["journal"]

    async def reading(self):
        result = await self.fetch("/api/analyze", "POST", {"journal": self.journal(), "request_id": "reading-1"})
        self.assertEqual(result.status, 200, result.body)
        return data(result)

    async def test_cheap_health_static_and_gateway_do_not_read_request_body(self):
        health = await self.fetch("/api/health")
        self.assertEqual(data(health)["provider"], "mock")
        self.assertEqual(self.namespace.names, [])
        head = await self.fetch("/api/health", "HEAD")
        self.assertIsNone(head.body)
        asset = await self.fetch("/index.html")
        self.assertEqual(asset.body, "asset")
        response = await self.fetch("/api/preview", "POST", {"journal": {"schema_version": "norn.events.v1", "events": []}, "compact": True})
        self.assertEqual(response.status, 200)
        self.assertEqual(self.namespace.pre_rpc_reads, [0], "Only the DO may buffer/parse the body")
        self.assertEqual(data(response)["transport"], "norn.inspection.compact.v1")
        token = self.cookie.split("=", 1)[1]
        self.assertRegex(token, r"^[A-Za-z0-9_-]{43}$")
        cookie = health.headers.get("Set-Cookie")
        self.assertEqual(response.headers.get("Set-Cookie"), cookie,
                         "API activity renews the same browser routing capability")
        for flag in ("Path=/", "HttpOnly", "Secure", "SameSite=Strict", "Max-Age=1800"):
            self.assertIn(flag, cookie)
        for name, expected in ENTRY.SECURITY_HEADERS.items():
            self.assertEqual(response.headers.get(name), expected)

    async def test_frozen_session_and_retry_receipts_survive_every_rpc_recreation(self):
        reading = await self.reading()
        session = reading["session_id"]
        initial = data(await self.fetch(f"/api/sessions/{session}/inspect"))
        before = (await self.fetch(f"/api/sessions/{session}/export")).body
        retried = await self.fetch("/api/analyze", "POST", {"journal": self.journal(), "request_id": "reading-1"})
        self.assertEqual(data(retried), reading)
        for turn in range(1, 4):
            payload = {"session_id": session, "user_message": "为什么？", "turn_id": f"turn-{turn}"}
            response = await self.fetch("/api/dialogue", "POST", payload)
            self.assertEqual(response.status, 200, response.body)
            self.assertEqual(data(response)["turn_count"], turn)
            duplicate = await self.fetch("/api/dialogue", "POST", payload)
            self.assertEqual(data(duplicate), data(response))
        final = data(await self.fetch(f"/api/sessions/{session}/inspect"))
        self.assertEqual(len(final["calls"]), 4)
        self.assertEqual(final["context"], initial["context"])
        self.assertEqual(final["profile"], initial["profile"])
        self.assertEqual((await self.fetch(f"/api/sessions/{session}/export")).body, before)
        compact = data(await self.fetch(f"/api/sessions/{session}/inspect?compact=1"))
        self.assertNotIn("context", compact)
        self.assertEqual(compact["turn_count"], 3)
        self.assertEqual(compact["preference_map"], final["preference_map"])
        storage = self.namespace.storages[self.namespace.names[-1]]
        self.assertEqual(storage.alarm, 2800_000)
        self.assertGreater(storage.transactions, 0)

    async def test_optional_rating_persists_with_the_session_and_its_export(self):
        session = (await self.reading())["session_id"]
        before = data(await self.fetch(f"/api/sessions/{session}/export"))
        self.assertNotIn("post_session", before)
        rejected = await self.fetch("/api/feedback", "POST", {"session_id": session, "reading_rating": 3,
                                                             "closest_card_id": None, "journal": {}})
        self.assertEqual(rejected.status, 400)
        saved = await self.fetch("/api/feedback", "POST", {"session_id": session, "reading_rating": 3,
                                                          "closest_card_id": None})
        self.assertEqual(saved.status, 200, saved.body)
        # Every request recreates the actor service from durable storage.
        exported = data(await self.fetch(f"/api/sessions/{session}/export"))
        self.assertEqual(exported["post_session"]["reading_rating"], 3)
        self.assertEqual({key: exported[key] for key in before}, before)

    async def test_two_cookie_actors_cannot_read_each_others_session(self):
        first = await self.reading()
        owner_cookie = self.cookie
        response = await self.fetch(f"/api/sessions/{first['session_id']}/inspect", cookie=False)
        self.assertEqual(response.status, 404)
        self.assertEqual(len(self.namespace.storages), 2)
        self.cookie = owner_cookie
        self.assertEqual((await self.fetch(f"/api/sessions/{first['session_id']}/inspect")).status, 200)
        # Duplicate/invalid cookies never select an ambiguous existing actor.
        bad = owner_cookie + "; " + owner_cookie
        response = await self.fetch(f"/api/sessions/{first['session_id']}/inspect", headers={"Cookie": bad}, cookie=False)
        self.assertEqual(response.status, 404)
        self.assertEqual(len(self.namespace.storages), 3)

    async def test_pure_preview_and_import_never_overwrite_conversation_storage(self):
        reading = await self.reading()
        storage = self.namespace.storages[self.namespace.names[-1]]
        actor = ENTRY.NornSession(SimpleNamespace(storage=storage), self.env)
        before = actor._read_state()
        bundle = data(await self.fetch(f"/api/sessions/{reading['session_id']}/export"))
        preview = await self.fetch("/api/preview", "POST", {"journal": self.journal(), "compact": True})
        self.assertEqual(preview.status, 200)
        replay = await self.fetch("/api/replay", "POST", bundle)
        self.assertIs(data(replay)["verified"], True)
        self.assertEqual(actor._read_state(), before)

    async def test_trajectory_routes_are_pure_and_session_scoped(self):
        reading = await self.reading()
        storage = self.namespace.storages[self.namespace.names[-1]]
        before = ENTRY.NornSession(SimpleNamespace(storage=storage), self.env)._read_state()
        posted = await self.fetch("/api/trajectory", "POST", {"journal": self.journal()})
        self.assertEqual(posted.status, 200, posted.body)
        frozen = await self.fetch(f"/api/sessions/{reading['session_id']}/trajectory")
        self.assertEqual(frozen.status, 200, frozen.body)
        self.assertEqual(data(frozen)["states"], data(posted)["states"])
        self.assertEqual(data(frozen)["source"], "session")
        self.assertEqual((await self.fetch(f"/api/sessions/{reading['session_id']}/trajectory?x=1")).status, 404)
        after = ENTRY.NornSession(SimpleNamespace(storage=storage), self.env)._read_state()
        self.assertEqual(after, before, "Trajectory reads never rewrite conversation storage")

    async def test_terminal_operation_errors_are_persisted_and_deduplicated(self):
        reading = await self.reading()
        payload = {"session_id": reading["session_id"], "user_message": "继续", "turn_id": "blocked-turn"}
        for turn in range(3):
            await self.fetch("/api/dialogue", "POST", {**payload, "turn_id": f"turn-{turn}"})
        response = await self.fetch("/api/dialogue", "POST", payload)
        # A completed turn limit response is a stable operation result.
        self.assertEqual(response.status, 200)
        repeated = await self.fetch("/api/dialogue", "POST", payload)
        self.assertEqual(data(repeated), data(response))
        missing = {**payload, "session_id": "not-a-session", "turn_id": "missing-turn"}
        first = await self.fetch("/api/dialogue", "POST", missing)
        second = await self.fetch("/api/dialogue", "POST", missing)
        self.assertEqual(first.status, 404)
        self.assertEqual(data(first), data(second))
        actor = ENTRY.NornSession(SimpleNamespace(storage=self.namespace.storages[self.namespace.names[-1]]), self.env)
        state = actor._read_state()
        receipt = next(row for row in state["operations"] if row["key"] == "missing-turn")
        self.assertEqual(receipt["error"][1], 404)

    async def test_alarm_prunes_expired_data_and_reschedules_remaining_receipts(self):
        reading = await self.reading()
        storage = self.namespace.storages[self.namespace.names[-1]]
        self.now = 1010
        await self.fetch("/api/dialogue", "POST", {"session_id": reading["session_id"],
                                                  "user_message": "为什么？", "turn_id": "later-turn"})
        self.now = 2801
        actor = ENTRY.NornSession(SimpleNamespace(storage=storage), self.env)
        await actor.alarm()
        state = actor._read_state()
        self.assertEqual(state["sessions"], [])
        self.assertEqual([row["key"] for row in state["operations"]], ["later-turn"])
        self.assertEqual(storage.alarm, 2810_000)
        self.now = 2811
        await ENTRY.NornSession(SimpleNamespace(storage=storage), self.env).alarm()
        self.assertIsNone(actor._read_state())
        self.assertIsNone(storage.alarm)

    async def test_cookie_renewal_keeps_absolute_session_and_receipt_expiry(self):
        await self.fetch("/api/health")
        original_cookie = self.cookie
        self.now = 1010
        reading = await self.reading()
        storage = self.namespace.storages[self.namespace.names[-1]]
        actor = ENTRY.NornSession(SimpleNamespace(storage=storage), self.env)
        original = actor._read_state()
        self.assertEqual(original["sessions"][0]["expires_at"], 2810)
        self.assertEqual(storage.alarm, 2810_000)
        self.now = 2805
        renewed = await self.fetch("/api/health")
        self.assertEqual(self.cookie, original_cookie)
        self.assertIn("Max-Age=1800", renewed.headers.get("Set-Cookie"))
        self.assertEqual(actor._read_state(), original, "Cookie renewal never refreshes data TTL")
        self.assertEqual(storage.alarm, 2810_000)
        self.now = 2811
        expired = await self.fetch(f"/api/sessions/{reading['session_id']}/inspect")
        self.assertEqual(expired.status, 404)
        self.assertEqual(self.cookie, original_cookie)
        self.assertIn("Max-Age=1800", expired.headers.get("Set-Cookie"))
        await ENTRY.NornSession(SimpleNamespace(storage=storage), self.env).alarm()
        self.assertIsNone(actor._read_state())
        self.assertIsNone(storage.alarm)

    async def test_sql_chunks_are_bounded_and_failed_replace_is_atomic(self):
        storage = Storage()
        self.addCleanup(storage.connection.close)
        actor = ENTRY.NornSession(SimpleNamespace(storage=storage), self.env)
        state = {"long": "图像😀" * 80_000}
        actor._write_state(state)
        original = actor._read_state()
        rows = storage.sql.exec("SELECT part, payload FROM actor_state ORDER BY part").toArray()
        self.assertGreater(len(rows), 1)
        self.assertTrue(all(len(row["payload"].encode()) <= 256 * 1024 for row in rows))
        storage.sql.fail_insert = True
        with self.assertRaises(RuntimeError):
            actor._write_state({"replacement": "must roll back"})
        self.assertEqual(actor._read_state(), original)
        storage.sql.fail_insert = False
        with self.assertRaises(ENTRY.HTTPError):
            actor._write_state({"large": "x" * (ENTRY.MAX_STATE_BYTES + 1)})
        self.assertEqual(actor._read_state(), original)

    async def test_stream_limits_strict_json_origin_and_query_rejections(self):
        for raw in (b'{"journal":{},"journal":{}}', b'{"x":NaN}', b'{"x":Infinity}',
                    b'{"x":1e400}', b'[]', b'"scalar"', b'{"x":"\xff"}'):
            with self.subTest(raw=raw):
                response = await self.fetch("/api/preview", "POST", raw)
                self.assertEqual(response.status, 400)
        request = Request("/api/preview", "POST", chunks=[b"x" * (ENTRY.MAX_REQUEST_BYTES + 1)])
        response = await self.gateway.fetch(request)
        self.assertEqual(response.status, 413)
        self.assertTrue(request.reader.cancelled)
        self.assertTrue(request.reader.released)
        declared = Request("/api/preview", "POST", b"{}", headers={"Content-Length": str(ENTRY.MAX_REQUEST_BYTES + 1)})
        self.assertEqual((await self.gateway.fetch(declared)).status, 413)
        self.assertEqual(declared.reader.reads, 0)
        self.assertEqual((await self.fetch("/api/preview", "POST", b"{}", headers={"Content-Encoding": "gzip"})).status, 415)
        self.assertEqual((await self.fetch("/api/preview", "POST", b"{}", headers={"Content-Type": "text/plain"})).status, 415)
        count = len(self.namespace.names)
        cross = await self.fetch("/api/analyze", "POST", {}, headers={"Origin": "https://attacker.example"})
        self.assertEqual(cross.status, 403)
        self.assertEqual(len(self.namespace.names), count)
        provider = await self.fetch("/api/analyze", "POST", {"provider": "openai-compatible"})
        self.assertEqual(provider.status, 400)
        for suffix in ("?compact=0", "?compact=1&compact=1", "?provider=mock"):
            response = await self.fetch("/api/sessions/missing/inspect" + suffix)
            self.assertEqual(response.status, 400)

    async def test_structured_logs_omit_private_values_and_request_paths(self):
        await self.fetch("/api/secret-personal-route", "POST", {"question": "private-question"},
                         headers={"Cookie": "private-cookie"})
        logs = self.output.getvalue()
        self.assertNotIn("private-question", logs)
        self.assertNotIn("private-cookie", logs)
        self.assertNotIn("secret-personal-route", logs)
        for line in logs.splitlines():
            row = json.loads(line)
            self.assertEqual(set(row), {"event", "method", "status", "elapsed_ms"})

    async def test_corrupt_storage_fails_closed_without_resetting_it(self):
        await self.fetch("/api/health")
        token = self.cookie.split("=", 1)[1]
        storage = Storage()
        self.namespace.storages[token] = storage
        actor = ENTRY.NornSession(SimpleNamespace(storage=storage), self.env)
        storage.sql.exec("INSERT INTO actor_state (part, payload) VALUES (?, ?)", 0, "private-corrupt-state")
        response = await self.fetch("/api/sessions/unknown/inspect")
        self.assertEqual(response.status, 503)
        self.assertNotIn("private-corrupt-state", response.body)
        rows = storage.sql.exec("SELECT part, payload FROM actor_state ORDER BY part").toArray()
        self.assertEqual(rows[0]["payload"], "private-corrupt-state")
        logs = self.output.getvalue()
        self.assertNotIn("private-corrupt-state", logs)
        failure = next(json.loads(line) for line in logs.splitlines()
                       if json.loads(line).get("error_type"))
        self.assertEqual(failure["error_type"], "ValueError")
        self.assertTrue(failure["frames"])
        self.assertTrue(all(set(frame) == {"file", "line"} for frame in failure["frames"]))

    async def test_durable_object_restores_runtime_files_at_each_entry_boundary(self):
        storage = Storage()
        self.addCleanup(storage.connection.close)
        with patch.object(ENTRY, "bootstrap") as bootstrap:
            actor = ENTRY.NornSession(SimpleNamespace(storage=storage), self.env)
            self.assertEqual(bootstrap.call_count, 1)
            response = await actor.handle(Request("/api/preview", "POST", {
                "journal": {"schema_version": "norn.events.v1", "events": []}, "compact": True}))
            self.assertEqual(response.status, 200)
            self.assertEqual(bootstrap.call_count, 2)
            await actor.alarm()
            self.assertEqual(bootstrap.call_count, 3)


if __name__ == "__main__":
    unittest.main()

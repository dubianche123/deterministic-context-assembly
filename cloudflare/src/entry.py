"""Hosted Norn: cheap gateway, isolated sessions, and bounded language calls.

The gateway forwards the Request over RPC without parsing or buffering its
body. All event replay, learning, strict JSON parsing and conversation work run
inside the browser's Durable Object rather than the short-lived router.
"""

from norn_bundle import bootstrap

bootstrap()

import asyncio
import json
import math
import re
import secrets
import time
from urllib.parse import parse_qs, unquote, urlsplit

from workers import DurableObject, Response, WorkerEntrypoint


MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_STATE_BYTES = 16 * 1024 * 1024
# At most four UTF-8 bytes per code point: each TEXT chunk stays <=256 KiB.
CHUNK_CHARACTERS = 64 * 1024
MAX_CHUNKS = MAX_STATE_BYTES // CHUNK_CHARACTERS
COOKIE_NAME = "__Host-norn-actor"
COOKIE_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; media-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
}
POST_ROUTES = {"/api/analyze", "/api/dialogue", "/api/preview", "/api/replay", "/api/feedback", "/api/trajectory", "/api/record/withdraw", "/api/record/partial", "/api/jev/start", "/api/jev/predict"}
ARCHIVE_NAME = "records-v2"
EXPORT_PAGE_MAX = 500


class HTTPError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _json_response(value, status=200, *, head=False, headers=None):
    return Response(None if head else json.dumps(value, ensure_ascii=False, allow_nan=False,
                                                separators=(",", ":")), status=status,
                    headers={**SECURITY_HEADERS, "Content-Type": "application/json; charset=utf-8",
                             **(headers or {})})


def _error_response(error, *, head=False, headers=None):
    return _json_response({"error": str(error),
                           "status": "internal_error" if error.status >= 500 else "request_error"},
                          error.status, head=head, headers=headers)


def _request_location(request):
    parsed = urlsplit(request.url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPError("请求地址无效")
    origin = request.headers.get("Origin")
    if origin and origin.lower() != f"{parsed.scheme}://{parsed.netloc}".lower():
        raise HTTPError("仅允许同源请求", 403)
    host = request.headers.get("Host")
    if host and host.lower() != parsed.netloc.lower():
        raise HTTPError("请求 Host 无效", 403)
    try:
        path = unquote(parsed.path, errors="strict")
    except UnicodeError as exc:
        raise HTTPError("请求路径无效") from exc
    if "\\" in path or "\x00" in path:
        raise HTTPError("请求路径无效")
    return path, parsed.query


def _actor_token(cookie):
    values = []
    for item in (cookie or "").split(";"):
        key, separator, value = item.strip().partition("=")
        if separator and key == COOKIE_NAME:
            values.append(value)
    # Duplicate cookie names are ambiguous capabilities, so never choose one.
    if len(values) == 1 and COOKIE_TOKEN.fullmatch(values[0]):
        return values[0], False
    return secrets.token_urlsafe(32), True


def _cookie(token):
    return f"{COOKIE_NAME}={token}; Path=/; Max-Age=1800; HttpOnly; Secure; SameSite=Strict"


def _event(name, **fields):
    """Same JSON-lines shape as the local logger; never request content."""
    print(json.dumps({"event": name, **fields}, separators=(",", ":")))


def _log(method, status, started, error=None):
    # Never include path/session identifiers, headers, bodies or user questions.
    record = {"event": "norn_request", "method": method, "status": status,
              "elapsed_ms": round((time.perf_counter() - started) * 1000, 3)}
    if error is not None:
        # Inspect code locations directly: traceback formatters include source
        # lines and exception messages, which can contain private request data.
        frames = []
        trace = error.__traceback__
        while trace is not None:
            frames.append({"file": trace.tb_frame.f_code.co_filename.rsplit("/", 1)[-1],
                           "line": trace.tb_lineno})
            trace = trace.tb_next
        record.update(error_type=type(error).__name__, frames=frames[-8:])
    print(json.dumps(record, separators=(",", ":")))


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        started = time.perf_counter()
        method = request.method.upper()
        cookie_headers = None
        failure = None
        try:
            path, query = _request_location(request)
            if path.startswith("/api/"):
                token, _ = _actor_token(request.headers.get("Cookie"))
                # Routing capability lifetime follows browser activity. This
                # never changes any persisted session/receipt expiry or alarm.
                cookie_headers = {"Set-Cookie": _cookie(token)}
            if path == "/api/health":
                if method not in ("GET", "HEAD") or query:
                    raise HTTPError("健康检查仅支持 GET 或 HEAD", 405)
                provider = str(getattr(self.env, "NORN_PROVIDER", "mock"))
                live = provider == "openai-compatible"
                ready = not live or bool(str(getattr(self.env, "NORN_API_KEY", "")))
                response = _json_response({"status": "ok" if ready else "unavailable", "provider": provider,
                                           "model": "deepseek-flash" if live else None,
                                           "simulated": not live, "offline": not live, "deployment": "cloudflare",
                                           "session_ttl_seconds": 1800}, head=method == "HEAD",
                                          status=200 if ready else 503, headers=cookie_headers)
            elif path == "/api/archive/export":
                response = await _export_records(self.env, request, query, method, cookie_headers)
            elif path.startswith("/api/"):
                stub = self.env.NORN_SESSION.getByName(token)
                # RPC supports Request/Response with streamed, byte-oriented
                # bodies. The router never calls request.text()/json().
                remote = await stub.handle(request)
                headers = dict(SECURITY_HEADERS)
                for name in ("Content-Type", "Content-Disposition"):
                    value = remote.headers.get(name)
                    if value:
                        headers[name] = value
                headers.update(cookie_headers)
                response = Response(remote.body, status=remote.status, headers=headers)
            else:
                # Normal assets bypass this entrypoint through Static Assets.
                response = await self.env.ASSETS.fetch(request)
        except HTTPError as exc:
            response = _error_response(exc, head=method == "HEAD", headers=cookie_headers)
        except Exception as exc:
            failure = exc
            response = _error_response(HTTPError("服务暂时不可用，请稍后重试。", 503),
                                       head=method == "HEAD", headers=cookie_headers)
        _log(method, response.status, started, failure)
        return response


async def _export_records(env, request, query, method, headers):
    """Operator-only NDJSON pages: `Authorization: Bearer $NORN_ARCHIVE_TOKEN`."""
    import hmac
    secret = str(getattr(env, "NORN_ARCHIVE_TOKEN", "") or "")
    supplied = request.headers.get("Authorization") or ""
    if method != "GET" or not secret or not hasattr(env, "NORN_ARCHIVE"):
        raise HTTPError("接口不存在", 404)
    if not hmac.compare_digest(supplied.encode(), f"Bearer {secret}".encode()):
        raise HTTPError("需要导出凭证", 401)
    fields = parse_qs(query, keep_blank_values=True)
    if set(fields) - {"after", "limit"} or any(len(value) != 1 for value in fields.values()):
        raise HTTPError("导出只支持 after 与 limit")
    after = fields.get("after", [""])[0]
    limit = fields.get("limit", [str(EXPORT_PAGE_MAX)])[0]
    if not re.fullmatch(r"[0-9a-f]{0,32}", after) or not limit.isdecimal() or not 1 <= int(limit) <= EXPORT_PAGE_MAX:
        raise HTTPError("导出分页参数无效")
    text = str(await env.NORN_ARCHIVE.getByName(ARCHIVE_NAME).page(after, int(limit)))
    _event("records.exported", lines=text.count("\n"))
    return Response(text, headers={**SECURITY_HEADERS, **(headers or {}),
                                   "Content-Type": "application/x-ndjson; charset=utf-8"})


def _duplicates(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise HTTPError("JSON 对象包含重复字段")
        result[name] = value
    return result


def _nonfinite(value):
    raise HTTPError("JSON 不支持非有限数字")


def _float(value):
    number = float(value)
    if not math.isfinite(number):
        _nonfinite(value)
    return number


def _strict_json(raw):
    try:
        body = json.loads(raw.decode("utf-8"), object_pairs_hook=_duplicates,
                          parse_constant=_nonfinite, parse_float=_float)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise HTTPError("无效 JSON 请求体") from exc
    if type(body) is not dict:
        raise HTTPError("请求必须是 JSON 对象")
    return body


def _bytes(chunk):
    return bytes(chunk.to_py()) if hasattr(chunk, "to_py") else bytes(chunk)


async def _read_body(request):
    length = request.headers.get("Content-Length")
    if length is not None:
        if not length.isascii() or not length.isdecimal():
            raise HTTPError("Content-Length 无效")
        if len(length) > 10 or int(length) > MAX_REQUEST_BYTES:
            raise HTTPError("请求体过大", 413)
    if (request.headers.get("Content-Encoding") or "identity").lower() != "identity":
        raise HTTPError("不支持压缩请求体", 415)
    content_type = (request.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise HTTPError("Content-Type 必须是 application/json", 415)
    chunks = []
    total = 0
    body = request.body
    if body is not None:
        reader = body.getReader()
        try:
            while True:
                part = await reader.read()
                if part.done:
                    break
                chunk = _bytes(part.value)
                total += len(chunk)
                if total > MAX_REQUEST_BYTES:
                    await reader.cancel()
                    raise HTTPError("请求体过大", 413)
                chunks.append(chunk)
        finally:
            reader.releaseLock()
    if length is not None and total != int(length):
        raise HTTPError("请求体不完整")
    return b"".join(chunks)


def _column(row, name):
    return row[name] if isinstance(row, dict) else getattr(row, name)


def _plain_rows(cursor):
    """DO SQL rows -> tuples in column order, for the shared collection SQL."""
    rows = []
    for row in cursor.toArray():
        row = row.to_py() if hasattr(row, "to_py") else row
        rows.append(tuple(row.values()) if isinstance(row, dict) else tuple(row))
    return rows


class NornArchive(DurableObject):
    """One global SQLite archive of consented anonymous records."""

    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        bootstrap()
        from norn.collection import SCHEMA_SQL
        for statement in SCHEMA_SQL:
            self.ctx.storage.sql.exec(statement)

    def _execute(self, sql, *params):
        return _plain_rows(self.ctx.storage.sql.exec(sql, *params))

    async def apply(self, operation_json):
        from norn.collection import apply_operation
        operation = json.loads(str(operation_json))
        try:
            result = self.ctx.storage.transactionSync(lambda: apply_operation(self._execute, operation))
        except ValueError as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
        return json.dumps({"ok": bool(result)})

    async def page(self, after, limit):
        from norn.collection import PAGE_SQL
        lines = []
        for _, visit, payload, notes in self._execute(PAGE_SQL, str(after), int(limit)):
            lines.append(json.dumps({**json.loads(payload), "visit": visit, "annotations": json.loads(notes)},
                                    ensure_ascii=False, sort_keys=True))
        return "".join(line + "\n" for line in lines)


class NornSession(DurableObject):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        # Cloud snapshots preserve Python globals but /tmp may be recreated.
        # Restore runtime files inside the DO's CPU budget, never in the router.
        bootstrap()
        self._lock = asyncio.Lock()
        self.ctx.storage.sql.exec("CREATE TABLE IF NOT EXISTS actor_state (part INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
        from norn.cloud_quota import PaidReceipts
        self._receipts = PaidReceipts(self.ctx.storage.sql, self.ctx.storage.transactionSync)
        from norn.jev_live import JevStore
        self._jev = JevStore(self.ctx.storage.sql, self.ctx.storage.transactionSync)

    def _read_state(self):
        rows = self.ctx.storage.sql.exec("SELECT part, payload FROM actor_state ORDER BY part").toArray()
        chunks = []
        total = 0
        for index, row in enumerate(rows):
            part, payload = _column(row, "part"), _column(row, "payload")
            if part != index or type(payload) is not str or index >= MAX_CHUNKS:
                raise ValueError("Invalid actor storage chunks")
            length = len(payload.encode("utf-8"))
            total += length
            if length > 256 * 1024 or total > MAX_STATE_BYTES:
                raise ValueError("Actor storage exceeds bounds")
            chunks.append(payload)
        if not chunks:
            return None
        try:
            return _strict_json("".join(chunks).encode("utf-8"))
        except HTTPError as exc:
            # A damaged persisted snapshot is a server failure, never a bad
            # browser request. Keep it intact rather than silently resetting.
            raise ValueError("Invalid actor JSON state") from exc

    def _write_state(self, state, *, atomic=True):
        from norn.events import canonical_json
        payload = canonical_json(state)
        if len(payload.encode("utf-8")) > MAX_STATE_BYTES:
            raise HTTPError("会话记录已达容量上限，请重新开始。", 413)
        chunks = [payload[index:index + CHUNK_CHARACTERS]
                  for index in range(0, len(payload), CHUNK_CHARACTERS)]
        if len(chunks) > MAX_CHUNKS:
            raise HTTPError("会话记录已达容量上限，请重新开始。", 413)
        def replace():
            self.ctx.storage.sql.exec("DELETE FROM actor_state")
            for index, chunk in enumerate(chunks):
                self.ctx.storage.sql.exec("INSERT INTO actor_state (part, payload) VALUES (?, ?)", index, chunk)
        # No awaits between the related writes. A failed insert rolls back the
        # delete and leaves the last complete snapshot available after eviction.
        if atomic:
            self.ctx.storage.transactionSync(replace)
        else:
            replace()

    async def _schedule_state(self, service):
        from norn.actor_state import dump_actor_state, next_expiry
        expiries = [value for value in (next_expiry(dump_actor_state(service)),
                                        self._receipts.next_expiry(), self._jev.next_expiry()) if value is not None]
        if expiries:
            await self.ctx.storage.setAlarm(math.ceil(min(expiries) * 1000))
        else:
            await self.ctx.storage.deleteAlarm()

    async def _persist(self, service):
        from norn.actor_state import dump_actor_state, next_expiry
        state = dump_actor_state(service)
        expiry = next_expiry(state) or self._receipts.next_expiry()
        if expiry is None:
            self.ctx.storage.transactionSync(lambda: self.ctx.storage.sql.exec("DELETE FROM actor_state"))
        else:
            self._write_state(state)
        await self._schedule_state(service)

    async def handle(self, request):
        """Private RPC; the browser can only reach it through the cookie router."""
        started = time.perf_counter()
        method = request.method.upper()
        failure = None
        try:
            bootstrap()
            path, query = _request_location(request)
            if method == "POST":
                if path not in POST_ROUTES or query:
                    raise HTTPError("接口不存在", 404)
                body = _strict_json(await _read_body(request))
            elif method in ("GET", "HEAD"):
                body = None
            else:
                raise HTTPError("请求方法不支持", 405)
            if path.startswith("/api/jev/"):
                # Jev has its own synchronous reservation transactions. Waiting
                # for its optional network call must not block normal play,
                # archival checkpoints or an ordinary reading in this actor.
                response = await self._dispatch_jev(method, path, query, body)
            else:
                async with self._lock:
                    response = await self._dispatch(method, path, query, body)
        except HTTPError as exc:
            response = _error_response(exc, head=method == "HEAD")
        except Exception as exc:
            failure = exc
            response = _error_response(HTTPError("会话暂时不可用，请稍后重试。", 503), head=method == "HEAD")
        _log(method, response.status, started, failure)
        return response

    async def _dispatch_jev(self, method, path, query, body):
        from norn.jev_live import configured, predict, status, validate_start, validate_predict_body
        from norn.service import RequestError
        from norn.core import engine_module
        key = str(getattr(self.env, "NORN_JEV_API_KEY", "") or "")
        name = str(getattr(self.env, "NORN_DECK", "") or "")
        deck = engine_module("research_deck").load_deck(name) if name else None
        usable_key = key if deck is not None and deck.context is None else ""
        try:
            if query:
                raise RequestError("Jev endpoints do not accept query parameters")
            if method in ("GET", "HEAD") and path == "/api/jev/status":
                return _json_response(status(usable_key), head=method == "HEAD")
            if method != "POST" or path not in ("/api/jev/start", "/api/jev/predict"):
                raise RequestError("Jev endpoint does not exist", 404)
            if path == "/api/jev/start":
                validate_start(body, deck)
            else:
                validate_predict_body(body)
            if not configured(usable_key):
                return _json_response({"status": "unavailable", **status(""), "reason": "not_configured"})
            if path == "/api/jev/start":
                result = self._jev.start(body, deck)
            else:
                remote_quota = self.env.NORN_BUDGET.getByName("jev-play-v1")
                class Quota:
                    async def reserve(inner, attempt):
                        return await remote_quota.reserve_jev(attempt)
                    async def release(inner, attempt):
                        return await remote_quota.release(attempt)
                result = await predict(self._jev, body, deck, key, actor_id=str(self.ctx.id.toString()), quota=Quota())
            return _json_response(result)
        except RequestError as exc:
            raise HTTPError(str(exc), exc.status) from exc
        finally:
            # Preserve any earlier conversation/paid-receipt deadline while
            # adding the independent Jev capability's fixed absolute expiry.
            from norn.actor_state import next_expiry
            state = self._read_state()
            values = [value for value in (next_expiry(state) if state is not None else None,
                      self._receipts.next_expiry(), self._jev.next_expiry()) if value is not None]
            if values:
                await self.ctx.storage.setAlarm(math.ceil(min(values) * 1000))
            else:
                await self.ctx.storage.deleteAlarm()

    def _actor_service(self, state=None, **kwargs):
        from norn.actor_state import new_actor_service
        from norn.collection import OutboxCollector
        # A research deployment names its deck (NORN_DECK); Norn's own deployment has none.
        deck = str(getattr(self.env, "NORN_DECK", ""))
        service = new_actor_service(state, **({"deck": deck} if deck else {}), **kwargs)
        if hasattr(self.env, "NORN_ARCHIVE"):
            # Record operations queue during the synchronous service call and
            # are applied to the archive before the reply leaves this actor.
            service.collection, service.deployment = OutboxCollector(), "cloudflare"
        return service

    async def _flush_records(self, service, result):
        """Apply queued record operations; a lost write never costs the reading.

        An RPC that raises may have committed before its reply was lost, so its
        outcome is unknown, not failed. Every operation is idempotent and is
        retried once. A record write still unknown is settled by withdrawing
        it: only a confirmed withdrawal lets the reply say nothing was kept.
        Otherwise the receipt stays, flagged unconfirmed, so the visitor can
        still withdraw a record that may exist.
        """
        from norn.collection import OutboxCollector
        if not isinstance(service.collection, OutboxCollector):
            return
        archive = self.env.NORN_ARCHIVE.getByName(ARCHIVE_NAME)

        async def apply(operation):
            try:
                return json.loads(str(await archive.apply(json.dumps(operation, ensure_ascii=False))))
            except Exception as exc:
                return {"ok": None, "error": None, "error_type": type(exc).__name__}

        lost = False
        for operation in service.collection.drain():
            outcome = await apply(operation)
            if outcome.get("ok") is None:
                outcome = await apply(operation)
            _event("record." + operation["op"], ok=outcome.get("ok") is True,
                   **({"error_type": outcome["error_type"]} if "error_type" in outcome else {}))
            if outcome.get("ok") is True or operation["op"] == "annotate":
                continue
            if operation["op"] == "put_partial":
                # Queuing is not an archive acknowledgement. Rejected, failed
                # and uncertain partial writes must never claim stored:true.
                if isinstance(result, dict):
                    result["stored"] = False
                    if outcome.get("ok") is None:
                        # Retraction here could delete another actor's newer
                        # snapshot of the same visit. Keep the uncertainty and
                        # the visitor's withdrawal capability explicit instead.
                        result["unconfirmed"] = True
                continue
            if operation["op"] == "put":
                record = operation["record"]
                if outcome.get("ok") is None:
                    check = await apply({"op": "withdraw", "record_id": record["record_id"],
                                         "visitor_id": record["visitor_id"]})
                    _event("record.put_retracted", ok=check.get("ok") is True)
                    if check.get("ok") is not True:
                        if isinstance(result, dict) and isinstance(result.get("record"), dict):
                            result["record"]["unconfirmed"] = True
                        service.mark_record_unconfirmed(record["record_id"], session_id=result.get("session_id"))
                        continue
                lost = True
                if isinstance(result, dict):
                    result["record"] = None
                service.drop_record(record["record_id"], session_id=result.get("session_id"))
            elif operation["op"] in ("withdraw", "forget"):
                raise HTTPError(outcome.get("error") or "撤回暂时没有完成，请重试。",
                                403 if outcome.get("error") else 503)
        return lost

    async def _dispatch(self, method, path, query, body):
        from norn.service import RequestError
        head = method == "HEAD"
        pure = path in ("/api/preview", "/api/replay", "/api/trajectory") and method == "POST"
        # Pure work is deliberately detached from all persisted conversations.
        service = self._actor_service() if pure else self._actor_service(self._read_state())
        mutates = method == "POST" and path in ("/api/analyze", "/api/dialogue", "/api/feedback")
        try:
            if method == "POST":
                allowed = {"/api/analyze": {"journal", "request_id", "consent", "visitor", "probe", "language", "session_key", "experience", "total_rounds", "selections", "deselection_events", "round_durations"},
                           "/api/dialogue": {"session_id", "user_message", "turn_id"},
                           "/api/feedback": {"session_id", "reading_rating", "closest_card_id"},
                           "/api/record/withdraw": {"visitor", "record_id", "all"},
                           "/api/record/partial": {"consent", "visitor", "session_key", "journal", "open_round", "experience"}}
                if path in allowed and set(body) - allowed[path]:
                    raise HTTPError("请求包含不支持的字段")
                # A rating only annotates a stored session: no model route.
                if mutates and path != "/api/feedback":
                    from norn.cloud_host import model_route, HostedGenerator
                    route = model_route(self.env)
                    if path == "/api/dialogue":
                        session_id = body.get("session_id")
                        route = {"provider": "mock"}
                        if type(session_id) is str and session_id and len(session_id) <= 128:
                            try:
                                route = service.sessions.get(session_id).generation_route or route
                            except RequestError:
                                # Keep validation and terminal error receipts in
                                # the original no-dispatch dialogue boundary.
                                pass
                    if route["provider"] != "mock":
                        from norn.actor_state import DEEPSEEK_GENERATION_ROUTE
                        if route != DEEPSEEK_GENERATION_ROUTE or model_route(self.env) != route:
                            raise HTTPError("本次会话的模型配置已变更，请重新生成判词。", 503)
                        service = self._actor_service(self._read_state(), provider="openai-compatible",
                                                      config={"base_url": route["base_url"], "model": route["model"]})
                        identity = str(self.ctx.id.toString())
                        host = HostedGenerator(service, self._receipts,
                            self.env.NORN_BUDGET.getByName("deepseek-public-v1"), actor_id=identity,
                            api_key=str(getattr(self.env, "NORN_API_KEY", "")),
                            write_snapshot=self._write_state, schedule=self._schedule_state,
                            before_settle=lambda result: self._flush_records(service, result))
                        # The hosted path commits snapshot and receipt together,
                        # after the archive write, so both carry its real outcome.
                        # Never persist a half-finished generation in finally.
                        mutates = False
                        settled = await (host.analyze(body) if path == "/api/analyze" else host.dialogue(body))
                        return _json_response(settled["body"], settled["status"])
                    service.provider = "mock"
                action = {"/api/analyze": service.analyze, "/api/dialogue": service.dialogue,
                          "/api/preview": service.preview, "/api/replay": service.replay,
                          "/api/feedback": service.record_feedback, "/api/trajectory": service.trajectory,
                          "/api/record/withdraw": service.withdraw_record,
                          "/api/record/partial": service.record_partial}[path]
                result = action(body)
                await self._flush_records(service, result)
                status = {"provider_unavailable": 503, "invalid_output": 502,
                          "policy_blocked": 422, "internal_error": 500}.get(result.get("status"), 200)
                if status != 200:
                    result["error"] = result.get("reading") or result.get("reply") or "生成暂时不可用，请重试。"
                return _json_response(result, status)
            parts = path.strip("/").split("/")
            if len(parts) != 4 or parts[:2] != ["api", "sessions"]:
                raise HTTPError("接口不存在", 404)
            if parts[3] == "inspect":
                fields = parse_qs(query, keep_blank_values=True)
                if fields and fields != {"compact": ["1"]}:
                    raise HTTPError("检查请求仅支持 compact=1")
                inspected = service.inspect_session(parts[2], compact=bool(fields))
                inspected["limits"].update(scope="browser_actor", resets_on_restart=False,
                                           shared_daily_max_model_calls=100, shared_max_concurrency=4)
                return _json_response(inspected, head=head)
            if parts[3] == "trajectory" and not query:
                return _json_response(service.session_trajectory(parts[2]), head=head)
            if parts[3] == "export" and not query:
                bundle = service.export_session(parts[2])
                digest = bundle["profile"]["profile_hash"].removeprefix("sha256:")[:12]
                return Response(None if head else json.dumps(bundle, ensure_ascii=False, allow_nan=False, indent=2),
                                headers={**SECURITY_HEADERS, "Content-Type": "application/json; charset=utf-8",
                                         "Content-Disposition": f'attachment; filename="norn-replay-{digest}.json"'})
            raise HTTPError("接口不存在", 404)
        except RequestError as exc:
            raise HTTPError(str(exc), exc.status) from exc
        finally:
            if mutates:
                # This includes terminal error receipts: lost replies can be
                # retried after eviction without consuming a second turn.
                await self._persist(service)

    async def alarm(self):
        bootstrap()
        async with self._lock:
            service = self._actor_service(self._read_state())
            await self._persist(service)


class NornBudget(DurableObject):
    """A short shared admission transaction, without conversation or secrets."""
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        bootstrap()
        from norn.cloud_quota import QuotaStore
        self._quota = QuotaStore(ctx.storage.sql, ctx.storage.transactionSync)

    async def reserve(self, attempt_id):
        return json.dumps(self._quota.reserve(attempt_id), separators=(",", ":"))

    async def reserve_jev(self, attempt_id):
        # Called only on getByName('jev-play-v1'), never DeepSeek's bucket.
        return json.dumps(self._quota.reserve(attempt_id, max_daily=100, max_active=2), separators=(",", ":"))

    async def release(self, attempt_id):
        return json.dumps(self._quota.release(attempt_id), separators=(",", ":"))

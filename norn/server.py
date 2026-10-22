"""Small same-origin HTTP service; loopback unless hosting is explicit."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import mimetypes
from pathlib import Path
import time
from urllib.parse import parse_qs, unquote, urlsplit

from .core import web_root
from .logs import event
from .service import NornService, RequestError


MAX_REQUEST_BYTES = 8 * 1024 * 1024
LOGGER = logging.getLogger("norn.server")


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON 对象包含重复字段")
        result[key] = value
    return result


def _no_constant(value):
    raise ValueError("JSON 不支持非有限数字")


class NornHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class NornHandler(BaseHTTPRequestHandler):
    server_version = "Norn"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, format, *args):
        # The default access line contains raw URLs; _access_log replaces it.
        pass

    def _route(self):
        """Route template for logs: never a session ID, file name or query."""
        path = urlsplit(self.path).path
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["api", "sessions"]:
            return f"/api/sessions/:id/{parts[3]}"
        return path if path.startswith("/api/") or path == "/runtime-config.js" else "static"

    def _access_log(self, started):
        status = getattr(self, "_status", 0)
        if status == 200 and self._route() == "static":
            return  # card images and assets would drown the useful lines
        event(LOGGER, "http.request", method=self.command, route=self._route(), status=status,
              elapsed_ms=round((time.perf_counter() - started) * 1000, 1))

    def _send(self, status, content, content_type, head=False, headers=None):
        self._status = status
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; media-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if not head:
            self.wfile.write(content)

    def _json(self, status, body, head=False):
        content = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._send(status, content, "application/json; charset=utf-8", head)

    def _local_origin(self):
        allowed = self.server.allowed_hosts
        if len(self.headers.get_all("Host", [])) != 1 or self.headers.get("Host", "").lower() not in allowed:
            raise RequestError("请求 Host 不在服务允许范围", 403)
        origin = self.headers.get("Origin")
        # HTTPS may terminate at an explicitly configured reverse proxy.
        host = self.headers.get("Host", "").lower()
        if (len(self.headers.get_all("Origin", [])) > 1
                or origin and origin.lower() not in {scheme + host for scheme in ("http://", "https://")}):
            raise RequestError("仅允许同源请求", 403)

    def do_HEAD(self):
        started = time.perf_counter()
        self._get(head=True)
        self._access_log(started)

    def do_GET(self):
        started = time.perf_counter()
        self._get()
        self._access_log(started)

    def _get(self, head=False):
        try:
            self._local_origin()
            path = unquote(urlsplit(self.path).path, errors="strict")
            if path == "/api/jev/status":
                if urlsplit(self.path).query:
                    raise RequestError("Jev status does not accept query parameters")
                self._json(200, self.server.jev_live.status(), head)
                return
            if path == "/api/health":
                self._json(200, self.server.service.health(), head)
                return
            if path == "/runtime-config.js":
                from .collection import CONSENT_VERSION
                service = self.server.service
                config = {"apiEndpoint": "/api", "provider": service.provider,
                          "offline": service.provider == "mock",
                          "collect": service.collection.enabled, "consentVersion": CONSENT_VERSION,
                          **({"deck": service.deck.name} if service.deck is not None else {})}
                content = ("window.__NORN_CONFIG__ = " + json.dumps(config) + ";\n").encode("utf-8")
                self._send(200, content, "application/javascript; charset=utf-8", head)
                return
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "sessions"] and parts[3] == "inspect":
                query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                if query and query != {"compact": ["1"]}:
                    raise RequestError("检查请求仅支持 compact=1")
                self._json(200, self.server.service.inspect_session(parts[2], compact=bool(query)), head)
                return
            if len(parts) == 4 and parts[:2] == ["api", "sessions"] and parts[3] == "trajectory":
                if urlsplit(self.path).query:
                    raise RequestError("轨迹请求不接受查询参数")
                self._json(200, self.server.service.session_trajectory(parts[2]), head)
                return
            if len(parts) == 4 and parts[:2] == ["api", "sessions"] and parts[3] == "export":
                bundle = self.server.service.export_session(parts[2])
                content = json.dumps(bundle, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
                digest = bundle["profile"]["profile_hash"].removeprefix("sha256:")[:12]
                self._send(200, content, "application/json; charset=utf-8", head,
                           {"Content-Disposition": f'attachment; filename="norn-replay-{digest}.json"'})
                return
            if path.startswith("/api/"):
                raise RequestError("接口不存在", 404)
            if "\\" in path or "\x00" in path or any(part.startswith(".") for part in path.split("/") if part):
                raise RequestError("文件不存在", 404)
            relative = "index.html" if path == "/" else path.lstrip("/")
            target = (self.server.static_root / relative).resolve()
            if not target.is_relative_to(self.server.static_root) or not target.is_file():
                raise RequestError("文件不存在", 404)
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
                content_type += "; charset=utf-8"
            self._send(200, target.read_bytes(), content_type, head)
        except (UnicodeError, ValueError) as exc:
            self._json(getattr(exc, "status", 400), {"error": str(exc)}, head)
        except OSError:
            self._json(404, {"error": "文件不存在"}, head)

    def _read_json(self):
        if self.headers.get("Transfer-Encoding"):
            raise RequestError("不支持流式请求体", 400)
        lengths = self.headers.get_all("Content-Length", [])
        if not lengths:
            raise RequestError("需要 Content-Length", 411)
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdecimal():
            raise RequestError("Content-Length 无效", 400)
        if len(lengths[0]) > 10:
            raise RequestError("请求体过大", 413)
        length = int(lengths[0])
        if length > MAX_REQUEST_BYTES:
            raise RequestError("请求体过大", 413)
        if self.headers.get("Content-Encoding", "identity").lower() != "identity":
            raise RequestError("不支持压缩请求体", 415)
        if self.headers.get_content_type() != "application/json":
            raise RequestError("Content-Type 必须是 application/json", 415)
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise RequestError("请求体不完整", 400)
        try:
            body = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates, parse_constant=_no_constant)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise RequestError("无效 JSON 请求体") from exc
        if not isinstance(body, dict):
            raise RequestError("请求必须是 JSON 对象")
        return body

    def do_POST(self):
        started = time.perf_counter()
        try:
            self._post()
        finally:
            self._access_log(started)

    def _post(self):
        # Every response closes the connection; rejected bodies are never reused.
        self.close_connection = True
        try:
            self._local_origin()
            path = urlsplit(self.path).path
            if path.startswith("/api/jev/") and urlsplit(self.path).query:
                raise RequestError("Jev endpoints do not accept query parameters")
            routes = {"/api/analyze": self.server.service.analyze, "/api/dialogue": self.server.service.dialogue,
                      "/api/replay": self.server.service.replay, "/api/preview": self.server.service.preview,
                      "/api/trajectory": self.server.service.trajectory,
                      "/api/feedback": self.server.service.record_feedback,
                      "/api/record/withdraw": self.server.service.withdraw_record,
                      "/api/record/partial": self.server.service.record_partial,
                      "/api/jev/start": self.server.jev_live.start,
                      "/api/jev/predict": self.server.jev_live.predict}
            if path not in routes:
                raise RequestError("接口不存在", 404)
            body = self._read_json()
            result = routes[path](body)
            status = {"provider_unavailable": 503, "invalid_output": 502,
                      "policy_blocked": 422, "internal_error": 500}.get(result.get("status"), 200)
            if status != 200:
                result["error"] = result.get("reading") or result.get("reply") or "生成暂时不可用，请重试。"
            self._json(status, result)
        except RequestError as exc:
            self._json(exc.status, {"error": str(exc), "status": "internal_error" if exc.status >= 500 else "request_error"})
        except (TimeoutError, ConnectionError):
            self._json(408, {"error": "请求超时"})
        except Exception:
            LOGGER.exception("http.unhandled_error")
            self._json(500, {"error": "本地服务暂时无法处理请求", "status": "internal_error"})

    def do_OPTIONS(self):
        self._json(405, {"error": "仅支持同源 GET 和 POST 请求"})


def make_server(port=8765, provider="mock", config=None, service=None, static_root=None,
                host="127.0.0.1", allowed_hosts=None, max_model_calls=100, max_concurrency=2,
                collector=None, deck=None):
    """Public binding requires an explicit Host allowlist; no wildcard origins."""
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535")
    if host not in ("127.0.0.1", "localhost", "0.0.0.0"):
        raise ValueError("host must be 127.0.0.1, localhost, or explicitly 0.0.0.0")
    if host == "0.0.0.0" and not allowed_hosts:
        raise ValueError("Public binding requires at least one explicit --allowed-host")
    allowed = set()
    for value in allowed_hosts or []:
        if type(value) is not str or not value.isascii():
            raise ValueError("allowed-host must be an ASCII hostname, optionally with a port")
        parsed = urlsplit("http://" + value)
        try:
            valid_port = parsed.port is None or 1 <= parsed.port <= 65535
        except ValueError:
            valid_port = False
        if (not parsed.hostname or parsed.netloc != value or not valid_port
                or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
                or any(char.isspace() for char in value) or "*" in value):
            raise ValueError("allowed-host must be an exact hostname, optionally with a valid port")
        allowed.add(value.lower())
    # Resolve configuration and local files before opening even a local socket.
    root = Path(static_root).resolve() if static_root is not None else web_root()
    active_service = service if service is not None else NornService(
        provider=provider, config=config, max_model_calls=max_model_calls, max_concurrency=max_concurrency,
        collector=collector, deck=deck)
    server = NornHTTPServer((host, port), NornHandler)
    allowed.update({f"127.0.0.1:{server.server_port}", f"localhost:{server.server_port}"})
    if server.server_port == 80:
        allowed.update({"127.0.0.1", "localhost"})
    server.allowed_hosts = allowed
    server.static_root = root
    server.service = active_service
    import os
    from .jev_live import LocalJev
    server.jev_live = LocalJev(active_service.deck,
        os.environ.get("NORN_JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY") or "")
    return server

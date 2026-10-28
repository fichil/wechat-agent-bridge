"""Loopback-only authenticated control plane. No request/body logging."""

from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
from pathlib import Path
import secrets
import socket
from urllib.parse import parse_qs, urlsplit

from .console_models import ConsoleError


class ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = True
    # Windows SO_REUSEADDR permits a second listener to share this port. A
    # different local preview server must never receive this console's traffic.
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, service, port=0):
        self.service = service
        self.bootstrap = secrets.token_urlsafe(32)
        self.session = secrets.token_urlsafe(32)
        self.csrf = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), Handler)
        self.origin = f"http://127.0.0.1:{self.server_address[1]}"
        # Cookies have no port scope. Give each console its own cookie name so
        # development does not overwrite the old stable console's session.
        self.cookie_name = f'bridge_session_{self.server_address[1]}'

    @property
    def launch_url(self):
        return self.origin+"/#"+self.bootstrap


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalBridge"

    def log_message(self, *args):
        pass

    def _send(self, code, data, mime="application/json; charset=utf-8", cookie=None, download=False):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        if download:
            self.send_header("Content-Disposition", 'attachment; filename="attachment.bin"')
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _host(self):
        if self.headers.get("Host") != self.server.origin.removeprefix("http://"):
            raise ConsoleError("invalid_host")

    def _authenticated(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            token = cookie[self.server.cookie_name].value
        except (KeyError, ValueError):
            raise ConsoleError("authentication_required") from None
        if not hmac.compare_digest(token, self.server.session):
            raise ConsoleError("authentication_required")

    def _origin(self):
        if self.headers.get("Origin") != self.server.origin:
            raise ConsoleError("invalid_origin")

    def _body(self):
        size = int(self.headers.get("Content-Length", "0"))
        if size < 0 or size > 1024*1024:
            raise ConsoleError("request_too_large")
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            raise ConsoleError("json_required")
        data = json.loads(self.rfile.read(size))
        if not isinstance(data, dict):
            raise ConsoleError("invalid_request")
        return data

    def do_GET(self):
        try:
            self._host()
            path = urlsplit(self.path)
            static = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                      "/style.css": ("style.css", "text/css; charset=utf-8")}
            if path.path in static:
                filename, mime = static[path.path]
                return self._send(200, (Path(__file__).parent/"web"/filename).read_bytes(), mime)
            self._authenticated()
            service = self.server.service
            params = {k: values[0] for k, values in parse_qs(path.query).items()}
            if path.path == "/api/session":
                return self._send(200, {"csrf": self.server.csrf})
            if path.path == "/api/status":
                return self._send(200, service.status())
            if path.path == "/api/config":
                return self._send(200, service.configuration())
            if path.path == "/api/messages":
                return self._send(200, service.read_messages(params["account"], params["conversation"], int(params["before"]) if params.get("before") else None))
            if path.path == "/api/memory":
                return self._send(200, service.memory(params["account"], params["conversation"]))
            if path.path == "/api/attachment":
                data, mime = service.get_attachment(params["account"], params["conversation"], params["id"])
                inline = mime in ("image/png", "image/jpeg", "image/webp", "audio/wav", "audio/mpeg", "audio/mp4")
                return self._send(200, data, mime if inline else "application/octet-stream", download=not inline)
            self._send(404, {"error": "not_found"})
        except ConsoleError as exc:
            self._send(403 if str(exc) in ("authentication_required", "invalid_host", "invalid_origin") else 409, {"error": str(exc)})
        except (ValueError, KeyError, TypeError):
            self._send(400, {"error": "invalid_request"})
        except Exception:
            self._send(500, {"error": "operation_failed"})

    def do_POST(self):
        try:
            self._host()
            self._origin()
            path = urlsplit(self.path).path
            body = self._body()
            if path == "/api/session":
                token = body.get("token", "")
                if not isinstance(token, str) or not hmac.compare_digest(token, self.server.bootstrap):
                    raise ConsoleError("authentication_required")
                return self._send(200, {"csrf": self.server.csrf}, cookie=f"{self.server.cookie_name}={self.server.session}; HttpOnly; SameSite=Strict; Path=/")
            self._authenticated()
            if not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), self.server.csrf):
                raise ConsoleError("invalid_origin")
            service = self.server.service
            account, conversation = body.get("account", ""), body.get("conversation", "")
            if path == "/api/discover":
                result = service.discover()
            elif path == "/api/accessibility":
                result = service.accessibility(body.get("action"), body)
            elif path == '/api/identity-probe':
                result = service.identity_probe(body.get('action'),body)
            elif path == '/api/bind-current-group':
                result = service.bind_current_group(account, body.get('group_name'))
            elif path == '/api/read-channel':
                result = service.bind_read_channel(account, conversation, body.get('window'), body.get('automation_id'))
            elif path == '/api/reply-scope':
                result = service.configure_reply_scope(account, body.get('all_private'), body.get('history', 'new_only'))
            elif path == '/api/prefix-trial':
                result = service.enable_prefix_trial(account, conversation, body.get('trigger_mode'))
            elif path == '/api/send-preflight':
                result = service.send_preflight(account, conversation)
            elif path == '/api/reply-recovery':
                result = service.recover_reply(account, conversation, body.get('message_ids'), body.get('recovery_id'))
            elif path == "/api/probe-account":
                if not hasattr(service.adapter, "probe_account"):
                    raise ConsoleError("not_available_in_mock")
                with service.desktop_lock:
                    service.adapter.probe_account(body["instance_id"])
                result = service.discover()
            elif path == "/api/config":
                result = service.save_configuration(body)
            elif path == '/api/provider-preset':
                result = service.select_provider_preset(body.get('name'))
            elif path == "/api/provider-test":
                result = service.test_provider(body.get("capability", "text"))
            elif path == "/api/grant":
                result = service.authorize(account, body)
            elif path == "/api/conversations":
                result = service.conversations(account)
            elif path == "/api/sync":
                result = service.sync(account, conversation, older=body.get("older") is True)
            elif path == "/api/pause":
                result = service.pause()
            elif path == "/api/resume":
                result = service.resume()
            elif path == "/api/clear-memory":
                result = service.clear_memory(account, conversation or None)
            elif path == "/api/acknowledge-unknown":
                if body.get("reviewed") is not True:
                    raise ConsoleError("manual_review_required")
                result = service.acknowledge_unknown(account, conversation)
            elif path == "/api/mock-inject":
                if service.adapter.simulation_only is not True:
                    raise ConsoleError("mock_only")
                service._require(account, conversation)
                text = body.get("text", "")
                if not isinstance(text, str) or not text.strip() or len(text) > 5000:
                    raise ConsoleError("invalid_message")
                service.adapter.inject(account, conversation, text, mentions=body.get("mentions") is True)
                result = {"injected": True}
            else:
                return self._send(404, {"error": "not_found"})
            self._send(200, result)
        except ConsoleError as exc:
            self._send(403 if str(exc) in ("authentication_required", "invalid_host", "invalid_origin") else 409, {"error": str(exc)})
        except (ValueError, KeyError, TypeError):
            self._send(400, {"error": "invalid_request"})
        except Exception:
            self._send(500, {"error": "operation_failed"})

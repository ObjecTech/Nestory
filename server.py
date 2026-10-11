#!/usr/bin/env python3
from __future__ import annotations

import json
import ipaddress
from dataclasses import replace
import io
import re
import socket
import secrets
from http.cookies import SimpleCookie
from PIL import Image
from security import AuthConfig, Sessions, public_payload, preview_lan_allows
import mimetypes
import os
import queue
import signal
import sys
import threading
import time
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from access_control import AccessStateStore
from baby_monitor.app import BabyMonitorApp
from baby_monitor.config import Config


class MonitorServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address,
        handler,
        app: BabyMonitorApp,
        access_store: AccessStateStore,
        auth_config: AuthConfig | None = None,
        *, lan_subnet=None, auto_login=None, tailscale_user="",
    ):
        super().__init__(address, handler)
        self.lan_subnet = lan_subnet
        self.auto_login = auto_login
        self.tailscale_user = tailscale_user
        self.app = app
        self.access_store = access_store
        self.auth = Sessions(auth_config or AuthConfig.from_env())
        self.stream_lock = threading.RLock()
        self.active_streams = {}

    def handle_error(self, request, client_address):
        # Suppress default tracebacks with paths and client addresses.
        print("HTTP request failed", file=sys.stderr)

    def close_streams(self, token=None):
        with self.stream_lock:
            for connection, owner in list(self.active_streams.items()):
                if token is None or owner == token:
                    try:
                        connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    self.active_streams.pop(connection, None)


class Handler(BaseHTTPRequestHandler):
    server_version = "BabyMonitor/0.1"

    @property
    def app(self) -> BabyMonitorApp:
        return self.server.app  # type: ignore[attr-defined]

    @property
    def access_store(self) -> AccessStateStore:
        return self.server.access_store  # type: ignore[attr-defined]

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def _lan_allows(self, address):
        if self.server.lan_subnet is None:
            return preview_lan_allows(address)
        if not self.server.lan_subnet:
            return False
        try:
            network = ipaddress.ip_network(self.server.lan_subnet, strict=True)
            return (network.version == 4 and network.is_private
                    and network.prefixlen >= 24 and ipaddress.ip_address(address) in network)
        except ValueError:
            return False

    def _tailscale_owner(self):
        return (bool(self.server.tailscale_user)
                and self.server.server_address[0] in {"127.0.0.1", "::1"}
                and self.client_address[0] in {"127.0.0.1", "::1"}
                and self.server.auth.config.secure
                and self.headers.get("Tailscale-User-Login") == self.server.tailscale_user)

    def _valid_host(self):
        if self.server.tailscale_user and not self._tailscale_owner():
            self.send_error(403)
            return False
        if (self.server.lan_subnet if self.server.lan_subnet is not None else os.getenv("BMON_PREVIEW_LAN_SUBNET")) and not self._lan_allows(self.client_address[0]):
            self.send_error(403)
            return False
        if self.headers.get("Host") != urlparse(self.server.auth.config.origin).netloc:
            self.send_error(421)
            return False
        return True

    def _session_token(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            return cookie["bmon_session"].value if "bmon_session" in cookie else ""
        except Exception:
            return ""

    def _allow_request(self, path: str) -> bool:
        if not self.server.auth.valid(self._session_token()):
            if not path.startswith(("/api/", "/snapshot", "/stream", "/media/")):
                self.send_response(303)
                self.send_header("Location", "/login")
                self.end_headers()
            else:
                self._json({"error": "Authentication required"}, 401)
            return False
        if self.access_store.read()["lan_enabled"]:
            return True
        if path in {"/api/access", "/api/health", "/logout", "/", "/app.js", "/app.css", "/manifest.webmanifest", "/service-worker.js", "/apple-touch-icon.png"} or path.startswith("/assets/"):
            return True
        self._json({"error": "Monitoring access disabled"}, 403)
        return False

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if self.server.auth.config.secure:
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        super().end_headers()

    def log_message(self, format, *args):
        # Do not log client addresses, request URLs, credentials or query parameters.
        pass

    def _login_page(self):
        body = b"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Edge AI Monitor - Sign in</title>
<style>
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;background:#f0f5f1;color:#173c35;font:16px system-ui,sans-serif}main{width:100%;max-width:420px;padding:40px;background:#fff;border:1px solid #d3e1d9;border-radius:24px;box-shadow:0 14px 50px #173c3510}h1{font-size:28px;margin:12px 0}p{color:#60746c;line-height:1.5}label{display:block;margin:18px 0 8px;font-size:14px}input{display:block;width:100%;padding:12px;margin-top:7px;border:1px solid #b9cec2;border-radius:8px;font:inherit}button{width:100%;padding:13px;margin-top:24px;border:0;border-radius:9px;background:#28675e;color:white;font:inherit;cursor:pointer}.brand{letter-spacing:.12em;font-size:11px;font-weight:700;color:#28675e}small{display:block;margin-top:22px;color:#60746c;line-height:1.5}
</style></head><body><main><span class="brand">EDGE AI RESEARCH PLATFORM</span>
<h1>Edge AI Monitor</h1><p>Sign in to your authorized research workspace.</p>
<form method="post" action="/login"><label>Username<input name="username" autocomplete="username" required></label><label>Password<input name="password" type="password" autocomplete="current-password" required></label><button>Sign in</button></form>
<small>Research software. Not a medical device.<br>Network access requires HTTPS.</small>
</main></body></html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _cookie(self, name, value, age, http_only=False):
        text = f"{name}={value}; Path=/; Max-Age={age}; SameSite=Strict"
        if http_only:
            text += "; HttpOnly"
        if self.server.auth.config.secure:
            text += "; Secure"
        return text

    def _login(self):
        if self.headers.get("Origin") != self.server.auth.config.origin or self.headers.get_content_type() != "application/x-www-form-urlencoded":
            self._json({"error": "Invalid login origin"}, 403)
            return
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size <= 16384:
            self._json({"error": "Invalid request"}, 400)
            return
        fields = parse_qs(self.rfile.read(size).decode("utf-8"))
        result = self.server.auth.login(fields.get("username", [""])[0], fields.get("password", [""])[0])
        if result is None:
            self._json({"error": "Login failed or temporarily rate limited"}, 401)
            return
        token, csrf = result
        self.send_response(303)
        self.send_header("Location", "/")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Set-Cookie", self._cookie("bmon_session", token, self.server.auth.config.session_seconds, True))
        self.send_header("Set-Cookie", self._cookie("bmon_csrf", csrf, self.server.auth.config.session_seconds))
        self.end_headers()

    def _stream_allowed(self):
        return (self.connection in self.server.active_streams
                and self.server.auth.valid(self._session_token())
                and self.access_store.read()["lan_enabled"])

    def _photo(self, event_id, variant):
        from baby_monitor.app import PHOTO_EVENT_TYPES
        item = self.app.database.event(event_id)
        if not item or item.get("event_type") not in PHOTO_EVENT_TYPES:
            self.send_error(404)
            return
        relative = item.get("thumbnail_path") if variant == "thumbnail" else item.get("original_path")
        relative = relative or item.get("original_path")
        if not relative:
            self.send_error(404)
            return
        root = self.app.config.media_dir.resolve()
        path = (root / relative).resolve()
        try:
            parts = path.relative_to(root).parts
            if not parts or parts[0] not in {"classified", "smiles"} or path.suffix.lower() not in {".jpg", ".jpeg", ".png"} or not path.is_file() or path.stat().st_size > 20_000_000:
                raise ValueError()
            with Image.open(path) as picture:
                if picture.format not in {"JPEG", "PNG"}:
                    raise ValueError()
                output = io.BytesIO()
                picture.convert("RGB").save(output, "JPEG", quality=90)
            body = output.getvalue()
        except (ValueError, OSError, Image.DecompressionBombError):
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, status=HTTPStatus.OK) -> None:
        body = json.dumps(public_payload(payload), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        size = int(self.headers.get("Content-Length", "0"))
        if size <= 0 or size > 1_000_000:
            raise ValueError("invalid request body")
        return json.loads(self.rfile.read(size).decode("utf-8"))

    def _file(self, path: Path, cache: bool = False) -> None:
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type, _ = mimetypes.guess_type(path.name)
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=86400" if cache else "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        try:
            self._get()
        except (ValueError, KeyError, UnicodeError):
            self._json({"error": "Invalid request"}, 400)
        except OSError:
            return

    def _get(self) -> None:
        if not self._valid_host():
            return
        parsed = urlparse(self.path)
        path = parsed.path
        auto_login = self.server.auto_login if self.server.auto_login is not None else os.getenv("BMON_LOCAL_PREVIEW_NO_LOGIN", "false").lower() == "true"
        if (auto_login or self._tailscale_owner()) and path in {"/", "/login"}:
            loopback = {"127.0.0.1", "::1"}
            local_preview = (self.server.server_address[0] in loopback
                             and self.client_address[0] in loopback
                             and urlparse(self.server.auth.config.origin).hostname in loopback | {"localhost"})
            lan_preview = all(self._lan_allows(address) for address in (
                self.server.server_address[0], self.client_address[0],
                urlparse(self.server.auth.config.origin).hostname))
            if not (local_preview or lan_preview or self._tailscale_owner()):
                self.send_error(403)
                return
            if not self.server.auth.valid(self._session_token()):
                token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                with self.server.auth.lock:
                    if len(self.server.auth.sessions) >= 32:
                        self.server.auth.sessions.pop(next(iter(self.server.auth.sessions)))
                    self.server.auth.sessions[token] = (csrf, time.monotonic() + self.server.auth.config.session_seconds)
                self.send_response(303)
                self.send_header("Location", "/")
                self.send_header("Set-Cookie", self._cookie("bmon_session", token, self.server.auth.config.session_seconds, True))
                self.send_header("Set-Cookie", self._cookie("bmon_csrf", csrf, self.server.auth.config.session_seconds))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            if path == "/login":
                self.send_response(303)
                self.send_header("Location", "/")
                self.end_headers()
                return
        if path == "/login":
            self._login_page()
            return
        if not self._allow_request(path):
            return
        photo_match = re.fullmatch(r"/api/photos/([1-9][0-9]*)/(original|thumbnail)", path)
        if photo_match:
            self._photo(int(photo_match[1]), photo_match[2])
        elif path == "/api/access":
            self._json(self.access_store.read())
        elif path == "/api/health":
            self._json({"ok": True, "ts": time.time()})
        elif path == "/api/status":
            self._json(self.app.status())
        elif path == "/api/player":
            self._json(self.app.player.status())
        elif path == "/api/voice":
            self._json(self.app.voice.status())
        elif path == "/api/timeline":
            query = parse_qs(parsed.query)
            date_text = query.get("date", [datetime.now().strftime("%Y-%m-%d")])[0]
            try:
                day = datetime.strptime(date_text, "%Y-%m-%d")
            except ValueError:
                self._json({"error": "日期格式应为 YYYY-MM-DD"}, HTTPStatus.BAD_REQUEST)
                return
            start_ts = day.timestamp()
            end_ts = (day + timedelta(days=1)).timestamp()
            self._json({"date": date_text, **self.app.timeline(start_ts, end_ts)})
        elif path == "/api/smiles":
            query = parse_qs(parsed.query)
            self._json({"items": self.app.smile_events(int(query.get("limit", ["100"])[0]))})
        elif path == "/api/photos":
            query = parse_qs(parsed.query)
            self._json({"items": self.app.photo_events(int(query.get("limit", ["300"])[0]))})
        elif path == "/api/observations":
            query = parse_qs(parsed.query)
            date_text = query.get("date", [None])[0]
            start_ts = None
            end_ts = None
            if date_text:
                try:
                    day = datetime.strptime(date_text, "%Y-%m-%d")
                except ValueError:
                    self._json({"error": "日期格式应为 YYYY-MM-DD"}, HTTPStatus.BAD_REQUEST)
                    return
                start_ts = day.timestamp()
                end_ts = (day + timedelta(days=1)).timestamp()
            self._json(
                {
                    "items": self.app.observations(
                        int(query.get("limit", ["100"])[0]),
                        start_ts,
                        end_ts,
                    )
                }
            )
        elif path == "/api/events":
            self._events()
        elif path == "/stream.mjpg":
            self._stream()
        elif path == "/snapshot.jpg":
            jpeg = self.app.camera.compatible_snapshot()
            if jpeg is None:
                self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "camera not ready")
            else:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(jpeg)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(jpeg)
        elif path.startswith("/media/"):
            self.send_error(404)
        else:
            relative = "index.html" if path == "/" else unquote(path.lstrip("/"))
            static_root = self.app.config.static_dir.resolve()
            candidate = (static_root / relative).resolve()
            if candidate != static_root and static_root not in candidate.parents:
                self.send_error(HTTPStatus.NOT_FOUND)
            else:
                self._file(candidate, cache=relative != "index.html")

    def do_POST(self) -> None:
        if not self._valid_host():
            return
        try:
            path = urlparse(self.path).path
            if path == "/login":
                self._login()
                return
            if not self._allow_request(path):
                return
            if (self.headers.get("Origin") != self.server.auth.config.origin
                    or not self.server.auth.csrf_valid(self._session_token(), self.headers.get("X-CSRF-Token", ""))):
                self._json({"error": "Invalid request authorization"}, 403)
                return
            if path == "/logout":
                token = self._session_token()
                self.server.auth.revoke(token)
                self.server.close_streams(token)
                self.send_response(204)
                self.send_header("Set-Cookie", self._cookie("bmon_session", "", 0, True))
                self.send_header("Set-Cookie", self._cookie("bmon_csrf", "", 0))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            if path == "/api/access":
                if (
                    self.headers.get_content_type() != "application/json"
                ):
                    self._json({"error": "只允许从授权网络修改访问开关"}, HTTPStatus.FORBIDDEN)
                    return
                payload = self._read_json()
                if payload.get("key") != "lan_enabled":
                    raise ValueError("Unsupported access setting")
                with self.server.stream_lock:
                    state = self.access_store.update("lan_enabled", payload.get("enabled"))
                    if not state["lan_enabled"]:
                        self.server.close_streams()
                self._json(state)
            elif path == "/api/observation-subject":
                payload = self._read_json()
                self._json(self.app.set_observation_subject(payload.get("person_test_mode")))
            elif path == "/api/manual-state":
                payload = self._read_json()
                result = self.app.set_manual_state(
                    str(payload.get("category", "")), str(payload.get("value", ""))
                )
                self._json(result)
            elif path == "/api/player/category":
                payload = self._read_json()
                self._json(self.app.player.play_category(str(payload.get("category", ""))))
            elif path == "/api/player/control":
                payload = self._read_json()
                self._json(self.app.player.control(str(payload.get("action", ""))))
            elif path == "/api/player/volume":
                payload = self._read_json()
                self._json(self.app.player.set_volume(int(payload.get("volume", -1))))
            elif path == "/api/player/seek":
                payload = self._read_json()
                self._json(self.app.player.seek(payload.get("position_seconds", -1)))
            elif path == "/api/voice":
                if (
                    self.headers.get_content_type() != "application/json"
                ):
                    self._json({"error": "只允许从授权网络修改语音开关"}, HTTPStatus.FORBIDDEN)
                    return
                payload = self._read_json()
                self._json(self.app.voice.set_enabled(payload.get("enabled")))
            elif self.path == "/api/save-smile":
                self._json(self.app.save_smile(1.0, "手机手动保存"), HTTPStatus.CREATED)
            elif self.path == "/api/save-photo":
                self._json(self.app.save_current_classified_photo(), HTTPStatus.CREATED)
            elif self.path == "/api/photos/trash":
                payload = self._read_json()
                self._json(
                    self.app.trash_photo(
                        int(payload.get("id", 0)), str(payload.get("event_type", ""))
                    )
                )
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self._json({"error": "Invalid request"}, HTTPStatus.BAD_REQUEST)
        except RuntimeError as exc:
            self._json({"error": "Operation unavailable"}, HTTPStatus.SERVICE_UNAVAILABLE)

    def _events(self) -> None:
        with self.server.stream_lock:
            if not self.access_store.read()["lan_enabled"] or not self.server.auth.valid(self._session_token()):
                self._json({"error": "Access disabled"}, 403)
                return
            self.server.active_streams[self.connection] = self._session_token()
        channel = self.app.events.subscribe()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            initial = json.dumps(public_payload({"type": "status", **self.app.status()}), ensure_ascii=False)
            self.wfile.write(f"data: {initial}\n\n".encode("utf-8"))
            self.wfile.flush()
            while self._stream_allowed():
                try:
                    message = channel.get(timeout=0.25)
                    data = json.dumps(public_payload(message), ensure_ascii=False)
                    self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
        except OSError:
            pass
        finally:
            self.app.events.unsubscribe(channel)
            with self.server.stream_lock:
                self.server.active_streams.pop(self.connection, None)

    def _stream(self) -> None:
        with self.server.stream_lock:
            if not self.access_store.read()["lan_enabled"] or not self.server.auth.valid(self._session_token()):
                self._json({"error": "Access disabled"}, 403)
                return
            self.server.active_streams[self.connection] = self._session_token()
        boundary = "frame"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={boundary}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        last_ts = 0.0
        try:
            while self._stream_allowed():
                frame = self.app.camera.wait_for_frame(last_ts, 0.25)
                if not self._stream_allowed():
                    break
                if frame is None:
                    continue
                last_ts = frame.ts
                self.wfile.write(f"--{boundary}\r\n".encode("ascii"))
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(frame.jpeg)}\r\n\r\n".encode("ascii"))
                self.wfile.write(frame.jpeg)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
        except OSError:
            pass
        finally:
            with self.server.stream_lock:
                self.server.active_streams.pop(self.connection, None)


def main() -> int:
    os.umask(0o077)
    auth_config = AuthConfig.from_env()
    config = Config.from_env()
    if config.host not in {"127.0.0.1", "localhost", "::1"} and not auth_config.secure and not preview_lan_allows(config.host):
        raise ValueError("Non-loopback binding requires HTTPS configuration")
    app = BabyMonitorApp(config)
    access_store = AccessStateStore(config.data_dir / "access.json")
    ts_user = os.getenv("BMON_TAILSCALE_USER", "")
    if ts_user and (config.host not in {"127.0.0.1", "::1"} or not auth_config.secure):
        raise ValueError("Tailscale identity requires a loopback HTTPS upstream")
    server = MonitorServer((config.host, config.port), Handler, app, access_store,
                           auth_config, lan_subnet="" if ts_user else None,
                           tailscale_user=ts_user)
    lan_server = None
    lan_host = os.getenv("BMON_LAN_HOST", "")
    if lan_host:
        subnet = os.getenv("BMON_LAN_SUBNET", "")
        network = ipaddress.ip_network(subnet, strict=True)
        if not (network.version == 4 and network.is_private and network.prefixlen >= 24
                and ipaddress.ip_address(lan_host) in network):
            raise ValueError("LAN listener requires an explicit trusted private subnet /24 or narrower")
        lan_port = int(os.getenv("BMON_LAN_PORT", "8080"))
        lan_server = MonitorServer((lan_host, lan_port), Handler, app, access_store,
                                   replace(auth_config, origin=f"http://{lan_host}:{lan_port}"),
                                   lan_subnet=subnet, auto_login=True)
        # Access-off must terminate streams on both listeners, while sessions remain separate.
        lan_server.stream_lock = server.stream_lock
        lan_server.active_streams = server.active_streams
    stopping = False

    def stop_server(*_args):
        nonlocal stopping
        if not stopping:
            stopping = True
            threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop_server)
    signal.signal(signal.SIGINT, stop_server)
    app.start()
    if lan_server:
        threading.Thread(target=lan_server.serve_forever, daemon=True).start()
    print("Edge AI Monitor started. Open the configured public origin.", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.close_streams()
        if lan_server:
            lan_server.shutdown()
            lan_server.server_close()
        app.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

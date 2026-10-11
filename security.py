"""Single-owner authentication for the research platform; no network-origin bypass."""
from __future__ import annotations
import base64
import ipaddress
import hashlib
import hmac
import os
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from urllib.parse import urlsplit


def preview_lan_allows(address: str) -> bool:
    """Explicit temporary LAN preview; never accepts public or broad networks."""
    value = os.getenv('BMON_PREVIEW_LAN_SUBNET', '')
    if not value:
        return False
    try:
        network = ipaddress.ip_network(value, strict=True)
        ip = ipaddress.ip_address(address)
        return (network.version == 4 and network.is_private
                and network.prefixlen >= 24 and ip in network)
    except ValueError:
        return False


def make_password_hash(password: str) -> str:
    salt = secrets.token_bytes(18)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 600_000)
    return 'pbkdf2_sha256$600000$' + base64.b64encode(salt).decode() + '$' + base64.b64encode(digest).decode()


def verify_password_hash(password: str, encoded: str) -> bool:
    try:
        scheme, count, salt, digest = encoded.split('$')
        iterations = int(count)
        if scheme != 'pbkdf2_sha256' or not 100_000 <= iterations <= 2_000_000:
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), base64.b64decode(salt, validate=True), iterations)
        return hmac.compare_digest(actual, base64.b64decode(digest, validate=True))
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class AuthConfig:
    username: str
    password_hash: str
    origin: str
    session_seconds: int = 3600

    @property
    def secure(self) -> bool:
        return self.origin.startswith('https://')

    @classmethod
    def from_env(cls):
        username = os.environ.get('BMON_USERNAME', '')
        password_hash = os.environ.get('BMON_PASSWORD_HASH', '')
        origin = os.environ.get('BMON_PUBLIC_ORIGIN', 'http://localhost:8080').rstrip('/')
        parsed = urlsplit(origin)
        if not username or not password_hash:
            raise ValueError('Configure BMON_USERNAME and BMON_PASSWORD_HASH before starting.')
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError('BMON_PUBLIC_ORIGIN must be a single HTTP(S) origin.')
        if parsed.scheme == 'http' and parsed.hostname not in {'localhost', '127.0.0.1', '::1'} and not preview_lan_allows(parsed.hostname):
            raise ValueError('Non-loopback access requires an HTTPS origin.')
        # Reject invalid hashes at startup without printing their value.
        try:
            scheme, count, salt, digest = password_hash.split('$')
            assert scheme == 'pbkdf2_sha256' and 100_000 <= int(count) <= 2_000_000
            assert len(base64.b64decode(salt, validate=True)) >= 16
            assert len(base64.b64decode(digest, validate=True)) == 32
        except (ValueError, AssertionError):
            raise ValueError('Invalid password hash configuration.') from None
        return cls(username, password_hash, origin)


class Sessions:
    """Opaque, random, server-side sessions. Restart and logout revoke access."""
    def __init__(self, config: AuthConfig):
        self.config = config
        self.lock = threading.RLock()
        self.sessions = {}
        self.attempts = deque()

    def login(self, username: str, password: str):
        # Global bounded limiter protects the single-owner account behind proxies.
        with self.lock:
            now = time.monotonic()
            while self.attempts and self.attempts[0] < now - 900:
                self.attempts.popleft()
            if len(self.attempts) >= 5:
                return None
            self.attempts.append(now)
            valid = verify_password_hash(password, self.config.password_hash)
            valid = hmac.compare_digest(username.encode(), self.config.username.encode()) and valid
            if not valid:
                return None
            self.attempts.clear()
            self.sessions = {k: v for k, v in self.sessions.items() if v[1] > now}
            if len(self.sessions) >= 32:
                self.sessions.pop(next(iter(self.sessions)))
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            self.sessions[token] = (csrf, now + self.config.session_seconds)
            return token, csrf

    def valid(self, token):
        with self.lock:
            entry = self.sessions.get(token)
            if entry and entry[1] > time.monotonic():
                return True
            self.sessions.pop(token, None)
            return False

    def csrf_valid(self, token, supplied):
        with self.lock:
            return self.valid(token) and hmac.compare_digest(self.sessions[token][0], supplied)

    def revoke(self, token):
        with self.lock:
            self.sessions.pop(token, None)


SENSITIVE_FIELDS = {'original_path', 'thumbnail_path', 'metadata_json', 'mount', 'path',
                    'root', 'address', 'device', 'audio_device', 'components', 'last_command',
                    'last_result', 'last_feedback', 'last_heard_at', 'command_examples', 'speaker', 'total_gb', 'free_gb'}


def public_payload(value):
    """Remove implementation details at the HTTP boundary, including SSE payloads."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in SENSITIVE_FIELDS or 'path' in key.lower():
                continue
            if 'error' in key.lower():
                result[key] = 'Operation unavailable' if item else None
            elif key.endswith('_url') and isinstance(item, str) and item.startswith('/media/'):
                if value.get('id'):
                    result[key] = f"/api/photos/{value['id']}/{'thumbnail' if key == 'thumbnail_url' else 'original'}"
            else:
                result[key] = public_payload(item)
        return result
    if isinstance(value, list):
        return [public_payload(item) for item in value]
    return value

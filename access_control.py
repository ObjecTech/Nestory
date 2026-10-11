from __future__ import annotations

import json
import os
import threading
from pathlib import Path


ACCESS_KEYS = ("lan_enabled",)
DEFAULT_ACCESS_STATE = {
    "lan_enabled": True,
}


class AccessStateStore:
    """Small atomic store shared by the local and remote HTTP processes."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def read(self) -> dict[str, bool]:
        state = dict(DEFAULT_ACCESS_STATE)
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return state
        if not isinstance(payload, dict):
            return state
        for key in ACCESS_KEYS:
            if isinstance(payload.get(key), bool):
                state[key] = payload[key]
        return state

    def update(self, key: str, enabled: bool) -> dict[str, bool]:
        if key not in ACCESS_KEYS:
            raise ValueError("未知的访问开关")
        if not isinstance(enabled, bool):
            raise ValueError("enabled 必须为布尔值")
        with self._lock:
            state = self.read()
            state[key] = enabled
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600,
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(state, stream, ensure_ascii=False, separators=(",", ":"))
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
            return state

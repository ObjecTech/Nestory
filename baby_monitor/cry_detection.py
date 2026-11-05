"""Turn per-second cry scores from the audio tagger into crying periods.

Only scores and period times are kept; no audio is stored anywhere.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

# AudioSet labels that count as a baby crying.
CRY_LABELS = ("Baby cry, infant cry", "Crying, sobbing")


class CryTracker:
    """Start a period after repeated confident scores; end it after a quiet gap.

    `on_start(start_ts, score)` and `on_end(end_ts, peak_score)` are called at
    most once per period, in that order.
    """

    def __init__(
        self,
        on_start: Callable[[float, float], None],
        on_end: Callable[[float, float], None],
        threshold: float = 0.5,
        confirm_hits: int = 2,
        confirm_window: float = 5.0,
        end_gap: float = 15.0,
        window_seconds: float = 1.0,
        clock: Callable[[], float] = time.time,
    ):
        self.on_start, self.on_end = on_start, on_end
        self.threshold = threshold
        self.confirm_hits = confirm_hits
        self.confirm_window = confirm_window
        self.end_gap = end_gap
        self.window_seconds = window_seconds
        self.clock = clock
        self._lock = threading.Lock()
        self._recent_hits: list[float] = []
        self._active_since: float | None = None
        self._last_hit: float | None = None
        self._peak = 0.0
        self.last_score = 0.0

    @property
    def active_since(self) -> float | None:
        return self._active_since

    def observe(self, score: float, ts: float | None = None) -> None:
        ts = self.clock() if ts is None else ts
        start = end = None
        with self._lock:
            self.last_score = score
            if self._active_since is not None and ts - self._last_hit > self.end_gap:
                end = self._close()
            if score >= self.threshold:
                self._last_hit = ts
                if self._active_since is not None:
                    self._peak = max(self._peak, score)
                else:
                    self._recent_hits = [t for t in self._recent_hits if ts - t <= self.confirm_window] + [ts]
                    if len(self._recent_hits) >= self.confirm_hits:
                        self._active_since = self._recent_hits[0]
                        self._peak = score
                        self._recent_hits = []
                        start = (self._active_since, score)
        if end:
            self.on_end(*end)
        if start:
            self.on_start(*start)

    def flush(self) -> None:
        """Close an open period, e.g. when the microphone stops."""
        with self._lock:
            end = self._close() if self._active_since is not None else None
            self._recent_hits = []
        if end:
            self.on_end(*end)

    def _close(self) -> tuple[float, float]:
        end = (self._last_hit + self.window_seconds, self._peak)
        self._active_since = None
        self._peak = 0.0
        return end

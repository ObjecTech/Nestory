from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


ACTIVITY_VALUES = {
    "sleeping",
    "awake",
    "awake_happy",
    "awake_unhappy",
    "crying",
    "unknown",
}
POSTURE_VALUES = {
    "supine",
    "side",
    "prone",
    "left_side",
    "right_side",
    "left_prone",
    "right_prone",
    "unknown",
}
PRESENCE_VALUES = {"visible", "not_visible", "unknown"}


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        with self._lock, self._connection:
            self._connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=NORMAL;
                CREATE TABLE IF NOT EXISTS intervals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL,
                    value TEXT NOT NULL,
                    start_ts REAL NOT NULL,
                    end_ts REAL,
                    source TEXT NOT NULL,
                    confidence REAL,
                    note TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_intervals_category_time
                    ON intervals(category, start_ts, end_ts);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_one_open_interval
                    ON intervals(category) WHERE end_ts IS NULL;

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    ts REAL NOT NULL,
                    activity TEXT,
                    posture TEXT,
                    confidence REAL,
                    original_path TEXT,
                    thumbnail_path TEXT,
                    metadata_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_events_type_time
                    ON events(event_type, ts DESC);

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    source TEXT NOT NULL,
                    baby_visible INTEGER,
                    visibility TEXT NOT NULL,
                    sleep_state TEXT NOT NULL,
                    posture TEXT NOT NULL,
                    expression TEXT NOT NULL,
                    activity_level TEXT NOT NULL,
                    confidence REAL,
                    expression_confidence REAL,
                    posture_confidence REAL,
                    visibility_confidence REAL,
                    suggested_state TEXT,
                    description TEXT,
                    observed_actions_json TEXT,
                    metadata_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_observations_time
                    ON observations(ts DESC);
                """
            )
            observation_columns = {
                row["name"]
                for row in self._connection.execute(
                    "PRAGMA table_info(observations)"
                ).fetchall()
            }
            for name in (
                "expression_confidence",
                "posture_confidence",
                "visibility_confidence",
            ):
                if name not in observation_columns:
                    self._connection.execute(
                        f"ALTER TABLE observations ADD COLUMN {name} REAL"
                    )
        self.ensure_open_interval("activity", "unknown", "system")
        self.ensure_open_interval("posture", "unknown", "system")
        self.ensure_open_interval("presence", "unknown", "system")

    def ensure_open_interval(self, category: str, value: str, source: str) -> None:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT id FROM intervals WHERE category = ? AND end_ts IS NULL",
                (category,),
            ).fetchone()
            if row is None:
                self._connection.execute(
                    "INSERT INTO intervals(category, value, start_ts, source) VALUES(?, ?, ?, ?)",
                    (category, value, time.time(), source),
                )

    def current(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM intervals WHERE end_ts IS NULL ORDER BY category"
            ).fetchall()
        return {row["category"]: dict(row) for row in rows}

    def transition(
        self,
        category: str,
        value: str,
        source: str,
        confidence: float | None = None,
        note: str | None = None,
        now: float | None = None,
    ) -> tuple[bool, str]:
        if category not in {"activity", "posture", "presence"}:
            raise ValueError("unsupported category")
        allowed = (
            ACTIVITY_VALUES
            if category == "activity"
            else POSTURE_VALUES
            if category == "posture"
            else PRESENCE_VALUES
        )
        if value not in allowed:
            raise ValueError("unsupported value")
        now = now or time.time()
        with self._lock, self._connection:
            current = self._connection.execute(
                "SELECT * FROM intervals WHERE category = ? AND end_ts IS NULL",
                (category,),
            ).fetchone()
            previous = current["value"] if current else "unknown"
            if current and previous == value:
                self._connection.execute(
                    "UPDATE intervals SET confidence = COALESCE(?, confidence), note = COALESCE(?, note) WHERE id = ?",
                    (confidence, note, current["id"]),
                )
                return False, previous
            if current:
                self._connection.execute(
                    "UPDATE intervals SET end_ts = ? WHERE id = ?", (now, current["id"])
                )
            self._connection.execute(
                """
                INSERT INTO intervals(category, value, start_ts, source, confidence, note)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (category, value, now, source, confidence, note),
            )
        return True, previous

    def intervals(self, start_ts: float, end_ts: float) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM intervals
                WHERE start_ts < ? AND COALESCE(end_ts, ?) > ?
                ORDER BY start_ts ASC
                """,
                (end_ts, time.time(), start_ts),
            ).fetchall()
        result = []
        now = time.time()
        for row in rows:
            item = dict(row)
            clipped_start = max(item["start_ts"], start_ts)
            clipped_end = min(item["end_ts"] or now, end_ts)
            # Return the displayed daily segment, not the original multi-day bounds.
            item["start_ts"] = clipped_start
            item["ends_at_day_boundary"] = clipped_end == end_ts
            item["end_ts"] = (
                None if item["end_ts"] is None and now < end_ts else clipped_end
            )
            item["duration_seconds"] = max(0, clipped_end - clipped_start)
            result.append(item)
        return result

    def add_event(
        self,
        event_type: str,
        ts: float,
        activity: str | None,
        posture: str | None,
        confidence: float | None,
        original_path: str | None = None,
        thumbnail_path: str | None = None,
        metadata_json: str | None = None,
    ) -> int:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO events(
                    event_type, ts, activity, posture, confidence,
                    original_path, thumbnail_path, metadata_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_type,
                    ts,
                    activity,
                    posture,
                    confidence,
                    original_path,
                    thumbnail_path,
                    metadata_json,
                ),
            )
            return int(cursor.lastrowid)

    def add_observation(
        self,
        ts: float,
        source: str,
        observation: dict[str, Any],
        metadata_json: str | None = None,
    ) -> int:
        baby_visible = observation.get("baby_visible")
        visible_value = None if baby_visible is None else int(bool(baby_visible))
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO observations(
                    ts, source, baby_visible, visibility, sleep_state, posture,
                    expression, activity_level, confidence, expression_confidence,
                    posture_confidence, visibility_confidence, suggested_state,
                    description, observed_actions_json, metadata_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    source,
                    visible_value,
                    observation.get("visibility", "unknown"),
                    observation.get("sleep_state", "unknown"),
                    observation.get("posture", "unknown"),
                    observation.get("expression", "unknown"),
                    observation.get("activity_level", "unknown"),
                    observation.get("confidence"),
                    observation.get("expression_confidence"),
                    observation.get("posture_confidence"),
                    observation.get("visibility_confidence"),
                    observation.get("suggested_state"),
                    observation.get("description"),
                    json.dumps(observation.get("observed_actions", []), ensure_ascii=False),
                    metadata_json,
                ),
            )
            return int(cursor.lastrowid)

    def observations(
        self,
        limit: int = 100,
        start_ts: float | None = None,
        end_ts: float | None = None,
    ) -> list[dict[str, Any]]:
        clauses = []
        parameters: list[Any] = []
        if start_ts is not None:
            clauses.append("ts >= ?")
            parameters.append(start_ts)
        if end_ts is not None:
            clauses.append("ts < ?")
            parameters.append(end_ts)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(min(max(limit, 1), 500))
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM observations{where} ORDER BY ts DESC LIMIT ?",
                parameters,
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["baby_visible"] = (
                None if item["baby_visible"] is None else bool(item["baby_visible"])
            )
            try:
                item["observed_actions"] = json.loads(
                    item.pop("observed_actions_json") or "[]"
                )
            except json.JSONDecodeError:
                item["observed_actions"] = []
            result.append(item)
        return result

    def events(self, event_type: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM events WHERE event_type = ? ORDER BY ts DESC LIMIT ?",
                (event_type, min(max(limit, 1), 500)),
            ).fetchall()
        return [dict(row) for row in rows]

    def events_by_types(
        self, event_types: tuple[str, ...], limit: int = 300
    ) -> list[dict[str, Any]]:
        if not event_types:
            return []
        placeholders = ",".join("?" for _ in event_types)
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM events WHERE event_type IN ({placeholders}) "
                "ORDER BY ts DESC LIMIT ?",
                (*event_types, min(max(limit, 1), 500)),
            ).fetchall()
        return [dict(row) for row in rows]

    def event(self, event_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM events WHERE id = ?", (event_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def mark_event_trashed(
        self,
        event_id: int,
        expected_type: str,
        original_path: str | None,
        thumbnail_path: str | None,
        metadata_json: str,
    ) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE events
                SET event_type = 'photo_trashed',
                    original_path = ?, thumbnail_path = ?, metadata_json = ?
                WHERE id = ? AND event_type = ?
                """,
                (
                    original_path,
                    thumbnail_path,
                    metadata_json,
                    event_id,
                    expected_type,
                ),
            )
            return cursor.rowcount == 1

    def event_counts(
        self,
        event_type: str,
        start_ts: float,
        end_ts: float,
        group_by: str = "activity",
    ) -> dict[str, int]:
        if group_by not in {"activity", "posture"}:
            raise ValueError("unsupported event count group")
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT COALESCE({group_by}, '') AS category_value, COUNT(*) AS count
                FROM events
                WHERE event_type = ? AND ts >= ? AND ts < ?
                GROUP BY {group_by}
                """,
                (event_type, start_ts, end_ts),
            ).fetchall()
        return {str(row["category_value"]): int(row["count"]) for row in rows}

    def event_count(self, event_type: str, start_ts: float, end_ts: float) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM events "
                "WHERE event_type = ? AND ts >= ? AND ts < ?",
                (event_type, start_ts, end_ts),
            ).fetchone()
        return int(row["count"] if row is not None else 0)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

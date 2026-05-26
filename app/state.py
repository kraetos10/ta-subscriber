"""SQLite-backed persistence: settings, channels, seen videos, scan log."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS channels (
    channel_id TEXT PRIMARY KEY,
    name TEXT,
    handle TEXT,
    added_at INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS seen_videos (
    video_id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    title TEXT,
    first_seen INTEGER NOT NULL,
    queued_at INTEGER,
    skipped_reason TEXT,
    published_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_seen_channel ON seen_videos(channel_id);
CREATE INDEX IF NOT EXISTS idx_seen_queued ON seen_videos(queued_at DESC);

CREATE TABLE IF NOT EXISTS scan_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at INTEGER NOT NULL,
    finished_at INTEGER,
    channels INTEGER,
    candidates INTEGER,
    new_videos INTEGER,
    queued INTEGER,
    skipped INTEGER,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_log_started ON scan_log(started_at DESC);
"""


def _migrate(conn) -> None:
    cur = conn.execute("PRAGMA table_info(seen_videos)")
    cols = {row[1] for row in cur.fetchall()}
    if "skipped_reason" not in cols:
        conn.execute("ALTER TABLE seen_videos ADD COLUMN skipped_reason TEXT")
    if "published_at" not in cols:
        conn.execute("ALTER TABLE seen_videos ADD COLUMN published_at INTEGER")
    cur = conn.execute("PRAGMA table_info(scan_log)")
    cols = {row[1] for row in cur.fetchall()}
    if "skipped" not in cols:
        conn.execute("ALTER TABLE scan_log ADD COLUMN skipped INTEGER")


class State:
    def __init__(self, state_dir: str) -> None:
        Path(state_dir).mkdir(parents=True, exist_ok=True)
        self.path = os.path.join(state_dir, "subscriber.db")
        with self._conn() as c:
            c.executescript(SCHEMA)
            _migrate(c)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---- settings -------------------------------------------------------

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self._conn() as c:
            cur = c.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cur.fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, json.JSONDecodeError):
            return row["value"]

    def set_setting(self, key: str, value: Any) -> None:
        payload = json.dumps(value)
        with self._conn() as c:
            c.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, payload),
            )

    def all_settings(self) -> dict[str, Any]:
        with self._conn() as c:
            cur = c.execute("SELECT key, value FROM settings")
            rows = cur.fetchall()
        out: dict[str, Any] = {}
        for row in rows:
            try:
                out[row["key"]] = json.loads(row["value"])
            except (TypeError, json.JSONDecodeError):
                out[row["key"]] = row["value"]
        return out

    # ---- channels -------------------------------------------------------

    def list_channels(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT channel_id, name, handle, added_at, enabled FROM channels"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY LOWER(COALESCE(name, channel_id))"
        with self._conn() as c:
            cur = c.execute(sql)
            return [dict(row) for row in cur.fetchall()]

    def add_channel(
        self,
        channel_id: str,
        name: str | None,
        handle: str | None,
    ) -> bool:
        with self._conn() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO channels(channel_id, name, handle, added_at, enabled) "
                "VALUES (?, ?, ?, ?, 1)",
                (channel_id, name, handle, int(time.time())),
            )
            return cur.rowcount > 0

    def upsert_channel(
        self,
        channel_id: str,
        name: str | None,
        handle: str | None,
    ) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO channels(channel_id, name, handle, added_at, enabled) "
                "VALUES (?, ?, ?, ?, 1) "
                "ON CONFLICT(channel_id) DO UPDATE SET "
                "  name = COALESCE(excluded.name, channels.name), "
                "  handle = COALESCE(excluded.handle, channels.handle)",
                (channel_id, name, handle, int(time.time())),
            )

    def remove_channel(self, channel_id: str) -> bool:
        with self._conn() as c:
            cur = c.execute(
                "DELETE FROM channels WHERE channel_id = ?", (channel_id,)
            )
            return cur.rowcount > 0

    def set_channel_enabled(self, channel_id: str, enabled: bool) -> bool:
        with self._conn() as c:
            cur = c.execute(
                "UPDATE channels SET enabled = ? WHERE channel_id = ?",
                (1 if enabled else 0, channel_id),
            )
            return cur.rowcount > 0

    def count_channels(self, enabled_only: bool = False) -> int:
        sql = "SELECT COUNT(*) FROM channels"
        if enabled_only:
            sql += " WHERE enabled = 1"
        with self._conn() as c:
            cur = c.execute(sql)
            return int(cur.fetchone()[0])

    # ---- seen videos ----------------------------------------------------

    def filter_unseen(self, video_ids: list[str]) -> list[str]:
        if not video_ids:
            return []
        with self._conn() as c:
            placeholders = ",".join("?" * len(video_ids))
            cur = c.execute(
                f"SELECT video_id FROM seen_videos WHERE video_id IN ({placeholders})",
                video_ids,
            )
            seen = {row["video_id"] for row in cur.fetchall()}
        return [v for v in video_ids if v not in seen]

    def mark_seen(
        self,
        items: list[tuple[str, str, str, int | None]],
        queued: bool,
        now_ts: int,
        skipped_reason: str | None = None,
    ) -> None:
        """items: (video_id, channel_id, title, published_at_unix)."""
        if not items:
            return
        rows = [
            (
                vid,
                cid,
                title,
                now_ts,
                now_ts if queued else None,
                skipped_reason,
                published,
            )
            for vid, cid, title, published in items
        ]
        with self._conn() as c:
            c.executemany(
                "INSERT OR IGNORE INTO seen_videos"
                "(video_id, channel_id, title, first_seen, queued_at, skipped_reason, published_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def recent_queued(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as c:
            cur = c.execute(
                "SELECT v.video_id, v.channel_id, v.title, v.queued_at, v.published_at, "
                "c.name AS channel_name "
                "FROM seen_videos v LEFT JOIN channels c ON c.channel_id = v.channel_id "
                "WHERE v.queued_at IS NOT NULL "
                "ORDER BY COALESCE(v.published_at, v.queued_at) DESC LIMIT ?",
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]

    def total_seen(self) -> int:
        with self._conn() as c:
            cur = c.execute("SELECT COUNT(*) FROM seen_videos")
            return int(cur.fetchone()[0])

    def total_queued(self) -> int:
        with self._conn() as c:
            cur = c.execute(
                "SELECT COUNT(*) FROM seen_videos WHERE queued_at IS NOT NULL"
            )
            return int(cur.fetchone()[0])

    def clear_seen(self) -> int:
        with self._conn() as c:
            cur = c.execute("DELETE FROM seen_videos")
            return cur.rowcount

    def clear_scans(self) -> int:
        with self._conn() as c:
            cur = c.execute("DELETE FROM scan_log")
            return cur.rowcount

    # ---- scan log -------------------------------------------------------

    def start_scan(self) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO scan_log(started_at) VALUES (?)",
                (int(time.time()),),
            )
            return int(cur.lastrowid)

    def finish_scan(
        self,
        scan_id: int,
        channels: int,
        candidates: int,
        new_videos: int,
        queued: int,
        skipped: int = 0,
        error: str | None = None,
    ) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE scan_log SET finished_at = ?, channels = ?, candidates = ?, "
                "new_videos = ?, queued = ?, skipped = ?, error = ? WHERE id = ?",
                (
                    int(time.time()),
                    channels,
                    candidates,
                    new_videos,
                    queued,
                    skipped,
                    error,
                    scan_id,
                ),
            )

    def recent_scans(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._conn() as c:
            cur = c.execute(
                "SELECT id, started_at, finished_at, channels, candidates, "
                "new_videos, queued, skipped, error "
                "FROM scan_log ORDER BY started_at DESC LIMIT ?",
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]

    def last_scan(self) -> dict[str, Any] | None:
        rows = self.recent_scans(limit=1)
        return rows[0] if rows else None

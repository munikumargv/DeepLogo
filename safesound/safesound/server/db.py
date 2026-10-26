"""SQLite storage. One file, no server - fits on the AI Cell / an on-prem VM."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS schools (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, timezone TEXT NOT NULL DEFAULT 'Asia/Dubai',
  settings TEXT NOT NULL DEFAULT '{}', keyword_version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS locations (
  id TEXT PRIMARY KEY, school_id TEXT NOT NULL, name TEXT NOT NULL, type TEXT NOT NULL,
  sensitive INTEGER NOT NULL DEFAULT 0, cameras TEXT NOT NULL DEFAULT '[]',
  schedule TEXT NOT NULL DEFAULT '[]', cleaning TEXT NOT NULL DEFAULT '[]',
  sensor_confirmed INTEGER NOT NULL DEFAULT 0, sensor_false INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS devices (
  id TEXT PRIMARY KEY, school_id TEXT NOT NULL, location_id TEXT NOT NULL, name TEXT,
  key_hash TEXT NOT NULL, last_seen TEXT, keyword_list_version INTEGER, created_at TEXT);
CREATE TABLE IF NOT EXISTS keywords (
  id INTEGER PRIMARY KEY AUTOINCREMENT, school_id TEXT NOT NULL, term TEXT NOT NULL,
  lang TEXT NOT NULL, category TEXT NOT NULL, weight REAL, enabled INTEGER NOT NULL DEFAULT 1,
  confirmed INTEGER NOT NULL DEFAULT 0, false_alarms INTEGER NOT NULL DEFAULT 0,
  created_at TEXT, UNIQUE(school_id, term));
CREATE TABLE IF NOT EXISTS video_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, school_id TEXT NOT NULL, camera_id TEXT NOT NULL,
  ts TEXT NOT NULL, cue TEXT NOT NULL, confidence REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_video ON video_events(camera_id, ts);
CREATE TABLE IF NOT EXISTS incidents (
  id INTEGER PRIMARY KEY AUTOINCREMENT, school_id TEXT NOT NULL, device_id TEXT NOT NULL,
  location_id TEXT NOT NULL, ts TEXT NOT NULL, types TEXT NOT NULL, alert TEXT NOT NULL,
  context TEXT NOT NULL, score INTEGER NOT NULL, level TEXT NOT NULL, action TEXT NOT NULL,
  notified INTEGER NOT NULL, routed_roles TEXT NOT NULL, fusion TEXT NOT NULL,
  status TEXT NOT NULL, outcome TEXT, notes TEXT, acknowledged_by TEXT, acknowledged_at TEXT,
  resolved_by TEXT, resolved_at TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_inc ON incidents(school_id, ts);
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT, school_id TEXT NOT NULL, username TEXT UNIQUE NOT NULL,
  display_name TEXT NOT NULL, role TEXT NOT NULL, pw_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, school_id TEXT, actor TEXT NOT NULL,
  action TEXT NOT NULL, target TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT, school_id TEXT NOT NULL, incident_id INTEGER NOT NULL,
  role TEXT NOT NULL, ts TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, read_by TEXT NOT NULL DEFAULT '[]');
"""

JSON_COLS = {"settings", "cameras", "schedule", "cleaning", "types", "alert", "context",
             "routed_roles", "fusion", "read_by"}


class DB:
    def __init__(self, path: str = "safesound.db"):
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.lock = threading.RLock()
        self.conn.executescript(SCHEMA)

    @staticmethod
    def _row(r: sqlite3.Row | None) -> dict | None:
        if r is None:
            return None
        d = dict(r)
        for k in JSON_COLS & d.keys():
            if isinstance(d[k], str):
                d[k] = json.loads(d[k])
        return d

    def all(self, sql: str, *args) -> list[dict]:
        with self.lock:
            return [self._row(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql: str, *args) -> dict | None:
        with self.lock:
            return self._row(self.conn.execute(sql, args).fetchone())

    def run(self, sql: str, *args) -> int:
        args = tuple(json.dumps(a, ensure_ascii=False) if isinstance(a, (dict, list)) else a for a in args)
        with self.lock:
            return self.conn.execute(sql, args).lastrowid

    def insert(self, table: str, **row) -> int:
        cols = ", ".join(row)
        return self.run(f"INSERT INTO {table} ({cols}) VALUES ({', '.join('?' * len(row))})", *row.values())

    @contextmanager
    def tx(self):
        with self.lock:
            self.conn.execute("BEGIN")
            try:
                yield
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise

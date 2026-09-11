"""SQLite 持久化：草稿模板 / 计划任务 / 发送审计 / 媒体库。"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Draft, SendResult

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS templates (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    payload     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schedules (
    id           TEXT PRIMARY KEY,
    name         TEXT,
    chat_id      TEXT NOT NULL,
    payload      TEXT NOT NULL,
    run_at       TEXT,
    cron         TEXT,
    next_run_at  TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_schedules_due ON schedules(status, next_run_at);

CREATE TABLE IF NOT EXISTS sends (
    id           TEXT PRIMARY KEY,
    chat_id      TEXT,
    method       TEXT,
    ok           INTEGER NOT NULL,
    message_id   INTEGER,
    message_ids  TEXT,
    error        TEXT,
    error_code   INTEGER,
    payload      TEXT,
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sends_chat ON sends(chat_id, created_at DESC);

CREATE TABLE IF NOT EXISTS assets (
    id           TEXT PRIMARY KEY,
    filename     TEXT NOT NULL,
    path         TEXT NOT NULL,
    mime_type    TEXT,
    size_bytes   INTEGER NOT NULL,
    file_id      TEXT,
    created_at   TEXT NOT NULL
);
"""

_UPDATE_SCHEDULE_SQL = (
    "UPDATE schedules SET status=?, next_run_at=?, last_error=?, "
    "attempts=attempts+1, updated_at=? WHERE id=?"
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    """线程安全的 SQLite 封装（WAL 模式）。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=15, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=15000")
            self._local.conn = conn
        yield conn

    def close(self) -> None:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # -- 模板 ----------------------------------------------------------
    def save_template(self, name: str, draft: Draft) -> str:
        now = _utcnow()
        payload = draft.model_dump_json(exclude_none=True)
        with self._connect() as conn:
            row = conn.execute("SELECT id FROM templates WHERE name = ?", (name,)).fetchone()
            if row:
                conn.execute(
                    "UPDATE templates SET payload=?, updated_at=? WHERE id=?",
                    (payload, now, row["id"]),
                )
                return str(row["id"])
            tid = uuid.uuid4().hex[:12]
            conn.execute(
                "INSERT INTO templates(id, name, payload, created_at, updated_at) VALUES(?,?,?,?,?)",
                (tid, name, payload, now, now),
            )
            return tid

    def get_template(self, name: str) -> Draft | None:
        with self._connect() as conn:
            row = conn.execute("SELECT payload FROM templates WHERE name = ?", (name,)).fetchone()
        return Draft.from_json(row["payload"]) if row else None

    def list_templates(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, name, created_at, updated_at FROM templates ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_template(self, name: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM templates WHERE name = ?", (name,))
        return cur.rowcount > 0

    # -- 计划任务 ------------------------------------------------------
    def add_schedule(
        self,
        draft: Draft,
        *,
        chat_id: str | int,
        name: str | None = None,
        run_at: datetime | None = None,
        cron: str | None = None,
        next_run_at: datetime | None = None,
    ) -> str:
        if not run_at and not cron:
            raise ValueError("计划任务必须提供 run_at 或 cron")
        sid = uuid.uuid4().hex[:12]
        now = _utcnow()
        nxt = next_run_at or run_at
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO schedules(id, name, chat_id, payload, run_at, cron, next_run_at,"
                " status, created_at, updated_at) VALUES(?,?,?,?,?,?,?,'pending',?,?)",
                (
                    sid,
                    name,
                    str(chat_id),
                    draft.model_dump_json(exclude_none=True),
                    run_at.isoformat() if run_at else None,
                    cron,
                    nxt.isoformat() if nxt else None,
                    now,
                    now,
                ),
            )
        return sid

    def due_schedules(self, *, now: datetime | None = None, limit: int = 20) -> list[dict[str, Any]]:
        moment = (now or datetime.now(timezone.utc)).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM schedules WHERE status = 'pending' AND next_run_at IS NOT NULL"
                " AND next_run_at <= ? ORDER BY next_run_at LIMIT ?",
                (moment, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_schedule(
        self, schedule_id: str, *, status: str, next_run_at: str | None, error: str | None
    ) -> None:
        with self._connect() as conn:
            conn.execute(_UPDATE_SCHEDULE_SQL, (status, next_run_at, error, _utcnow(), schedule_id))

    def list_schedules(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM schedules"
        args: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            args.append(status)
        sql += " ORDER BY COALESCE(next_run_at, created_at) LIMIT ?"
        args.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def cancel_schedule(self, schedule_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE schedules SET status='cancelled', updated_at=? WHERE id=? AND status='pending'",
                (_utcnow(), schedule_id),
            )
        return cur.rowcount > 0

    # -- 发送审计 ------------------------------------------------------
    def record_send(self, result: SendResult, *, payload: dict[str, Any] | None = None) -> str:
        rid = uuid.uuid4().hex[:12]
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sends(id, chat_id, method, ok, message_id, message_ids, error,"
                " error_code, payload, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    rid,
                    str(result.chat_id) if result.chat_id is not None else None,
                    result.method,
                    1 if result.ok else 0,
                    result.message_id,
                    json.dumps(result.message_ids),
                    result.error,
                    result.error_code,
                    json.dumps(payload, ensure_ascii=False) if payload else None,
                    _utcnow(),
                ),
            )
        return rid

    def recent_sends(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sends ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # -- 媒体库 --------------------------------------------------------
    def add_asset(
        self,
        *,
        filename: str,
        path: str,
        size_bytes: int,
        mime_type: str | None = None,
        file_id: str | None = None,
    ) -> str:
        aid = uuid.uuid4().hex[:12]
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO assets(id, filename, path, mime_type, size_bytes, file_id, created_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (aid, filename, path, mime_type, size_bytes, file_id, _utcnow()),
            )
        return aid

    def list_assets(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM assets ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def remember_file_id(self, asset_id: str, file_id: str) -> None:
        """复用 file_id，避免重复上传同一张图。"""
        with self._connect() as conn:
            conn.execute("UPDATE assets SET file_id=? WHERE id=?", (file_id, asset_id))

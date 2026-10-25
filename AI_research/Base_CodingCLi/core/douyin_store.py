"""Douyin's durable, account-scoped work queue, separate from the running Bilibili outbox."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from core import sqlite_util

ACTIVE = ("QUEUED", "RUNNING")


class DouyinStore:
    def __init__(self, state_dir: str):
        self.path = str(Path(state_dir) / "douyin.db")
        with self.connect(write=True) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, uid TEXT NOT NULL,
                    status TEXT NOT NULL, phase TEXT NOT NULL DEFAULT '', payload TEXT NOT NULL,
                    result TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
                    work_id TEXT NOT NULL DEFAULT '', parent_id TEXT NOT NULL DEFAULT '',
                    script_id TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 0,
                    render_id TEXT NOT NULL DEFAULT '', heartbeat REAL NOT NULL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS once_per_version
                    ON jobs(uid,script_id,version) WHERE kind='UPLOAD';
                CREATE INDEX IF NOT EXISTS work_queue ON jobs(status,created_at);
                CREATE UNIQUE INDEX IF NOT EXISTS once_per_author_comment ON jobs(uid,parent_id) WHERE kind='FIRST_COMMENT';
                CREATE UNIQUE INDEX IF NOT EXISTS once_per_comment_work ON jobs(uid,work_id) WHERE kind='FIRST_COMMENT' AND work_id<>'';
                CREATE TABLE IF NOT EXISTS settings (uid TEXT PRIMARY KEY,first_comment TEXT NOT NULL);
            """)

    def connect(self, write: bool = False):
        return sqlite_util.connect(self.path, write=write)

    @staticmethod
    def decode(row) -> dict | None:
        if row is None:
            return None
        data = dict(row)
        for key in ("payload", "result"):
            data[key] = json.loads(data[key])
        return data

    def get(self, key: str) -> dict | None:
        with self.connect() as conn:
            return self.decode(conn.execute("SELECT * FROM jobs WHERE id=?", (key,)).fetchone())

    def create(self, kind: str, uid: str = "", payload: dict | None = None, *, render: dict | None = None,
               parent_id: str = "") -> dict:
        render = render or {}
        key, now = uuid.uuid4().hex, time.time()
        with self.connect(write=True) as conn:
            conn.execute("INSERT OR IGNORE INTO jobs "
                         "(id,kind,uid,status,payload,parent_id,script_id,version,render_id,heartbeat,created_at,updated_at) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (key, kind, uid, "WAITING" if kind == "FIRST_COMMENT" else "QUEUED", json.dumps(payload or {}, ensure_ascii=False), parent_id,
                          render.get("script_id", ""), render.get("version", 0), render.get("id", ""), now, now, now))
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (key,)).fetchone()
            if row is None and kind == "FIRST_COMMENT":
                row = conn.execute("SELECT * FROM jobs WHERE kind='FIRST_COMMENT' AND uid=? AND parent_id=?", (uid, parent_id)).fetchone()
            if row is None and kind == "UPLOAD":
                row = conn.execute("SELECT * FROM jobs WHERE kind='UPLOAD' AND uid=? AND script_id=? AND version=?",
                                   (uid, render["script_id"], render["version"])).fetchone()
        return self.decode(row)

    def update(self, key: str, *, expected: str | None = None, **fields) -> bool:
        allowed = {"status", "phase", "result", "error", "work_id", "heartbeat", "payload"}
        if not fields or set(fields) - allowed:
            raise ValueError("Unsupported queue field")
        fields = {**fields, "updated_at": time.time()}
        for name in ("result", "payload"):
            if name in fields:
                fields[name] = json.dumps(fields[name], ensure_ascii=False)
        with self.connect(write=True) as conn:
            return bool(conn.execute("UPDATE jobs SET " + ",".join(f"{k}=?" for k in fields) +
                                     " WHERE id=?" + (" AND status=?" if expected else ""),
                                     (*fields.values(), key, *((expected,) if expected else ()))).rowcount)

    def list(self, *, uid: str | None = None, kind: str | None = None, statuses: tuple[str, ...] = ()) -> list[dict]:
        terms, params = [], []
        for key, value in (("uid", uid), ("kind", kind)):
            if value is not None:
                terms.append(f"{key}=?")
                params.append(value)
        if statuses:
            terms.append("status IN (" + ",".join("?" for _ in statuses) + ")")
            params.extend(statuses)
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM jobs" + (" WHERE " + " AND ".join(terms) if terms else "") +
                                " ORDER BY created_at DESC", params).fetchall()
        return [self.decode(row) for row in rows]

    def claim(self) -> dict | None:
        with self.connect(write=True) as conn:
            if conn.execute("SELECT 1 FROM jobs WHERE status='RUNNING' LIMIT 1").fetchone():
                return None
            row = conn.execute("SELECT * FROM jobs WHERE status='QUEUED' ORDER BY created_at LIMIT 1").fetchone()
            if row is None:
                return None
            now = time.time()
            conn.execute("UPDATE jobs SET status='RUNNING',phase='准备',heartbeat=?,updated_at=? WHERE id=?",
                         (now, now, row["id"]))
        return self.get(row["id"])

    def touch(self, key: str) -> None:
        self.update(key, heartbeat=time.time())

    def recover(self) -> None:
        """Caller has proved that the worker lock is free. Keep any accepted work ID."""
        for row in self.list(statuses=("RUNNING",)):
            if time.time() - row["heartbeat"] < 90:
                continue
            if row['kind'] == 'FIRST_COMMENT':
                accepted = bool(row['result'].get('comment_id'))
                uncertain = bool(row['result'].get('submission_started'))
                self.update(row['id'], expected='RUNNING', status='SENT' if accepted else 'UNKNOWN' if uncertain else 'FAILED',
                            phase='评论执行已中断', error='已保留评论回执，不会自动重发。' if accepted or uncertain else '评论提交前检查中断，可重试。',
                            result={**row['result'], 'confirmed_not_submitted': not accepted and not uncertain})
                continue
            status = ("SUBMITTED" if row["work_id"] else "UNKNOWN") if row["kind"] == "UPLOAD" else "FAILED"
            self.update(row["id"], expected="RUNNING", status=status, phase="执行中断",
                        error="执行进程已中断，已保留回执；请核对结果。" if row["kind"] == "UPLOAD" else "连接检查被中断，请重试。")

    def comment_settings(self, uid: str) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT first_comment FROM settings WHERE uid=?", (uid,)).fetchone()
        return json.loads(row[0]) if row else {"enabled": False, "text": "", "pin": False}

    def save_comment_settings(self, uid: str, value: dict) -> dict:
        with self.connect(write=True) as conn:
            conn.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(uid) DO UPDATE SET first_comment=excluded.first_comment",
                         (uid, json.dumps(value, ensure_ascii=False)))
        return value

    def comments(self, uid: str) -> dict[str, dict]:
        return {r['parent_id']: r for r in self.list(uid=uid, kind='FIRST_COMMENT')}

    def activate_comment(self, key: str, uid: str, work_id: str) -> bool:
        """Reserve this account/work exactly once, even across different upload attempts."""
        now = time.time()
        with self.connect(write=True) as conn:
            return bool(conn.execute(
                "UPDATE jobs SET status='QUEUED',work_id=?,phase=?,updated_at=? "
                "WHERE id=? AND uid=? AND kind='FIRST_COMMENT' AND status='WAITING' "
                "AND NOT EXISTS (SELECT 1 FROM jobs other WHERE other.kind='FIRST_COMMENT' "
                "AND other.uid=? AND other.work_id=? AND other.id<>?)",
                (work_id, '视频已公开，等待发送作者评论', now, key, uid, uid, work_id, key)).rowcount)

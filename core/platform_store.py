"""Durable platform outbox and comment inbox. Credentials live in a separate 0600 file."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from core import sqlite_util


class PlatformStore:
    def __init__(self, state_dir: str):
        self.path = str(Path(state_dir) / "platforms.db")
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS uploads (
                    id TEXT PRIMARY KEY, uid TEXT NOT NULL, render_id TEXT NOT NULL,
                    script_id TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL,
                    status TEXT NOT NULL, bvid TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    UNIQUE(uid, render_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS upload_version ON uploads(uid, script_id, version);
                CREATE TABLE IF NOT EXISTS comments (
                    uid TEXT NOT NULL, bvid TEXT NOT NULL, rpid TEXT NOT NULL, root TEXT NOT NULL,
                    author TEXT NOT NULL, author_mid TEXT NOT NULL, message TEXT NOT NULL,
                    ctime INTEGER NOT NULL, likes INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'NEW',
                    draft TEXT NOT NULL DEFAULT '', reply TEXT NOT NULL DEFAULT '',
                    reply_rpid TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL, PRIMARY KEY(uid, bvid, rpid)
                );
                CREATE TABLE IF NOT EXISTS videos (
                    uid TEXT NOT NULL, bvid TEXT NOT NULL, title TEXT NOT NULL,
                    synced_at REAL, last_attempt_at REAL, sync_error TEXT NOT NULL DEFAULT '', PRIMARY KEY(uid, bvid)
                );
            """)
            # Serialize additive migrations when the API and worker first open an old database together.
            conn.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(videos)")}
            for name, definition in (("last_attempt_at", "REAL"), ("sync_error", "TEXT NOT NULL DEFAULT ''")):
                if name not in columns:
                    conn.execute(f"ALTER TABLE videos ADD COLUMN {name} {definition}")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(uploads)")}
            for name, definition in (
                ("first_comment_status", "TEXT NOT NULL DEFAULT 'DISABLED'"),
                ("first_comment_rpid", "TEXT NOT NULL DEFAULT ''"),
                ("first_comment_error", "TEXT NOT NULL DEFAULT ''"),
                ("first_comment_pin_status", "TEXT NOT NULL DEFAULT 'DISABLED'"),
                ("first_comment_pin_error", "TEXT NOT NULL DEFAULT ''"),
                ("first_comment_updated_at", "REAL NOT NULL DEFAULT 0"),
            ):
                if name not in columns:
                    conn.execute(f"ALTER TABLE uploads ADD COLUMN {name} {definition}")

    def connect(self, write: bool = False):
        return sqlite_util.connect(self.path, write=write)

    def settings(self) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT data FROM settings WHERE id=1").fetchone()
        return {"auto_upload": False, "auto_since": 0, "tid": 0,
                "first_comment": {"enabled": False, "text": "", "pin": False},
                "sync_comments": False, **(json.loads(row[0]) if row else {})}

    def save_settings(self, patch: dict) -> dict:
        with self.connect(write=True) as conn:
            row = conn.execute("SELECT data FROM settings WHERE id=1").fetchone()
            old = json.loads(row[0]) if row else {}
            if patch.get("auto_upload") and not old.get("auto_upload"):
                patch = {**patch, "auto_since": time.time()}
            data = {**old, **patch}
            conn.execute("INSERT OR REPLACE INTO settings VALUES (1, ?)", (json.dumps(data),))
        return self.settings()

    def enqueue(self, uid: str, render: dict, payload: dict) -> dict:
        now = time.time()
        with self.connect(write=True) as conn:
            inserted = conn.execute("INSERT OR IGNORE INTO uploads "
                         "(id,uid,render_id,script_id,version,payload,status,created_at,updated_at) "
                         "VALUES (?,?,?,?,?,?,'QUEUED',?,?)",
                         (uuid.uuid4().hex, uid, render["id"], render["script_id"], render["version"],
                          json.dumps(payload, ensure_ascii=False), now, now)).rowcount
            row = conn.execute("SELECT * FROM uploads WHERE uid=? AND script_id=? AND version=?",
                               (uid, render["script_id"], render["version"])).fetchone()
            # Only the newly inserted task receives this immutable, explicitly selected option.
            if inserted and payload.get("first_comment", {}).get("enabled"):
                conn.execute("UPDATE uploads SET first_comment_status='WAITING',first_comment_pin_status=? WHERE id=?",
                             ("WAITING" if payload["first_comment"].get("pin") else "DISABLED", row["id"]))
                row = conn.execute("SELECT * FROM uploads WHERE id=?", (row["id"],)).fetchone()
        return self._upload(row)

    @staticmethod
    def _upload(row) -> dict:
        data = dict(row)
        data["payload"] = json.loads(data["payload"])
        return data

    def uploads(self, uid: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM uploads WHERE uid=? ORDER BY created_at DESC LIMIT 200", (uid,)).fetchall()
        return [self._upload(row) for row in rows]

    def upload_versions(self, uid: str) -> set[tuple[str, int]]:
        """All versions already represented in the outbox, including older rows outside the UI page."""
        with self.connect() as conn:
            return {(row[0], row[1]) for row in conn.execute("SELECT script_id,version FROM uploads WHERE uid=?", (uid,))}

    def script_uploads(self, uid: str, script_id: str) -> list[dict]:
        """All tasks for a document, including versions outside the overview's page."""
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM uploads WHERE uid=? AND script_id=? ORDER BY created_at DESC",
                                (uid, script_id)).fetchall()
        return [self._upload(row) for row in rows]

    def pending_uploads(self, uid: str) -> list[dict]:
        """Pending work must remain discoverable beyond the overview's 200 rows."""
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM uploads WHERE uid=? AND status IN ('QUEUED','SUBMITTED') ORDER BY created_at",
                                (uid,)).fetchall()
        return [self._upload(row) for row in rows]

    def get_upload(self, task_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM uploads WHERE id=?", (task_id,)).fetchone()
        return self._upload(row) if row else None

    def update_upload(self, task_id: str, *, status: str, error: str = "", bvid: str = "", expected: str | None = None) -> bool:
        with self.connect(write=True) as conn:
            return bool(conn.execute("UPDATE uploads SET status=?,error=?,bvid=?,updated_at=? WHERE id=?" +
                                     (" AND status=?" if expected else ""),
                                     (status, error, bvid, time.time(), task_id) + ((expected,) if expected else ())).rowcount)

    def recover_uploads(self) -> None:
        # Caller holds the cross-process worker lock: no living upload is being interrupted.
        with self.connect(write=True) as conn:
            conn.execute("UPDATE uploads SET status='UNKNOWN', error=?, updated_at=? WHERE status='UPLOADING'",
                         ("投稿进程已中断，请在创作中心核对后回填 BV 号。", time.time()))
            conn.execute("UPDATE uploads SET first_comment_status='UNKNOWN',first_comment_error=? "
                         "WHERE first_comment_status='SENDING'",
                         ("评论发送进程已中断，请到视频下核对；系统不会自动重发。",))
            conn.execute("UPDATE uploads SET first_comment_pin_status='UNKNOWN',first_comment_pin_error=? "
                         "WHERE first_comment_pin_status='PINNING'",
                         ("置顶结果未确认，请检查后仅重试置顶。",))

    def first_comment_tasks(self, uid: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM uploads WHERE uid=? AND status='PUBLISHED' AND "
                                "(first_comment_status='WAITING' OR (first_comment_status='SENT' AND "
                                "first_comment_pin_status='WAITING')) ORDER BY first_comment_updated_at,created_at",
                                (uid,)).fetchall()
        return [self._upload(row) for row in rows]

    @staticmethod
    def _comment_busy(conn, uid: str) -> bool:
        conn.execute("UPDATE comments SET status='UNKNOWN',error=? WHERE uid=? AND status='SENDING' AND updated_at<?",
                     ("发送进程已中断，请到原评论核对。", uid, time.time() - 120))
        cutoff = time.time() - 15
        return bool(conn.execute("SELECT 1 FROM comments WHERE uid=? AND "
                                 "(status='SENDING' OR (reply<>'' AND updated_at>?)) LIMIT 1", (uid, cutoff)).fetchone()
                    or conn.execute("SELECT 1 FROM uploads WHERE uid=? AND "
                                    "(first_comment_status='SENDING' OR first_comment_updated_at>?) LIMIT 1",
                                    (uid, cutoff)).fetchone())

    def claim_first_comment(self, task_id: str, uid: str) -> bool:
        with self.connect(write=True) as conn:
            if self._comment_busy(conn, uid):
                return False
            return bool(conn.execute("UPDATE uploads SET first_comment_status='SENDING',first_comment_error='',"
                                     "first_comment_updated_at=? WHERE id=? AND uid=? AND status='PUBLISHED' "
                                     "AND first_comment_status='WAITING'", (time.time(), task_id, uid)).rowcount)

    def finish_first_comment(self, task_id: str, *, status: str, rpid: str = "", error: str = "") -> None:
        with self.connect(write=True) as conn:
            conn.execute("UPDATE uploads SET first_comment_status=?,first_comment_rpid=?,first_comment_error=?,"
                         "first_comment_updated_at=? WHERE id=? AND first_comment_status='SENDING'",
                         (status, rpid, error, time.time(), task_id))

    def first_comment_preflight_error(self, task_id: str, error: str, *, pin: bool = False) -> None:
        with self.connect(write=True) as conn:
            column = "first_comment_pin_error" if pin else "first_comment_error"
            state_column = "first_comment_pin_status" if pin else "first_comment_status"
            conn.execute(f"UPDATE uploads SET {column}=?,first_comment_updated_at=? WHERE id=? "
                         f"AND {state_column}='WAITING'", (error, time.time(), task_id))

    def claim_first_comment_pin(self, task_id: str, uid: str) -> bool:
        with self.connect(write=True) as conn:
            return bool(conn.execute("UPDATE uploads SET first_comment_pin_status='PINNING',first_comment_pin_error='' "
                                     "WHERE id=? AND uid=? AND status='PUBLISHED' AND first_comment_status='SENT' "
                                     "AND first_comment_rpid<>'' AND first_comment_pin_status='WAITING'", (task_id, uid)).rowcount)

    def finish_first_comment_pin(self, task_id: str, *, status: str, error: str = "") -> None:
        with self.connect(write=True) as conn:
            conn.execute("UPDATE uploads SET first_comment_pin_status=?,first_comment_pin_error=? "
                         "WHERE id=? AND first_comment_pin_status='PINNING'", (status, error, task_id))

    def retry_first_comment(self, task_id: str, uid: str, *, pin: bool = False) -> bool:
        with self.connect(write=True) as conn:
            if pin:
                return bool(conn.execute("UPDATE uploads SET first_comment_pin_status='WAITING',first_comment_pin_error='' "
                                         "WHERE id=? AND uid=? AND status='PUBLISHED' AND first_comment_status='SENT' "
                                         "AND first_comment_rpid<>'' AND first_comment_pin_status IN ('FAILED','UNKNOWN')",
                                         (task_id, uid)).rowcount)
            return bool(conn.execute("UPDATE uploads SET first_comment_status='WAITING',first_comment_error='' "
                                     "WHERE id=? AND uid=? AND status='PUBLISHED' AND first_comment_status='FAILED' "
                                     "AND first_comment_rpid=''", (task_id, uid)).rowcount)

    def watch(self, uid: str, bvid: str, title: str) -> None:
        with self.connect(write=True) as conn:
            conn.execute("INSERT INTO videos(uid,bvid,title) VALUES (?,?,?) "
                         "ON CONFLICT(uid,bvid) DO UPDATE SET title=excluded.title", (uid, bvid, title))

    def videos(self, uid: str) -> list[dict]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM videos WHERE uid=? ORDER BY title", (uid,))]

    def sync_attempt(self, uid: str, bvid: str, error: str = "") -> None:
        with self.connect(write=True) as conn:
            conn.execute("UPDATE videos SET last_attempt_at=?,sync_error=? WHERE uid=? AND bvid=?",
                         (time.time(), error, uid, bvid))

    def save_comments(self, uid: str, bvid: str, comments: list[dict]) -> int:
        count = 0
        with self.connect(write=True) as conn:
            for row in comments:
                inserted = conn.execute("INSERT OR IGNORE INTO comments "
                    "(uid,bvid,rpid,root,author,author_mid,message,ctime,likes,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (uid, bvid, str(row["rpid"]), str(row.get("root") or row["rpid"]),
                     str(row.get("member", {}).get("uname", "")), str(row.get("mid", "")),
                     str(row.get("content", {}).get("message", "")), int(row.get("ctime") or 0),
                     int(row.get("like") or 0), time.time())).rowcount
                count += inserted
                if not inserted:
                    conn.execute("UPDATE comments SET message=?,likes=? WHERE uid=? AND bvid=? AND rpid=?",
                                 (str(row.get("content", {}).get("message", "")), int(row.get("like") or 0), uid, bvid, str(row["rpid"])))
            conn.execute("UPDATE videos SET synced_at=?,sync_error='' WHERE uid=? AND bvid=?", (time.time(), uid, bvid))
        return count

    def comments(self, uid: str, bvid: str = "") -> list[dict]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM comments WHERE uid=?" +
                    (" AND bvid=?" if bvid else "") + " ORDER BY ctime DESC LIMIT 500", (uid, bvid) if bvid else (uid,))]

    def get_comment(self, uid: str, bvid: str, rpid: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM comments WHERE uid=? AND bvid=? AND rpid=?", (uid, bvid, rpid)).fetchone()
        return dict(row) if row else None

    def edit_comment(self, uid: str, bvid: str, rpid: str, *, draft: str, handled: bool) -> None:
        with self.connect(write=True) as conn:
            conn.execute("UPDATE comments SET draft=?,status=CASE WHEN status IN ('NEW','HANDLED') "
                         "THEN ? ELSE status END WHERE uid=? AND bvid=? AND rpid=?",
                         (draft, "HANDLED" if handled else "NEW", uid, bvid, rpid))

    def claim_reply(self, uid: str, bvid: str, rpid: str, text: str) -> bool:
        with self.connect(write=True) as conn:
            if self._comment_busy(conn, uid):
                return False
            return bool(conn.execute("UPDATE comments SET status='SENDING',reply=?,updated_at=? "
                "WHERE uid=? AND bvid=? AND rpid=? AND status IN ('NEW','HANDLED')",
                (text, time.time(), uid, bvid, rpid)).rowcount)

    def finish_reply(self, uid: str, bvid: str, rpid: str, *, reply_rpid: str = "", error: str = "") -> None:
        with self.connect(write=True) as conn:
            conn.execute("UPDATE comments SET status=?,reply_rpid=?,error=?,updated_at=? WHERE uid=? AND bvid=? AND rpid=?",
                         ("REPLIED" if reply_rpid else "UNKNOWN", reply_rpid, error, time.time(), uid, bvid, rpid))

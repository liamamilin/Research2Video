"""Video scripts, their versions, and their AutoVideo renders: ``state/video.db``.

Statuses
--------
script:  RESEARCHING → WRITING → DRAFT → APPROVED, or FAILED (with a reason).
         Restyling a script with versions goes DRAFT/APPROVED → WRITING → DRAFT;
         ``stage`` names the adaptation step while WRITING.
render:  SUBMITTING → QUEUED → RUNNING → DOWNLOADING → COMPLETED,
         or FAILED / CANCELLED

A render row is the ledger AutoVideo's integration guide asks callers to keep:
AutoVideo has no idempotency key, so the row is what stops a retry from
creating a second video.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from core import sqlite_util

DB_FILE = os.path.join("state", "video.db")

ACTIVE_SCRIPT_STATUSES = ("RESEARCHING", "WRITING")
ACTIVE_RENDER_STATUSES = ("SUBMITTING", "QUEUED", "RUNNING", "DOWNLOADING")

#: Work in flight touches its row this often...
HEARTBEAT_SECONDS = 20
#: ...so a row this quiet has lost its worker. Startup alone is not proof:
#: the gateway can start short-lived extra instances that exit at once
#: (port taken), and each runs startup recovery against the shared database.
STALE_AFTER_SECONDS = 120

_DB_PATH_OVERRIDE: Optional[str] = None
_lock = threading.Lock()

_SCRIPT_JSON = ("topic",)
_VERSION_JSON = ("sections", "length_check", "meta")
_RENDER_JSON = ("request", "artifacts")


def db_path() -> str:
    return _DB_PATH_OVERRIDE or DB_FILE


def set_db_path(path: Optional[str]) -> None:
    global _DB_PATH_OVERRIDE
    _DB_PATH_OVERRIDE = path


def use_state_dir(state_dir: str) -> None:
    set_db_path(os.path.join(state_dir, "video.db"))


@contextmanager
def connect(write: bool = False) -> Iterator[sqlite3.Connection]:
    with sqlite_util.connect(db_path(), write=write) as conn:
        conn.row_factory = sqlite3.Row
        yield conn


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def init_db() -> None:
    with _lock, connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS scripts (
                id TEXT PRIMARY KEY,
                column_key TEXT NOT NULL,
                topic TEXT NOT NULL,
                status TEXT NOT NULL,
                research_job TEXT NOT NULL DEFAULT '',
                report_path TEXT NOT NULL DEFAULT '',
                current_version INTEGER NOT NULL DEFAULT 0,
                approved_version INTEGER,
                error TEXT,
                created_by TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_scripts_status ON scripts(status);
            CREATE TABLE IF NOT EXISTS script_versions (
                script_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                title TEXT NOT NULL,
                sections TEXT NOT NULL,
                length_check TEXT NOT NULL,
                edit_type TEXT NOT NULL,
                meta TEXT NOT NULL DEFAULT '{}',
                created_by TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                PRIMARY KEY (script_id, version)
            );
            CREATE TABLE IF NOT EXISTS renders (
                id TEXT PRIMARY KEY,
                script_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                status TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                source_text TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                request TEXT NOT NULL DEFAULT '{}',
                autovideo_job_id TEXT,
                active_stage TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                resolved_video_form TEXT,
                error_code TEXT,
                error_message TEXT,
                artifacts TEXT NOT NULL DEFAULT '[]',
                submit_started_at TEXT,
                submitted_at TEXT,
                completed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_renders_script ON renders(script_id);
            -- One fact sheet per research report (keyed by its sha256), shared by every style.
            CREATE TABLE IF NOT EXISTS fact_sheets (
                id TEXT PRIMARY KEY,
                report_path TEXT NOT NULL DEFAULT '',
                sheet TEXT NOT NULL,
                meta TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            -- Where a script's video went up (a script may be published more than once).
            CREATE TABLE IF NOT EXISTS publications (
                id TEXT PRIMARY KEY,
                script_id TEXT NOT NULL,
                version INTEGER,
                render_id TEXT,
                platform TEXT NOT NULL DEFAULT 'bilibili',
                url TEXT NOT NULL,
                published_on TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                recorded_by TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_publications_script ON publications(script_id);
            -- A before-publishing check that what a flash script says still holds (a web search over its claims).
            CREATE TABLE IF NOT EXISTS rechecks (
                id TEXT PRIMARY KEY,
                script_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                status TEXT NOT NULL,
                verdict TEXT,
                summary TEXT NOT NULL DEFAULT '',
                job TEXT NOT NULL DEFAULT '',
                report_path TEXT NOT NULL DEFAULT '',
                error TEXT,
                started_by TEXT NOT NULL DEFAULT '',
                heartbeat_at REAL,
                created_at TEXT NOT NULL,
                finished_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_rechecks_script ON rechecks(script_id);
            -- Topics the user marked "not writing": reversible, hidden from the to-write counts and the pipeline.
            CREATE TABLE IF NOT EXISTS dismissed_topics (
                topic_id TEXT PRIMARY KEY,
                dismissed_at TEXT NOT NULL
            );
            -- One render in flight per script.
            CREATE UNIQUE INDEX IF NOT EXISTS idx_renders_one_active ON renders(script_id)
                WHERE status IN ('SUBMITTING', 'QUEUED', 'RUNNING', 'DOWNLOADING');
            """
        )
        for table in ("scripts", "renders"):
            columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            if "heartbeat_at" not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN heartbeat_at REAL")
        columns = {r[1] for r in conn.execute("PRAGMA table_info(scripts)")}
        if "stage" not in columns:
            conn.execute("ALTER TABLE scripts ADD COLUMN stage TEXT")
        if "archived" not in columns:
            conn.execute("ALTER TABLE scripts ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
        if "production" not in columns:  # the production config that made it (spec/production.md); '' before them
            conn.execute("ALTER TABLE scripts ADD COLUMN production TEXT NOT NULL DEFAULT ''")
        from core.video import autopilot_store, plans, reports, steps, topic_store
        autopilot_store.init(conn)
        plans.init(conn)
        topic_store.init(conn)
        steps.init(conn)
        reports.init(conn)  # after topic_store: it reads topic_imports to tell a source report from a research one


def _decode(row: sqlite3.Row | None, json_fields: tuple[str, ...]) -> dict | None:
    if row is None:
        return None
    out = dict(row)
    for key in json_fields:
        if out.get(key) is not None:
            out[key] = json.loads(out[key])
    return out


def _encode(fields: dict, json_fields: tuple[str, ...]) -> dict:
    return {k: (json.dumps(v, ensure_ascii=False) if k in json_fields else v) for k, v in fields.items()}


# --- Scripts -----------------------------------------------------------------


def create_script(column_key: str, topic: dict, created_by: str = "") -> dict:
    now = _now()
    script_id = uuid.uuid4().hex
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO scripts (id, column_key, topic, status, created_by, created_at, updated_at,"
            " heartbeat_at) VALUES (?, ?, ?, 'RESEARCHING', ?, ?, ?, ?)",
            (script_id, column_key, json.dumps(topic, ensure_ascii=False), created_by, now, now, time.time()),
        )
    return get_script(script_id)


def get_script(script_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM scripts WHERE id = ?", (script_id,)).fetchone()
    return _decode(row, _SCRIPT_JSON)


def list_scripts(limit: int = 100, archived: bool = False) -> list[dict]:
    """Newest first. Archived scripts (kept, but out of the way) are listed only when asked for."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM scripts WHERE archived = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (1 if archived else 0, limit),
        ).fetchall()
    return [_decode(r, _SCRIPT_JSON) for r in rows]


def count_scripts_by_column() -> dict[str, int]:
    """Column (recipe) key -> how many scripts were made with it, archived ones included."""
    with connect() as conn:
        return {row[0]: row[1] for row in conn.execute("SELECT column_key, COUNT(*) FROM scripts GROUP BY column_key")}


def update_script(script_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields = _encode({**fields, "updated_at": _now()}, _SCRIPT_JSON)
    assignments = ", ".join(f"{k} = ?" for k in fields)
    with connect(write=True) as conn:
        conn.execute(f"UPDATE scripts SET {assignments} WHERE id = ?", (*fields.values(), script_id))


def topic_ids_in_use() -> set[str]:
    """Topic ids that already have a script that did not fail (a roundup covers each of its items)."""
    with connect() as conn:
        rows = conn.execute("SELECT topic FROM scripts WHERE status != 'FAILED'").fetchall()
    ids: set = set()
    for row in rows:
        topic = json.loads(row["topic"])
        ids.add(topic.get("id"))
        ids.update(topic.get("items") or [])
    return ids - {None}


def scripts_by_topic() -> dict[str, list[dict]]:
    """Topic id → the scripts that cover it (not failed, newest first), a roundup covering each of its items."""
    with connect() as conn:
        rows = conn.execute("SELECT id, column_key, topic, status, created_at, archived FROM scripts"
                            " WHERE status != 'FAILED' ORDER BY created_at DESC").fetchall()
    covered: dict[str, list[dict]] = {}
    for row in rows:
        topic = json.loads(row["topic"])
        for topic_id in {topic.get("id"), *(topic.get("items") or [])} - {None}:
            covered.setdefault(topic_id, []).append({"id": row["id"], "column_key": row["column_key"],
                                                      "status": row["status"], "created_at": row["created_at"],
                                                      "archived": row["archived"]})
    return covered


@contextmanager
def beating(touch, key: str, every: float = HEARTBEAT_SECONDS) -> Iterator[None]:
    """Call ``touch(key)`` now and every ``every`` seconds until the block exits."""
    stop = threading.Event()

    def _beat() -> None:
        while True:
            try:
                touch(key)
            except Exception:  # noqa: BLE001 - a missed beat must not kill the work
                pass
            if stop.wait(every):
                return

    thread = threading.Thread(target=_beat, name=f"video-heartbeat-{key[:8]}", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=5)


def touch_script(script_id: str) -> None:
    with connect(write=True) as conn:
        conn.execute("UPDATE scripts SET heartbeat_at = ? WHERE id = ?", (time.time(), script_id))


def fail_interrupted(stale_after: float = STALE_AFTER_SECONDS) -> int:
    """On startup: research or writing whose worker has stopped beating.

    A script that already has versions was being restyled: it goes back to
    DRAFT with its versions intact rather than FAILED.
    """
    stale = time.time() - stale_after
    where = f" WHERE status IN {ACTIVE_SCRIPT_STATUSES} AND (heartbeat_at IS NULL OR heartbeat_at < ?)"
    with connect(write=True) as conn:
        restyled = conn.execute(
            "UPDATE scripts SET status = 'DRAFT', stage = NULL, error = ?, updated_at = ?"
            + where + " AND current_version > 0",
            ("服务重启，换风格被中断；之前的版本都还在。", _now(), stale),
        ).rowcount
        failed = conn.execute(
            "UPDATE scripts SET status = 'FAILED', stage = NULL, error = ?, updated_at = ?" + where,
            ("服务重启，研究或写稿被中断；可以重新生成。", _now(), stale),
        ).rowcount
        # a draft shown while its audits ran: the audits died with the process, the draft stays
        audits = conn.execute(
            "UPDATE scripts SET stage = NULL, error = ?, updated_at = ? WHERE status = 'DRAFT' AND stage IS NOT NULL"
            " AND (heartbeat_at IS NULL OR heartbeat_at < ?)",
            ("服务重启，事实和风格审计被中断；审校结果只有规则检查。", _now(), stale),
        ).rowcount
    return restyled + failed + audits + fail_interrupted_rechecks()


# --- Publications and rechecks ------------------------------------------------------


def add_publication(script_id: str, *, url: str, published_on: str, version: Optional[int] = None,
                    render_id: Optional[str] = None, platform: str = "bilibili", title: str = "", note: str = "",
                    recorded_by: str = "") -> dict:
    pub_id = uuid.uuid4().hex
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO publications (id, script_id, version, render_id, platform, url, published_on, title, note,"
            " recorded_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pub_id, script_id, version, render_id, platform, url, published_on, title, note, recorded_by, _now()),
        )
    return get_publication(pub_id)


def get_publication(pub_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM publications WHERE id = ?", (pub_id,)).fetchone()
    return dict(row) if row else None


def list_publications(script_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM publications WHERE script_id = ? ORDER BY published_on DESC, created_at DESC",
                            (script_id,)).fetchall()
    return [dict(r) for r in rows]


def delete_publication(pub_id: str) -> bool:
    with connect(write=True) as conn:
        return conn.execute("DELETE FROM publications WHERE id = ?", (pub_id,)).rowcount > 0


def latest_render_by_script() -> dict[str, dict]:
    """Script id -> its newest render's status and when it was made (what the outputs page shows as 成片)."""
    with connect() as conn:
        rows = conn.execute("SELECT script_id, status, created_at FROM renders ORDER BY created_at").fetchall()
    return {r["script_id"]: {"status": r["status"], "created_at": r["created_at"]} for r in rows}


def published_script_ids() -> set[str]:
    with connect() as conn:
        return {r["script_id"] for r in conn.execute("SELECT DISTINCT script_id FROM publications")}


def dismissed_topic_ids() -> set[str]:
    with connect() as conn:
        return {r["topic_id"] for r in conn.execute("SELECT topic_id FROM dismissed_topics")}


def set_dismissed(topic_id: str, dismissed: bool) -> None:
    with connect() as conn:
        if dismissed:
            conn.execute("INSERT OR REPLACE INTO dismissed_topics (topic_id, dismissed_at) VALUES (?, ?)",
                         (topic_id, time.strftime("%Y-%m-%dT%H:%M:%S%z")))
        else:
            conn.execute("DELETE FROM dismissed_topics WHERE topic_id = ?", (topic_id,))


def published_topic_ids() -> set[str]:
    """Topic ids whose script has been published."""
    with connect() as conn:
        rows = conn.execute("SELECT topic FROM scripts WHERE id IN (SELECT script_id FROM publications)").fetchall()
    ids: set = set()
    for row in rows:
        topic = json.loads(row["topic"])
        ids.add(topic.get("id"))
        ids.update(topic.get("items") or [])
    return ids - {None}


class ActiveRecheckError(RuntimeError):
    pass


def create_recheck(script_id: str, version: int, started_by: str = "") -> dict:
    """Record a recheck as RUNNING; one per script at a time."""
    with connect(write=True) as conn:
        if conn.execute("SELECT 1 FROM rechecks WHERE script_id = ? AND status = 'RUNNING'", (script_id,)).fetchone():
            raise ActiveRecheckError("这篇文稿正在复核")
        recheck_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO rechecks (id, script_id, version, status, started_by, heartbeat_at, created_at)"
            " VALUES (?, ?, ?, 'RUNNING', ?, ?, ?)",
            (recheck_id, script_id, version, started_by, time.time(), _now()),
        )
    return get_recheck(recheck_id)


def get_recheck(recheck_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM rechecks WHERE id = ?", (recheck_id,)).fetchone()
    return dict(row) if row else None


def list_rechecks(script_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM rechecks WHERE script_id = ? ORDER BY created_at DESC, id DESC",
                            (script_id,)).fetchall()
    return [dict(r) for r in rows]


def update_recheck(recheck_id: str, **fields: Any) -> None:
    if not fields:
        return
    assignments = ", ".join(f"{k} = ?" for k in fields)
    with connect(write=True) as conn:
        conn.execute(f"UPDATE rechecks SET {assignments} WHERE id = ?", (*fields.values(), recheck_id))


def touch_recheck(recheck_id: str) -> None:
    with connect(write=True) as conn:
        conn.execute("UPDATE rechecks SET heartbeat_at = ? WHERE id = ?", (time.time(), recheck_id))


def fail_interrupted_rechecks(stale_after: float = STALE_AFTER_SECONDS) -> int:
    with connect(write=True) as conn:
        return conn.execute(
            "UPDATE rechecks SET status = 'FAILED', error = ?, finished_at = ? WHERE status = 'RUNNING'"
            " AND (heartbeat_at IS NULL OR heartbeat_at < ?)",
            ("服务重启，复核被中断；可以重新复核。", _now(), time.time() - stale_after),
        ).rowcount


# --- Fact sheets ----------------------------------------------------------------


def get_fact_sheet(sheet_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM fact_sheets WHERE id = ?", (sheet_id,)).fetchone()
    return _decode(row, ("sheet", "meta"))


def save_fact_sheet(sheet_id: str, *, report_path: str, sheet: dict, meta: dict | None = None) -> None:
    with connect(write=True) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO fact_sheets (id, report_path, sheet, meta, created_at) VALUES (?, ?, ?, ?, ?)",
            (sheet_id, report_path, json.dumps(sheet, ensure_ascii=False),
             json.dumps(meta or {}, ensure_ascii=False), _now()),
        )


# --- Versions ----------------------------------------------------------------


def add_version(
    script_id: str,
    *,
    title: str,
    sections: list[dict],
    length_check: dict,
    edit_type: str,
    meta: dict | None = None,
    created_by: str = "",
) -> int:
    """Store a new version and make it current. Returns its number."""
    now = _now()
    with connect(write=True) as conn:
        current = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM script_versions WHERE script_id = ?", (script_id,)
        ).fetchone()[0]
        version = current + 1
        conn.execute(
            "INSERT INTO script_versions (script_id, version, title, sections, length_check,"
            " edit_type, meta, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (script_id, version, title, json.dumps(sections, ensure_ascii=False),
             json.dumps(length_check, ensure_ascii=False), edit_type,
             json.dumps(meta or {}, ensure_ascii=False), created_by, now),
        )
        conn.execute(
            "UPDATE scripts SET current_version = ?, updated_at = ? WHERE id = ?",
            (version, now, script_id),
        )
    return version


def update_version_audit(script_id: str, version: int, *, sections: list[dict], meta: dict,
                         length_check: dict | None = None) -> None:
    """Replace a GENERATED version's ``meta`` and sections once its audits finish (the audit marks paragraphs, and
    a rewrite from its findings changes the text, so the length check comes with it)."""
    with connect(write=True) as conn:
        if length_check is None:
            conn.execute("UPDATE script_versions SET sections = ?, meta = ? WHERE script_id = ? AND version = ?",
                         (json.dumps(sections, ensure_ascii=False), json.dumps(meta, ensure_ascii=False),
                          script_id, version))
        else:
            conn.execute("UPDATE script_versions SET sections = ?, meta = ?, length_check = ?"
                         " WHERE script_id = ? AND version = ?",
                         (json.dumps(sections, ensure_ascii=False), json.dumps(meta, ensure_ascii=False),
                          json.dumps(length_check, ensure_ascii=False), script_id, version))


def get_version(script_id: str, version: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM script_versions WHERE script_id = ? AND version = ?", (script_id, version)
        ).fetchone()
    return _decode(row, _VERSION_JSON)


def list_versions(script_id: str) -> list[dict]:
    """Versions, newest first, without their text; ``style`` is the style label of adapted ones."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT script_id, version, title, edit_type, length_check, created_by, created_at,"
            " json_extract(meta, '$.style.label') AS style"
            " FROM script_versions WHERE script_id = ? ORDER BY version DESC",
            (script_id,),
        ).fetchall()
    return [_decode(r, ("length_check",)) for r in rows]


# --- Renders -----------------------------------------------------------------


class ActiveRenderError(RuntimeError):
    pass


def create_render(script_id: str, version: int, *, title: str, source_text: str,
                  source_sha256: str, request: dict) -> dict:
    now = _now()
    render_id = uuid.uuid4().hex
    try:
        with connect(write=True) as conn:
            conn.execute(
                "INSERT INTO renders (id, script_id, version, status, title, source_text,"
                " source_sha256, request, created_at, updated_at)"
                " VALUES (?, ?, ?, 'SUBMITTING', ?, ?, ?, ?, ?, ?)",
                (render_id, script_id, version, title, source_text, source_sha256,
                 json.dumps(request, ensure_ascii=False), now, now),
            )
    except sqlite3.IntegrityError as exc:
        raise ActiveRenderError("这篇文稿已有进行中的出片任务") from exc
    return get_render(render_id)


def get_render(render_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM renders WHERE id = ?", (render_id,)).fetchone()
    return _decode(row, _RENDER_JSON)


def list_renders(script_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM renders WHERE script_id = ? ORDER BY created_at DESC, id DESC", (script_id,)
        ).fetchall()
    return [_decode(r, _RENDER_JSON) for r in rows]


def touch_render(render_id: str) -> None:
    with connect(write=True) as conn:
        conn.execute("UPDATE renders SET heartbeat_at = ? WHERE id = ?", (time.time(), render_id))


def orphaned_render_ids(stale_after: float = STALE_AFTER_SECONDS) -> list[str]:
    """In-flight renders whose poller has stopped beating (none is following them)."""
    with connect() as conn:
        rows = conn.execute(
            f"SELECT id FROM renders WHERE status IN {ACTIVE_RENDER_STATUSES}"
            " AND (heartbeat_at IS NULL OR heartbeat_at < ?)",
            (time.time() - stale_after,),
        ).fetchall()
    return [r["id"] for r in rows]


def owned_autovideo_job_ids() -> set[str]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT autovideo_job_id FROM renders WHERE autovideo_job_id IS NOT NULL"
        ).fetchall()
    return {r["autovideo_job_id"] for r in rows}


def update_render(render_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields = _encode({**fields, "updated_at": _now()}, _RENDER_JSON)
    assignments = ", ".join(f"{k} = ?" for k in fields)
    with connect(write=True) as conn:
        conn.execute(f"UPDATE renders SET {assignments} WHERE id = ?", (*fields.values(), render_id))

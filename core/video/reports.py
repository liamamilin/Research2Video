"""Research reports as records: one row per report file, which any number of scripts can be written from.

Until phase 4 a report was only a path on the script (``scripts.report_path``), so writing a second video from the
same research took a special case (``from_script``, the roundup's discovery report). Now each report a script uses is
recorded once (``research_reports``) and scripts point at it (``scripts.report_id``); a new script can be started
from any recorded report, whatever recipe wrote the first one. ``report_path`` stays on the script and is still what
the writing code reads. See spec/video-reports.md.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Optional

from core.video import store

SCHEMA = """
CREATE TABLE IF NOT EXISTS research_reports (
    id TEXT PRIMARY KEY,
    report_path TEXT NOT NULL UNIQUE,      -- relative to the workspace, as scripts store it
    kind TEXT NOT NULL,                    -- video_research | topic_source | other
    job TEXT NOT NULL DEFAULT '',          -- the job that wrote it, when known
    topic_id TEXT NOT NULL DEFAULT '',     -- the topic it was researched for (a video research job)
    topic_title TEXT NOT NULL DEFAULT '',
    research TEXT NOT NULL DEFAULT '',     -- the recipe's research template, when one was used
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""

KINDS = {"video_research": "文稿调研", "topic_source": "选题来源报告", "other": "其他报告"}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _kind(conn, report_path: str, job: str) -> str:
    if job.startswith("video/"):
        return "video_research"
    if conn.execute("SELECT 1 FROM topic_imports WHERE report_path = ? LIMIT 1", (report_path,)).fetchone():
        return "topic_source"
    return "video_research" if report_path.startswith("output/video/") else "other"


def _record(conn, report_path: str, *, job: str = "", topic: Optional[dict] = None, research: str = "",
            created_by: str = "") -> str:
    row = conn.execute("SELECT id FROM research_reports WHERE report_path = ?", (report_path,)).fetchone()
    if row:
        return row[0]
    topic = topic or {}
    kind = _kind(conn, report_path, job)
    researched = kind == "video_research"
    report_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO research_reports (id, report_path, kind, job, topic_id, topic_title, research, created_by,"
        " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (report_id, report_path, kind, job, str(topic.get("id") or "") if researched else "",
         str(topic.get("title") or "") if researched else "", research, created_by, _now()))
    return report_id


def init(conn) -> None:
    """Create the table and the scripts' ``report_id``, and record every report a script already uses."""
    conn.executescript(SCHEMA)
    columns = {r[1] for r in conn.execute("PRAGMA table_info(scripts)")}
    if "report_id" not in columns:
        conn.execute("ALTER TABLE scripts ADD COLUMN report_id TEXT")
    rows = conn.execute("SELECT id, report_path, research_job, topic, created_by FROM scripts"
                        " WHERE report_path != '' AND (report_id IS NULL OR report_id = '') ORDER BY created_at").fetchall()
    import json

    for script_id, report_path, job, topic, created_by in rows:
        try:
            topic = json.loads(topic or "{}")
        except ValueError:
            topic = {}
        # a script cut from another's report (from_report) did not research it: the report's own job did
        report_id = _record(conn, report_path, job=job if not topic.get("from_report") else "",
                            topic=topic if not topic.get("from_report") else None, created_by=created_by)
        conn.execute("UPDATE scripts SET report_id = ? WHERE id = ?", (report_id, script_id))


def attach(script_id: str, report_path: str, *, job: str = "", topic: Optional[dict] = None, research: str = "",
           created_by: str = "") -> str:
    """Record ``report_path`` (once) and point the script at it. Returns the report's id."""
    with store.connect(write=True) as conn:
        report_id = _record(conn, report_path, job=job, topic=topic, research=research, created_by=created_by)
        conn.execute("UPDATE scripts SET report_id = ? WHERE id = ?", (report_id, script_id))
    return report_id


def _scripts(conn, report_id: str) -> list[dict]:
    import json

    out = []
    for row in conn.execute("SELECT id, column_key, status, topic, archived, created_at FROM scripts"
                            " WHERE report_id = ? ORDER BY created_at", (report_id,)):
        try:
            title = json.loads(row[3] or "{}").get("title", "")
        except ValueError:
            title = ""
        out.append({"id": row[0], "column_key": row[1], "status": row[2], "title": title,
                    "archived": bool(row[4]), "created_at": row[5]})
    return out


def _row(conn, row) -> dict:
    keys = ("id", "report_path", "kind", "job", "topic_id", "topic_title", "research", "created_by", "created_at")
    report = dict(zip(keys, row))
    report["kind_label"] = KINDS.get(report["kind"], report["kind"])
    report["scripts"] = _scripts(conn, report["id"])
    return report


_COLUMNS = "id, report_path, kind, job, topic_id, topic_title, research, created_by, created_at"


def get(report_id: str) -> Optional[dict]:
    with store.connect() as conn:
        row = conn.execute(f"SELECT {_COLUMNS} FROM research_reports WHERE id = ?", (report_id,)).fetchone()
        return _row(conn, row) if row else None


def list_reports(limit: int = 100) -> list[dict]:
    """Newest first, each with the scripts written from it."""
    with store.connect() as conn:
        rows = conn.execute(f"SELECT {_COLUMNS} FROM research_reports ORDER BY created_at DESC LIMIT ?",
                            (limit,)).fetchall()
        return [_row(conn, row) for row in rows]


def exists_on_disk(report: dict, workspace: str) -> bool:
    path = report["report_path"]
    return os.path.isfile(path if os.path.isabs(path) else os.path.join(workspace, path))

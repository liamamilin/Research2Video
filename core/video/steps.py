"""A script's production steps — research, write, audit, approve, render — and a log of who started what.

The steps are read off what the records already say (the script's status, stage, report, versions, approval and its
renders), never stored as a second copy: the restart sweep, the batches and the routes all change those records
directly, and a stored step state would drift from them. What the records do not say — who started or reran a step,
when, and why — goes in ``script_events``. See spec/video-steps.md.
"""

from __future__ import annotations

import time
from typing import Optional

from core.video import store

STEPS = ("research", "write", "audit", "approve", "render")
LABELS = {"research": "调研", "write": "写稿", "audit": "审计", "approve": "定稿", "render": "出片"}
#: DONE / RUNNING / FAILED / PENDING, plus SKIPPED (not needed for this script), WAITING (on the user or the batch),
#: INCOMPLETE (ran but its model part did not finish: an audit that timed out).
STATES = ("PENDING", "RUNNING", "DONE", "FAILED", "SKIPPED", "WAITING", "INCOMPLETE")

SCHEMA = """
CREATE TABLE IF NOT EXISTS script_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    script_id TEXT NOT NULL,
    step TEXT NOT NULL,
    event TEXT NOT NULL,             -- start | rerun | resume | approve | render | hold | fail
    note TEXT NOT NULL DEFAULT '',
    by TEXT NOT NULL DEFAULT '',     -- a user name, or autopilot
    at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_script_events_script ON script_events(script_id);
"""


def init(conn) -> None:
    conn.executescript(SCHEMA)


def log(script_id: str, step: str, event: str, note: str = "", by: str = "") -> None:
    with store.connect(write=True) as conn:
        conn.execute("INSERT INTO script_events (script_id, step, event, note, by, at) VALUES (?, ?, ?, ?, ?, ?)",
                     (script_id, step, event, note, by, time.time()))


def events(script_id: str) -> list[dict]:
    with store.connect() as conn:
        rows = conn.execute("SELECT step, event, note, by, at FROM script_events WHERE script_id = ? ORDER BY id",
                            (script_id,)).fetchall()
    return [{"step": r[0], "event": r[1], "note": r[2], "by": r[3],
             "at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(r[4]))} for r in rows]


def _step(name: str, state: str, note: str = "") -> dict:
    return {"step": name, "label": LABELS[name], "state": state, "note": note}


def derive(script: dict, version: Optional[dict], renders: list[dict], *, research_mode: str = "job") -> list[dict]:
    """The five steps of ``script`` as its records show them now. ``version`` is the current version (or None)."""
    status, stage = script["status"], script.get("stage")
    topic = script.get("topic") or {}
    has_report = bool(script.get("report_path"))
    has_version = bool(script.get("current_version"))
    failed_before_report = status == "FAILED" and not has_report and not has_version
    error = script.get("error") or ""

    if topic.get("from_report"):
        research = _step("research", "SKIPPED", "用已有的研究报告，没有再调研")
    elif has_report:
        research = _step("research", "DONE")
    elif status == "RESEARCHING":
        research = _step("research", "RUNNING")
    elif failed_before_report:
        research = _step("research", "FAILED", error)
    elif research_mode == "none":
        research = _step("research", "SKIPPED", "配方不调研")
    else:
        research = _step("research", "PENDING")

    if status == "WRITING":
        write = _step("write", "RUNNING", stage or "")
    elif has_version:
        write = _step("write", "DONE", f"v{script['current_version']}")
    elif status == "FAILED" and has_report:
        write = _step("write", "FAILED", error)
    else:
        write = _step("write", "PENDING")

    checks = ((version or {}).get("meta") or {}).get("checks") or {}
    if status == "DRAFT" and stage:
        audit = _step("audit", "RUNNING", stage)
    elif not has_version or status == "WRITING":
        audit = _step("audit", "PENDING")
    elif checks.get("model_ran"):
        audit = _step("audit", "DONE")
    elif checks.get("model_checks_stale"):  # a hand edit re-runs only the rule checks
        audit = _step("audit", "INCOMPLETE", "改过稿子，模型审计没有对这一版重跑")
    else:
        audit = _step("audit", "INCOMPLETE", "模型审计没有跑完，只有规则检查")

    approved = script.get("approved_version")
    if approved:
        approve = _step("approve", "DONE", f"v{approved}")
    elif has_version and status == "DRAFT" and not stage:
        approve = _step("approve", "WAITING", "自动流水线会自动定稿" if script.get("created_by") == "autopilot" else "等你定稿")
    else:
        approve = _step("approve", "PENDING")

    current = [r for r in renders if approved and r["version"] == approved]
    if any(r["status"] == "COMPLETED" for r in current):
        render = _step("render", "DONE")
    elif any(r["status"] in store.ACTIVE_RENDER_STATUSES for r in current):
        render = _step("render", "RUNNING", next(r["status"] for r in current if r["status"] in store.ACTIVE_RENDER_STATUSES))
    elif current:
        render = _step("render", "FAILED", current[0].get("error_message") or "")
    elif approved:
        render = _step("render", "WAITING", "定稿了，还没出片")
    else:
        render = _step("render", "PENDING")
    return [research, write, audit, approve, render]

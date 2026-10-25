"""The timed work the schedule page does not manage itself, listed next to the plans it does.

The page edits Job plans (``state/schedules.db``), crontab lines and the two round agents. Two more things run on a
clock: the video pipeline's weekly batches (``autopilot_plans`` in ``state/video.db``, edited on the home board) and
the daily maintenance agent (``com.arec.maintenance``, installed by ``scripts/install_launchd.sh``). They are shown
here read-only, each with where it is changed and how its last run went, so one page answers "what runs on its own,
and did it work". Nothing here writes a plist or a plan.
"""

from __future__ import annotations

import os
import plistlib
import time
from typing import Optional

from web.settings import get_settings

MAINTENANCE_LABEL = "com.arec.maintenance"


def _iso(ts: Optional[float]) -> Optional[str]:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(ts)) if ts else None


def video_plans() -> list[dict]:
    """The weekly video batches and how the last batch of each went."""
    import sqlite3

    from core.video import autopilot_store as ap

    try:
        plans, runs = ap.list_plans(), ap.list_runs(limit=200)
    except sqlite3.Error:  # no video database yet
        return []
    rows = []
    for plan in plans:
        last = next((r for r in runs if r.get("plan_id") == plan["id"]), None)
        outcome = None
        if last:
            items = last.get("items") or []
            done = sum(i["state"] == "DONE" for i in items)
            outcome = {"status": last["status"], "note": last.get("note") or "",
                       "started_at": _iso(last.get("started_at")),
                       "empty": last["status"] == "DONE" and done == 0, "done": done, "items": len(items)}
        rows.append({
            "id": f"video:{plan['id']}", "kind": "video_batch", "title": plan["name"],
            "schedule": plan["expression"], "enabled": bool(plan["enabled"]),
            "next_run": _iso(plan["next_fire"]) if plan["enabled"] else None,
            "last_run": outcome, "where": "看板首页的「无人值守流水线」卡片", "link": "/",
            "cost": "每批启动选题发现（花搜索额度）和每篇文稿的调研",
        })
    return rows


def maintenance() -> Optional[dict]:
    """The daily maintenance agent, if installed: its time, and when its log was last written."""
    from web.services import launchd_agents

    path = launchd_agents.agent_dir() / f"{MAINTENANCE_LABEL}.plist"
    try:
        with open(path, "rb") as fh:
            config = plistlib.load(fh)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None
    calendar = config.get("StartCalendarInterval") or {}
    schedule = (f"{calendar.get('Minute', 0)} {calendar.get('Hour', '*')} * * *"
                if isinstance(calendar, dict) and calendar else "")
    logs = get_settings().paths.logs_dir
    out, err = (os.path.join(logs, name) for name in ("maintenance.log", "maintenance.err.log"))
    ran_at = os.path.getmtime(out) if os.path.isfile(out) else None
    errored = os.path.isfile(err) and os.path.getsize(err) > 0 and (ran_at is None or os.path.getmtime(err) >= ran_at)
    return {
        "id": MAINTENANCE_LABEL, "kind": "maintenance", "title": "每日维护（清理日志、会话、备份，整理数据库）",
        "schedule": schedule, "enabled": True, "next_run": None,
        "last_run": {"status": "failed" if errored else "ok", "started_at": _iso(ran_at),
                     "note": "错误输出见 logs/maintenance.err.log" if errored else ""} if ran_at else None,
        "where": "launchd，由 scripts/install_launchd.sh 安装；本页只读", "link": None, "cost": "",
    }


def rows() -> list[dict]:
    """Everything timed that this page shows but does not edit."""
    found = video_plans()
    agent = maintenance()
    return found + ([agent] if agent else [])

"""Automation alerts: what ran on its own and went wrong, for the home board (spec/video-architecture.md, phase 1).

Every automatic thing already records its result somewhere — job history, autopilot batches, plans, rounds — but
nobody looked: a batch wrote nothing, a job was restarted 70 times, a weekly slot was skipped, and the board said
nothing. This gathers those records into one list. It only reads; each alert has a stable key, and an alert the
user has acknowledged stays off the list.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Optional
from urllib.parse import quote

from core.video import autopilot_store as ap
from web.settings import get_settings

#: How far back to look.
WINDOW_DAYS = 7
#: A job cancelled this many times in one day was restarted over and over (each start spends searches).
RESTART_LOOP = 3
#: A batch still picking topics after this long is stuck.
TOPICS_STUCK_SECONDS = 3 * 3600
#: A plan whose slot passed this long ago without firing: the console was not woken.
PLAN_LATE_SECONDS = 2 * 3600
#: Jobs made per script live here; their failures show on the script itself.
_PER_SCRIPT_JOBS = ("video/", "vod/")


def _ts(text: str) -> Optional[float]:
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S%z").timestamp()
    except (TypeError, ValueError):
        return None


def _alert(key: str, kind: str, at: float, title: str, detail: str = "", link: str = "") -> dict:
    return {"key": key, "kind": kind, "at": at, "title": title, "detail": detail, "link": link}


def _history(state_dir: str, name: str) -> list[dict]:
    path = os.path.join(state_dir, "history", name.replace("/", "_").replace("\\", "_") + ".jsonl")
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()[-2000:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def job_alerts(since: float) -> list[dict]:
    """Failed runs, and days a job was cancelled again and again, for every job in the jobs folder.

    A job's history may sit under its path (``monitoring/x``) or its short name (``x``), depending on who started it."""
    from core.config import list_jobs

    settings = get_settings()
    alerts = []
    for name in list_jobs(settings.paths.jobs_dir):
        if name.startswith(_PER_SCRIPT_JOBS):
            continue
        short = name.rsplit("/", 1)[-1]
        entries = _history(settings.paths.state_dir, name)
        if short != name:
            entries += _history(settings.paths.state_dir, short)
        cancelled: dict[str, list[dict]] = {}
        for entry in entries:
            at = _ts(entry.get("ts"))
            if at is None or at < since:
                continue
            if entry.get("status") == "failed":
                alerts.append(_alert(f"job:{name}:{entry['ts']}", "job_failed", at, f"Job {name} 失败",
                                     str(entry.get("error") or "")[:300], f"/jobs/{quote(name, safe='')}"))
            elif entry.get("status") == "cancelled":
                cancelled.setdefault(entry["ts"][:10], []).append(entry)
        for day, runs in cancelled.items():
            if len(runs) < RESTART_LOOP:
                continue  # a cancel or two is someone stopping it by hand
            searches = sum(int((r.get("usage") or {}).get("searches") or 0) for r in runs)
            starters = "、".join(sorted({r.get("user") or "未知" for r in runs}))
            alerts.append(_alert(f"job-cancels:{name}:{day}", "job_restarted", _ts(runs[-1]["ts"]) or since,
                                 f"Job {name} 在 {day} 被取消了 {len(runs)} 次",
                                 f"发起：{starters}；这些没跑完的运行共花了 {searches} 次搜索。"
                                 "通常是同一个任务被反复重新启动（启动同名任务会取消正在跑的那个）。",
                                 f"/jobs/{quote(name, safe='')}"))
    return alerts


def batch_alerts(since: float, now: float) -> list[dict]:
    """Autopilot batches that wrote nothing, items that failed or wait for the user, batches stuck picking."""
    alerts = []
    for run in ap.list_runs(limit=100):
        if run["started_at"] < since or run["started_by"] == "probe":
            continue
        when = time.strftime("%m-%d %H:%M", time.localtime(run["started_at"]))
        who = "周计划" if run.get("plan_id") else "手动"
        if run["status"] == "DONE" and not run["items"]:
            alerts.append(_alert(f"batch-empty:{run['id']}", "batch_empty", run["started_at"],
                                 f"{when} 的{who}批次一篇也没写", run["note"], "/"))
        elif run["status"] == "RUNNING" and run["stage"] == "TOPICS" and now - run["started_at"] > TOPICS_STUCK_SECONDS:
            alerts.append(_alert(f"batch-stuck:{run['id']}", "batch_stuck", run["started_at"],
                                 f"{when} 的{who}批次还在等选题（{(now - run['started_at']) / 3600:.0f} 小时）",
                                 run["note"], "/"))
        bad = [i for i in run["items"] if i["state"] in ("FAILED", "HELD")]
        if bad:
            failed = sum(i["state"] == "FAILED" for i in bad)
            held = len(bad) - failed
            parts = [f"失败 {failed} 篇" if failed else "", f"等你处理 {held} 篇" if held else ""]
            alerts.append(_alert(f"batch-items:{run['id']}:{failed}:{held}", "batch_items", run["started_at"],
                                 f"{when} 的{who}批次：" + "，".join(filter(None, parts)),
                                 "；".join(f"{(i['topic'] or {}).get('title', '')}：{i['note']}" for i in bad)[:500],
                                 "/"))
    return alerts


def plan_alerts(now: float) -> list[dict]:
    """A weekly plan whose slot has passed without firing: the scheduler did not wake the console."""
    alerts = []
    for plan in ap.list_plans():
        if plan["enabled"] and plan["next_fire"] and now - plan["next_fire"] > PLAN_LATE_SECONDS:
            due = time.strftime("%m-%d %H:%M", time.localtime(plan["next_fire"]))
            alerts.append(_alert(f"plan-late:{plan['id']}:{plan['next_fire']}", "plan_late", plan["next_fire"],
                                 f"周计划「{plan['name']}」{due} 该开的批次没开",
                                 "到点没有被叫醒：看看调度执行器（launchd）是否在跑、控制台能否被网关拉起。", "/"))
    return alerts


def round_alerts(since: float) -> list[dict]:
    """Intelligence rounds that ended in anything but success."""
    from web.runner.pipeline import list_round_states

    alerts = []
    for state in list_round_states():
        at = _ts((state.get("finished_at") or "") + time.strftime("%z")) if state.get("finished_at") else None
        if at is None or at < since or state.get("status") in ("success", "running"):
            continue
        alerts.append(_alert(f"round:{state['date']}:{state.get('finished_at')}", "round_failed", at,
                             f"情报轮次 {state['date']} 结束状态：{state.get('status')}", "", "/rounds"))
    return alerts


def cap_alerts(now: float) -> list[dict]:
    """A matrix that used up its month of searches (its Jobs will not start) or is close to it."""
    from core import matrices

    settings = get_settings()
    when = datetime.fromtimestamp(now).astimezone()
    out = []
    for matrix in matrices.load_all(settings.paths.config_dir).values():
        if not matrix.monthly_searches:
            continue
        spent = matrices.month_searches(matrix, when, settings.paths.jobs_dir)
        used = f"本月已用 {spent}/{matrix.monthly_searches} 次搜索"
        link = f"/matrices/{quote(matrix.key)}"
        if spent >= matrix.monthly_searches:
            out.append(_alert(f"matrix_cap:{matrix.key}:{when:%Y-%m}", "matrix_cap", now, f"「{matrix.name}」本月搜索额度用完了",
                              f"{used}，它的 Job 都不会再启动（定时的和手动的）。到矩阵页调高上限，或等下个月", link))
        elif spent >= matrices.WARN_RATIO * matrix.monthly_searches:
            out.append(_alert(f"matrix_cap_near:{matrix.key}:{when:%Y-%m}", "matrix_cap", now, f"「{matrix.name}」本月搜索额度快用完了",
                              f"{used}，到上限它的 Job 会停", link))
    return out


def alerts(now: Optional[float] = None, *, include_acked: bool = False) -> list[dict]:
    """Everything that went wrong on its own in the last week, newest first, minus what the user acknowledged."""
    now = time.time() if now is None else now
    since = now - WINDOW_DAYS * 86400
    out = job_alerts(since) + batch_alerts(since, now) + plan_alerts(now) + round_alerts(since) + cap_alerts(now)
    if not include_acked:
        acked = ap.acked_alerts()
        out = [a for a in out if a["key"] not in acked]
    return sorted(out, key=lambda a: a["at"], reverse=True)

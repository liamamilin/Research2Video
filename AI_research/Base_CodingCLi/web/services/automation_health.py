"""Automation alerts: what ran on its own and went wrong, for the home board (spec/video-architecture.md, phase 1).

Every automatic thing already records its result somewhere — job history, autopilot batches, plans, rounds — but
nobody looked: a batch wrote nothing, a job was restarted 70 times, a weekly slot was skipped, and the board said
nothing. This gathers those records into one list. It only reads; each alert has a stable key, and an alert the
user has acknowledged stays off the list.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime
from typing import Optional
from urllib.parse import quote

from core.video import autopilot_store as ap
from core.fileio import tail_lines
from web.settings import get_settings

#: How far back to look.
WINDOW_DAYS = 7
#: A job cancelled this many times in one day was restarted over and over (each start spends searches).
RESTART_LOOP = 3
#: A batch still picking topics after this long is stuck.
TOPICS_STUCK_SECONDS = 3 * 3600
#: A plan whose slot passed this long ago without firing: the console was not woken.
PLAN_LATE_SECONDS = 2 * 3600
#: Bilibili video data runs every five minutes; this long without a success means it stopped arriving.
ANALYTICS_STALE_SECONDS = 2 * 3600
#: The daily watch reading (from 12:30) normally finishes within about 90 minutes; this long after, it is late.
WATCH_LATE_SECONDS = 4 * 3600
#: No reading saved for this long while late: the pass has stopped, not just slow.
WATCH_STALL_SECONDS = 3600
#: A provider out of credits that has not been tried for this long is probably no longer in use.
SEARCH_BLOCKED_QUIET_SECONDS = 2 * 86400
_SEARCH_NAMES = {"exa": "Exa", "youcom": "You.com", "tavily": "Tavily", "brave": "Brave", "serper": "Serper",
                 "duckduckgo": "DuckDuckGo", "bing": "Bing", "google": "Google"}
#: Bad items sharing a cause across batches are folded into one alert from this many on.
GROUP_MIN = 3
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
        lines = tail_lines(path, 2000)
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
        # A failure the job has since run past successfully needs nothing from the user.
        recovered = max((_ts(e.get("ts")) or 0 for e in entries if e.get("status") == "success"), default=0)
        for entry in entries:
            at = _ts(entry.get("ts"))
            if at is None or at < since:
                continue
            if entry.get("status") == "failed" and at > recovered:
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


def batch_alerts(since: float, now: float, acked: frozenset = frozenset()) -> list[dict]:
    """Autopilot batches that wrote nothing, items that failed or wait for the user, batches stuck picking.

    Many batches failing on one cause (search down for a night) are one alert, not one per batch."""
    alerts: list[dict] = []
    causes: dict[str, list] = {}
    for run in ap.list_runs(limit=100):
        if run["started_at"] < since or run["started_by"] == "probe":
            continue
        when = time.strftime("%m-%d %H:%M", time.localtime(run["started_at"]))
        who = "周计划" if run.get("plan_id") else "手动"
        if run["status"] == "DONE" and not run["items"]:
            if not run.get("plan_id"):
                continue  # started by hand: the person who pressed the button was told "no topics" on the spot
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
            key = f"batch-items:{run['id']}:{failed}:{held}"
            if key in acked:
                continue
            for item in bad:
                causes.setdefault(_cause(item["note"]), []).append((run, item))
            parts = [f"失败 {failed} 篇" if failed else "", f"等你处理 {held} 篇" if held else ""]
            alerts.append(_alert(key, "batch_items", run["started_at"],
                                 f"{when} 的{who}批次：" + "，".join(filter(None, parts)),
                                 "；".join(f"{(i['topic'] or {}).get('title', '')}：{i['note']}" for i in bad)[:500],
                                 "/") | {"_runs": {run["id"]}, "_causes": {_cause(i["note"]) for i in bad}})
    return _group_by_cause(alerts, causes)


def _cause(note: str) -> str:
    """What an item failed on, without the parts that differ per item (request ids, queries, links)."""
    from core import search_health
    if search_health.is_outage_error(note):
        return "联网搜索不可用"
    text = re.sub(r"https?://\S+|[0-9a-f]{16,}|\d+", "#", note or "")
    return text[:80]


def _short(note: str) -> str:
    """A search-outage note without the per-provider dump: the sentence that says so, and its advice."""
    from core.errors import SEARCH_UNAVAILABLE_PREFIX
    head, _, rest = note.partition(SEARCH_UNAVAILABLE_PREFIX)
    stop = rest.find("。")
    advice = note[note.rfind("请到"):] if "请到" in note else ""
    return (head + SEARCH_UNAVAILABLE_PREFIX + (rest[:stop + 1] if stop >= 0 else "") + advice).strip()


def _group_by_cause(alerts: list[dict], causes: dict[str, list]) -> list[dict]:
    """Batches whose every bad item shares a cause with GROUP_MIN or more others become one alert for that cause."""
    grouped = {cause: rows for cause, rows in causes.items() if len({r["id"] for r, _ in rows}) > 1
               and len(rows) >= GROUP_MIN}
    out = []
    for alert in alerts:
        causes_here = alert.pop("_causes", None)
        alert.pop("_runs", None)
        if causes_here is None or not causes_here <= grouped.keys():
            out.append(alert)
    for cause, rows in grouped.items():
        rows = sorted(rows, key=lambda row: row[0]["started_at"])
        first = rows[0][0]["started_at"]
        failed = sum(item["state"] == "FAILED" for _, item in rows)
        held = len(rows) - failed
        parts = [f"失败 {failed} 篇" if failed else "", f"等你处理 {held} 篇" if held else ""]
        titles = [(item["topic"] or {}).get("title", "") for _, item in rows]
        sample = "、".join(titles[:5]) + (f" 等 {len(titles)} 篇" if len(titles) > 5 else "")
        note = _short(rows[0][1]["note"]) if cause == "联网搜索不可用" else rows[0][1]["note"][:300]
        out.append(_alert(f"batch-cause:{hashlib.sha1(cause.encode()).hexdigest()[:10]}:{first:.0f}:{len(rows)}",
                          "batch_cause", first,
                          f"{time.strftime('%m-%d %H:%M', time.localtime(first))} 起 {len({r['id'] for r, _ in rows})} "
                          f"个批次因同一原因卡住：" + "，".join(filter(None, parts)),
                          f"原因：{note} 涉及：{sample}", "/"))
    return out


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


def analytics_alerts(now: float) -> list[dict]:
    """Bilibili video data that stopped arriving while collection is meant to be on.

    2026-10-31 00:10 the collector's plan was switched off outside the product (no audit entry) while the account
    still said "collect"; the data page showed one grey line and nothing was collected for two days. Pausing through
    the product clears the account flag too, so only a stop nobody asked for raises this."""
    import sqlite3

    from core.platform_analytics_store import SCHEDULE_ID
    from core.schedule_store import ScheduleStore

    state_dir = get_settings().paths.state_dir
    folder = os.path.join(state_dir, "platforms", "bilibili")
    path = os.path.join(state_dir, "platform_analytics.db")
    try:
        with open(os.path.join(folder, "account.json"), encoding="utf-8") as fh:
            uid = str(json.load(fh).get("uid") or "")
        if not uid or not os.path.isfile(os.path.join(folder, "credentials.json")) or not os.path.isfile(path):
            return []  # not connected: disconnecting is a deliberate stop
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2) as db:
            db.row_factory = sqlite3.Row
            account = db.execute("SELECT * FROM analytics_accounts WHERE uid=?", (uid,)).fetchone()
            try:
                watch = db.execute("SELECT * FROM analytics_watch_state WHERE uid=?", (uid,)).fetchone()
                read_at = db.execute("SELECT max(collected_at) FROM analytics_watch WHERE uid=?", (uid,)).fetchone()[0] or 0
            except sqlite3.OperationalError:
                watch, read_at = None, 0  # before the first daily reading created the table
    except (OSError, ValueError, sqlite3.Error):
        return []
    if account is None or not account["enabled"]:
        return []
    store = ScheduleStore(state_dir)
    plan = store.get(SCHEDULE_ID)
    link = "/analytics?tab=video"
    last = account["last_success_at"] or 0
    since = time.strftime("%m-%d %H:%M", time.localtime(last)) if last else "从未成功"
    if not plan or not plan["enabled"]:
        changed = plan["updated_at"] if plan else 0
        return [_alert(f"analytics-off:{uid}:{changed}", "analytics_off", changed or now,
                       "B 站视频数据的定时采集被关掉了",
                       f"账号仍设为定时采集，但采集计划没有开启，播放数据停在 {since}。"
                       "到数据分析的视频数据页点「开启定时采集」；有意暂停请用那里的「暂停定时采集」。", link)]
    if now - last <= ANALYTICS_STALE_SECONDS:
        return watch_alerts(uid, watch, read_at, now)
    reasons = []
    if account["blocked_until"] and account["blocked_until"] > now:
        reasons.append("B 站暂时拒绝请求，退避到 " + time.strftime("%m-%d %H:%M", time.localtime(account["blocked_until"])))
    if account["error"]:
        reasons.append(f"最近的错误：{account['error'][:200]}")
    if not store.health(now).get("online"):
        reasons.append("调度执行器离线，定时任务都不会运行")
    hours = (now - last) / 3600
    return [_alert(f"analytics-stale:{uid}:{last}", "analytics_stale", now,
                   f"B 站视频数据 {hours:.0f} 小时没有更新",
                   f"定时采集开着，但最近一次成功是 {since}。" + ("；".join(reasons) or "没有记录到错误，可以在视频数据页点「立即同步数据」看结果。"),
                   link)]


def watch_alerts(uid: str, watch, read_at: float, now: float) -> list[dict]:
    """Plays arrive but the daily creator-centre reading (completion, watch time) has not finished hours after noon,
    and it is failing or has stopped saving readings (a pass that is still moving is left alone).

    Those endpoints are not documented; a change on Bilibili's side would otherwise only show as missing columns."""
    from web.services.platform_analytics import watch_due

    due = watch_due(now)
    if now < due.timestamp() + WATCH_LATE_SECONDS:
        return []
    day = due.date().isoformat()
    done = watch is not None and watch["day"] == day and watch["next_at"] >= due.timestamp() + 86400
    if done:
        return []
    error = (watch["error"] if watch is not None else "") or ""
    if not error and now - read_at < WATCH_STALL_SECONDS:
        return []  # still reading, a few videos every five minutes
    last = watch["day"] if watch is not None and watch["day"] else "从未读到"
    return [_alert(f"analytics-watch:{uid}:{day}", "analytics_watch", due.timestamp(),
                   f"{day[5:]} 的完播率和观看时长没有读完",
                   f"播放数据正常更新，但创作中心的观看数据（每天 {due:%H:%M} 起读）到现在还没读完，"
                   f"最近一次读到是 {last}。" + (f"最近的错误：{error[:200]}" if error else "没有记录到错误。")
                   + "这两个接口 B 站没有公开，可能改了格式；每周复盘里这些列会缺数。", "/analytics?tab=review")]


def review_notices(now: float) -> list[dict]:
    """A week's review saved in the last week: say it is there to read (acknowledging it clears it)."""
    from core.weekly_review_store import ReviewStore

    out = []
    for kept in ReviewStore(get_settings().paths.state_dir).saved():
        if kept["stage"] != "24h" or now - kept["saved_at"] > WINDOW_DAYS * 86400:
            continue
        week = time.strftime("%m-%d", time.localtime(kept["start"]))
        s = kept["summary"]
        median = "—" if s.get("median_24h") is None else f"{s['median_24h']:g}"
        out.append(_alert(f"review:{kept['start']:.0f}:{kept['stage']}", "review_saved", kept["saved_at"],
                          f"{week} 这周的复盘已存档：{kept['findings']} 条结论、{kept['suggestions']} 条建议",
                          f"发布 {s.get('videos', 0)} 条，24 小时播放中位数 {median}。",
                          f"/analytics?tab=review&start={kept['start']:.0f}&stage=24h"))
    return out


def experiment_notices(now: float) -> list[dict]:
    """A/B experiment reminders (spec/ab-testing.md §9a). The module is optional: any failure shows none."""
    try:
        from core.experiments import notices
        return notices.recent(get_settings().paths.state_dir, now, WINDOW_DAYS)
    except Exception:  # noqa: BLE001 - reminders must never break the home board
        import logging
        logging.getLogger(__name__).warning("experiment reminders unavailable", exc_info=True)
        return []


def search_alerts(now: float) -> list[dict]:
    """A search provider out of credits or refusing its key: one alert per episode, until it answers again."""
    from core import search_health
    alerts = []
    down = search_health.outage(now) is not None
    for name, row in search_health.blocked().items():
        if now - float(row.get("at") or 0) > SEARCH_BLOCKED_QUIET_SECONDS:
            continue
        label = _SEARCH_NAMES.get(name, name)
        since = float(row.get("since") or row.get("at") or now)
        credits = row.get("cause") == "credits"
        title = f"{label} 搜索额度用完" if credits else f"{label} 搜索 key 被拒绝"
        detail = (f"{time.strftime('%m-%d %H:%M', time.localtime(since))} 起每次调用都返回"
                  f"{'余额不足' if credits else ' key 无效或无权限'}，最近一次在 "
                  f"{time.strftime('%m-%d %H:%M', time.localtime(float(row.get('at') or since)))}。")
        detail += ("所有搜索服务都不可用：自动调研和出片在等待恢复，不会算作失败。" if down else
                   "其他搜索服务在顶替，调研结果可能变少。")
        detail += ("充值后 30 分钟内会自动重试，恢复后这条提醒自动消失。" if credits else
                   "到设置里更新 key 后会立即重试。")
        alerts.append(_alert(f"search-{row.get('cause')}:{name}:{int(since)}", "search_blocked", since, title, detail,
                             "/settings"))
    return alerts


def alerts(now: Optional[float] = None, *, include_acked: bool = False) -> list[dict]:
    """Everything that went wrong on its own in the last week, newest first, minus what the user acknowledged."""
    now = time.time() if now is None else now
    since = now - WINDOW_DAYS * 86400
    acked = frozenset() if include_acked else frozenset(ap.acked_alerts())
    out = (job_alerts(since) + batch_alerts(since, now, acked) + plan_alerts(now) + round_alerts(since)
           + cap_alerts(now) + analytics_alerts(now) + review_notices(now) + search_alerts(now)
           + experiment_notices(now))
    out = [a for a in out if a["key"] not in acked]
    return sorted(out, key=lambda a: a["at"], reverse=True)

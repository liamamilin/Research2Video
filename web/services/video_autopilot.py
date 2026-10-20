"""The unattended pipeline: topics → script → audit → approve → render, resuming where it stopped.

``tick()`` is idempotent. Each call reads the real state of every item's script and render and does
the next thing that is missing, so a restart or a crash never repeats finished work:

* no script yet → create it (its id is recorded before the work starts);
* script FAILED or its audits cut off → resume it (research is reused, a saved draft is not rewritten);
* draft with audits done → approve automatically, with a credibility label (core/video/credibility.py);
* approved → one render (a render row with an AutoVideo job id is only polled, never resubmitted);
* render completed → done. Nothing here publishes.

Nothing here waits for the user. The label (有出处可信 … 有严重问题) says how far the words can be trusted and is
shown wherever a video is listed; an item is held only for a technical failure (the script failed after its retries,
the render failed after its retry).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from typing import Optional

from core.fileio import file_lock
from core.video import autopilot_store as ap
from core.video import columns as video_columns
from core.video import credibility
from core.video import flash as video_flash
from core.video import store
from core.video import topics as video_topics
from web.services import video_render, video_workflow
from web.settings import get_settings

logger = logging.getLogger("ai_research.web.video_autopilot")

#: A stage that fails is retried this many times, then the item is held for the user.
MAX_RETRIES = 2
#: A render AutoVideo failed is submitted again this many times (a render is 15-30 minutes of its time).
RENDER_RETRIES = 1
#: Scripts researching at once (each research uses search quota); renders go one at a time.
MAX_SCRIPTS = 2
MAX_RENDERS = 1
#: Wait this long for a fresh discovery report before using the newest one there is.
TOPICS_WAIT_SECONDS = 70 * 60

DEFAULT_SPEC = {"picks": [{"column": "flash", "count": 3, "source": "flash"},
                          {"column": "point_short", "count": 2, "source": "flash"}],
                "refresh": True}


# --- picking ---------------------------------------------------------------

def _loader(source: str):
    return video_flash.load if source == "flash" else video_topics.load


def resolve_pick(pick: dict) -> dict:
    """A pick by ``cell`` (what the matrix says it can be written from) becomes ``column`` + ``source``."""
    if not pick.get("cell"):
        return pick
    from core.video import recipes as matrix

    config_dir = get_settings().paths.config_dir
    sources = matrix.topic_sources(config_dir, pick["cell"])
    if not sources:
        raise ValueError(f"格子 {pick['cell']} 没有选题来源，不能自动挑选题")
    if video_columns.for_new_script(config_dir, pick["cell"]).topic_kind != "single":
        raise ValueError(f"格子 {pick['cell']} 一期用几条选题合成，不能按条数自动挑")
    return {"column": pick["cell"], "count": pick.get("count", 1), "source": sources[0]}


def coverage() -> list[dict]:
    """Per cell: its topic sources, how many topics wait to be written and how many are taken, newest report date."""
    from core.video import recipes as matrix

    settings = get_settings()
    covered = store.scripts_by_topic()
    dismissed = store.dismissed_topic_ids()
    cache: dict[str, tuple] = {}
    rows = []
    for cell in matrix.catalog(settings.paths.config_dir):
        sources = cell.get("topic_sources") or []
        if not cell["usable"] or cell["key"] is None:
            continue
        row = {"cell": cell["key"], "label": cell["label"], "status": cell["status"], "sources": sources,
               "open": 0, "written": 0, "dismissed": 0, "report_date": None,
               "multi": bool(cell["column"] and cell["column"].get("topic_kind") == "roundup"),
               "autopilot": bool(cell["column"] and cell["column"].get("topic_kind", "single") == "single")}
        for source in sources:
            if source not in cache:
                cache[source] = _loader(source)(settings.paths.output_dir)
            date, topics = cache[source]
            row["report_date"] = max(filter(None, [row["report_date"], date]), default=None)
            # written is per cell: a topic written as a quick take is still open for a knowledge point
            done = {t.id for t in topics if any(x["column_key"] == cell["key"] for x in covered.get(t.id, []))}
            skipped = {t.id for t in topics if t.id in dismissed} - done
            row["open"] += len(topics) - len(done) - len(skipped)
            row["written"] += len(done)
            row["dismissed"] += len(skipped)
        rows.append(row)
    return rows


def pick_topics(spec: dict) -> list[dict]:
    """Items for a batch: per pick, the best-scored topics that have no script, no publication and no item yet."""
    out_dir = get_settings().paths.output_dir
    taken = store.topic_ids_in_use() | store.published_topic_ids() | ap.used_topic_ids() | store.dismissed_topic_ids()
    items: list[dict] = []
    for pick in (resolve_pick(p) for p in spec.get("picks", [])):
        loader = _loader(pick.get("source", "flash"))
        _, topics = loader(out_dir)
        # news that is already old ranks after fresher news (it is still picked when nothing fresher is left)
        for topic in sorted(topics, key=lambda t: (bool(getattr(t, "stale", lambda: False)()), -(t.score or 0))):
            if len([i for i in items if i["pick"] is pick]) >= int(pick.get("count", 1)):
                break
            if topic.id in taken:
                continue
            taken.add(topic.id)
            items.append({"column_key": pick["column"], "topic": topic.as_dict(), "pick": pick})
    for item in items:
        del item["pick"]
    return items


DISCOVERY_JOB = "ai_flash_discovery"
#: A discovery run cut off (the app restarted) is started once more, no more: each one costs about 20 searches.
DISCOVERY_STARTS = 2


def fresh_topics(spec: dict, run: dict) -> bool:
    """True when the batch can start picking.

    A stale discovery report starts today's job — once. Starting a job cancels the one already running under the
    same name, so a start on every tick would restart it forever."""
    if not spec.get("refresh"):
        return True
    if video_flash.latest_report(get_settings().paths.output_dir) == time.strftime("%Y-%m-%d"):
        return True
    running = _discovery_running()
    started_at = run.get("discovery_at")
    if running:
        if started_at is None:  # someone else started it (by hand): wait for that one
            ap.update_run(run["id"], discovery_at=time.time())
            return False
        if time.time() - started_at <= TOPICS_WAIT_SECONDS:
            return False
        ap.update_run(run["id"], note="今天的快讯发现没有在限时内完成，用最新的一期")
        return True
    if int(run.get("discovery_tries") or 0) < DISCOVERY_STARTS:
        ap.update_run(run["id"], discovery_at=time.time(), discovery_tries=int(run.get("discovery_tries") or 0) + 1)
        _start_discovery()
        return False
    ap.update_run(run["id"], note="今天的快讯发现没有跑出报告，用最新的一期")
    return True


_discovery_lock = threading.RLock()


def discovery_status() -> dict:
    """Is the flash discovery job running, and when its newest report came out."""
    out = get_settings().paths.output_dir
    date = video_flash.latest_report(out)
    path = video_flash.report_path(out, date) if date else None
    return {"running": _discovery_running(), "report_date": date,
            "report_at": os.path.getmtime(path) if path and os.path.isfile(path) else None,
            "searches": 20}


def start_discovery_if_idle() -> bool:
    """Start today's flash discovery unless it is running — starting a job cancels the one running under its name."""
    with _discovery_lock:  # the check and the start are one step, so a double click starts it once
        if _discovery_running():
            return False
        _start_discovery()
        return True


def _discovery_running() -> bool:
    from web.runner.registry import TaskRegistry
    return TaskRegistry().is_running(DISCOVERY_JOB)


def _start_discovery() -> None:
    from web.runner.executor import start_job

    settings = get_settings()
    with _discovery_lock:
        try:
            start_job(DISCOVERY_JOB, "autopilot", config_dir=settings.paths.config_dir,
                      jobs_dir=settings.paths.jobs_dir)
        except Exception as exc:  # noqa: BLE001 - the job cannot start; the next tick sees it is not running
            logger.info("flash discovery not started: %s", exc)


# --- the label (there is no gate) ---------------------------------------------

def judge(script: dict, column) -> dict:
    """The credibility label of the current version, and what it let through. It never stops the pipeline."""
    version = store.get_version(script["id"], script["current_version"])
    label = credibility.label(version)
    checks = video_workflow.rule_checks(version, column)
    summary = checks.get("summary") or {}
    return {"label": label["tier"], "text": label["text"], "reasons": label["reasons"],
            "cited_share": label["cited_share"], "audited": bool(checks.get("model_ran")),
            "ignored": {"must": max(0, int(summary.get("must", 0)) - int(summary.get("rule_must", 0))),
                        "warn": int(summary.get("warn", 0))},
            "version": version["version"]}


# --- advancing one item ----------------------------------------------------

def _retries(item: dict, key: str) -> int:
    return int((item.get("retries") or {}).get(key, 0))


def _bump(item: dict, key: str) -> dict:
    retries = {**(item.get("retries") or {}), key: _retries(item, key) + 1}
    ap.update_item(item["id"], retries=retries)
    return {**item, "retries": retries}


def _hold(item: dict, note: str) -> None:
    ap.update_item(item["id"], state="HELD", note=note)


def advance(item: dict, *, can_start_script: bool, can_start_render: bool) -> dict:
    """Do the next missing thing for one item. Returns which kinds of work it newly started."""
    took = {"script": False, "render": False}
    if item["state"] in ("DONE", "FAILED", "HELD", "CANCELLED"):
        return took
    if not item["script_id"]:
        if not can_start_script:
            return took
        script = store.create_script(item["column_key"], _prepare(item), created_by="autopilot")
        ap.update_item(item["id"], script_id=script["id"], state="WORKING", note="已开始写稿")
        video_workflow.spawn(script["id"], "autopilot")
        took["script"] = True
        return took
    script = store.get_script(item["script_id"])
    if script is None:
        _fail(item, "文稿记录丢失")
        return took
    status = script["status"]
    column = video_columns.get(get_settings().paths.config_dir, script["column_key"])
    if status in ("RESEARCHING", "WRITING"):
        return took
    if status == "FAILED":
        if _retries(item, "script") >= MAX_RETRIES:
            _hold(item, f"写稿失败 {MAX_RETRIES + 1} 次：{script.get('error')}")
            return took
        if not can_start_script:
            return took
        _bump(item, "script")
        store.update_script(script["id"], status="RESEARCHING", error=None)
        video_workflow.spawn(script["id"], "autopilot")
        ap.update_item(item["id"], note="写稿失败，从断点继续")
        took["script"] = True
        return took
    if status == "DRAFT":
        if script.get("stage"):  # audits running
            return took
        verdict = judge(script, column)
        if not verdict["audited"] and _retries(item, "audit") < MAX_RETRIES and can_start_script:
            _bump(item, "audit")
            video_workflow.spawn(script["id"], "autopilot")  # current_version set → only the audits rerun
            ap.update_item(item["id"], note="审计没有跑完，重跑审计")
            took["script"] = True
            return took
        if not verdict["audited"] and _retries(item, "audit") < MAX_RETRIES:
            return took  # wait for a free slot to rerun the audit
        # whatever the label, the video is made: it is shown next to the video, and the user decides what to publish
        store.update_script(script["id"], status="APPROVED", approved_version=verdict["version"])
        ap.update_item(item["id"], gate={**verdict, "approved": "auto"}, note=f"已自动定稿（{verdict['text']}），准备出片")
        return took
    if status == "APPROVED":
        return _render_step(item, script, can_start_render, took)
    return took


def _prepare(item: dict) -> dict:
    topic = dict(item["topic"])
    column = video_columns.get(get_settings().paths.config_dir, item["column_key"])
    fields = topic.setdefault("fields", {})
    if column.audience and not (fields.get("目标受众") or "").strip():
        fields["目标受众"] = column.audience
    return topic


def _fail(item: dict, note: str) -> dict:
    ap.update_item(item["id"], state="FAILED", note=note)
    return item


def _render_step(item: dict, script: dict, can_start_render: bool, took: dict) -> dict:
    renders = store.list_renders(script["id"])
    current = [r for r in renders if r["version"] == script["approved_version"]]
    active = [r for r in current if r["status"] in store.ACTIVE_RENDER_STATUSES]
    done = [r for r in current if r["status"] == "COMPLETED"]
    if done:
        ap.update_item(item["id"], state="DONE", render_id=done[0]["id"], note="成片完成，等你看片和发布")
        return took
    if active:
        ap.update_item(item["id"], render_id=active[0]["id"], note=f"出片中（{active[0]['status']}）")
        video_render.start(active[0]["id"])  # no-op when this process already follows it
        return took
    failed = [r for r in current if r["status"] in ("FAILED", "CANCELLED")]
    if len(failed) > RENDER_RETRIES:
        _hold(item, f"出片失败 {len(failed)} 次：{failed[0].get('error_message')}")
        return took
    if not can_start_render:
        return took
    try:
        render = video_render.create(script)
    except video_render.RenderError as exc:
        _hold(item, f"不能出片：{exc}")
        return took
    video_render.start(render["id"])
    ap.update_item(item["id"], render_id=render["id"], note="已提交出片")
    took["render"] = True
    return took


# --- the tick --------------------------------------------------------------

def tick() -> int:
    """Fire due plans, then advance every running batch one step. Safe to call from any process, any time."""
    try:
        with file_lock(os.path.join(get_settings().paths.state_dir, "autopilot-tick"), timeout=0):
            fire_due_plans()
            reopen_approved()
            runs = ap.list_runs(status="RUNNING")
            if runs:  # a batch in flight counts as use of the console: the launcher's idle watchdog must not close it
                from core import activity

                activity.use_state_dir(get_settings().paths.state_dir)
                activity.mark_activity()
            return sum(_tick_run(run) for run in runs)
    except TimeoutError:
        return 0


def reopen_approved() -> int:
    """A held item whose script the user has since approved goes on to its render, in its batch.

    Items an earlier version of the pipeline held at its quality gate (there is none now) are let through too."""
    reopened = 0
    for item in ap.held_items():
        script = store.get_script(item["script_id"]) if item["script_id"] else None
        old_gate = script and script["status"] == "DRAFT" and not script.get("stage") and item["note"].startswith("闸门没通过")
        if old_gate:
            ap.update_item(item["id"], state="WORKING", note="不再设闸门，继续")
            ap.update_run(item["run_id"], status="RUNNING")
            reopened += 1
            continue
        if script and script["status"] == "APPROVED" and not any(
                r["status"] in ("COMPLETED", *store.ACTIVE_RENDER_STATUSES) for r in store.list_renders(script["id"])):
            ap.update_item(item["id"], state="WORKING", note="你已定稿，继续出片")
            ap.update_run(item["run_id"], status="RUNNING")
            reopened += 1
    return reopened


def _tick_run(run: dict) -> int:
    spec = run["spec"]
    if run["stage"] == "TOPICS":
        if not fresh_topics(spec, run):
            return 0
        items = pick_topics(spec)
        if not items:
            ap.update_run(run["id"], status="DONE", note="没有可写的新选题")
            return 0
        _add_items(run["id"], items)
        ap.update_run(run["id"], stage="ITEMS")
        run = ap.get_run(run["id"])
    scripts = renders = 0
    for item in run["items"]:  # work already in flight, to respect the limits
        if item["script_id"]:
            s = store.get_script(item["script_id"])
            scripts += bool(s and (s["status"] in ("RESEARCHING", "WRITING") or (s["status"] == "DRAFT" and s.get("stage"))))
            renders += any(r["status"] in store.ACTIVE_RENDER_STATUSES for r in store.list_renders(item["script_id"]))
    for item in run["items"]:
        took = advance(item, can_start_script=scripts < MAX_SCRIPTS, can_start_render=renders < MAX_RENDERS)
        scripts += took["script"]
        renders += took["render"]
    run = ap.get_run(run["id"])
    if run["items"] and all(i["state"] in ("DONE", "FAILED", "HELD", "CANCELLED") for i in run["items"]):
        ap.update_run(run["id"], status="DONE", note=_summary(run))
    return 1


def _add_items(run_id: str, items: list[dict]) -> None:
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    with store.connect(write=True) as conn:
        for i, item in enumerate(items):
            conn.execute("INSERT INTO autopilot_items (id, run_id, slot, column_key, topic, updated_at)"
                         " VALUES (?, ?, ?, ?, ?, ?)",
                         (uuid.uuid4().hex, run_id, i, item["column_key"], json.dumps(item["topic"], ensure_ascii=False), now))


def _summary(run: dict) -> str:
    counts: dict[str, int] = {}
    for i in run["items"]:
        counts[i["state"]] = counts.get(i["state"], 0) + 1
    return "，".join(f"{k} {v}" for k, v in sorted(counts.items()))


# --- plans -----------------------------------------------------------------

def start_run(spec: Optional[dict] = None, *, plan_id: Optional[str] = None, started_by: str = "") -> dict:
    """A batch from ``spec`` (the default one when none is given). Items are picked on the first tick."""
    spec = spec or DEFAULT_SPEC
    for pick in spec.get("picks", []):
        pick = resolve_pick(pick)
        video_columns.for_new_script(get_settings().paths.config_dir, pick["column"])  # unknown or blocked raises
    return ap.create_run(spec, [], plan_id=plan_id, started_by=started_by)


def save_plan(name: str, expression: str, spec: Optional[dict] = None, *, created_by: str = "") -> dict:
    from core.schedule_store import slot
    next_fire = slot(expression, time.time(), "local")
    return ap.save_plan(name, expression, spec or DEFAULT_SPEC, next_fire=next_fire, created_by=created_by)


def fire_due_plans(now: Optional[float] = None) -> int:
    from core.schedule_store import slot
    now = time.time() if now is None else now
    fired = 0
    for plan in ap.list_plans():
        if not plan["enabled"] or plan["next_fire"] is None or plan["next_fire"] > now:
            continue
        # a missed slot (the host was off) fires once, late — but not if it is more than a day old
        nxt = slot(plan["expression"], now, "local")
        if ap.claim_plan(plan["id"], plan["next_fire"], nxt):
            if now - plan["next_fire"] < 24 * 3600:
                start_run(plan["spec"], plan_id=plan["id"], started_by="autopilot")
                fired += 1
    return fired

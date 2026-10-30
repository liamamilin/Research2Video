"""The unattended pipeline: topics → script → audit → approve → render, resuming where it stopped.

``tick()`` is idempotent. Each call reads the real state of every item's script and render and does
the next thing that is missing, so a restart or a crash never repeats finished work:

* no script yet → create it (its id is recorded before the work starts);
* script FAILED or its audits cut off → resume it (research is reused, a saved draft is not rewritten);
* draft with audits done → approve automatically, with a credibility label (core/video/credibility.py);
* approved → one render (a render row with an AutoVideo job id is only polled, never resubmitted);
* render completed → done. Nothing here publishes.

Nothing here waits for the user, except a draft the audits still call seriously wrong (有严重问题) or that would be
spoken wrongly (口播有问题): it is held, with the reason, until the user approves it. The label (有出处可信 … 有严重问题)
says how far the words can be trusted and is shown wherever a video is listed; otherwise an item is held only for a
technical failure (the script failed after its retries, the render failed after its retry).
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
from core.video import auto as video_auto
from core.video import autopilot_store as ap
from core.video import columns as video_columns
from core.video import content_columns, research_dossier
from core.video import excerpt
from core.video import store, story_pool
from core.video import topic_store
from web.services import video_executor, video_render, video_workflow  # noqa: F401 - tests patch the last two here
from web.settings import get_settings

logger = logging.getLogger("ai_research.web.video_autopilot")

#: Retries of a failed stage and of a failed render: the executor's (web/services/video_executor.py).
MAX_RETRIES = video_executor.MAX_RETRIES
RENDER_RETRIES = video_executor.RENDER_RETRIES
#: Scripts researching at once (each research uses search quota); renders go one at a time.
MAX_SCRIPTS = 2
MAX_RENDERS = 1
#: How long past a discovery job's own time limit (``runtime.timeout_seconds``) to keep waiting for it to end.
#: The job's engine cancels it at that limit, so the batch waits for the job, not for a clock of its own.
DISCOVERY_SLACK_SECONDS = 5 * 60
DISCOVERY_DEFAULT_TIMEOUT = 3600

DEFAULT_SPEC = {"picks": [{"column": "flash", "count": 3, "source": "flash"},
                          {"column": "point_short", "count": 2, "source": "flash"}],
                "refresh": True}


# --- picking ---------------------------------------------------------------

def sync_topics() -> list[dict]:
    """Read new or changed reports of every topic source into the topics table (core/video/topic_store.py)."""
    settings = get_settings()
    return topic_store.sync(settings.paths.jobs_dir, settings.paths.output_dir)


def resolve_pick(pick: dict) -> dict:
    """A pick by ``cell`` (the recipe's key) becomes ``column`` + ``source`` (the recipe's first topic source)."""
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


def known_source(name: str) -> bool:
    return name == topic_store.MANUAL or topic_store.resolve(get_settings().paths.jobs_dir, name) is not None


def coverage(*, cells: Optional[list[dict]] = None, source_rows: Optional[list[dict]] = None) -> list[dict]:
    """Per cell: its topic sources, how many topics wait to be written and how many are taken, newest report date."""
    from core.video import recipes as matrix

    settings = get_settings()
    if source_rows is None:
        source_rows = sync_topics()
    known = {topic_store.MANUAL} | {value for row in source_rows for value in (row["name"], row["job"])}
    covered = store.scripts_by_topic()
    dismissed = store.dismissed_topic_ids()
    cache: dict[str, tuple] = {}
    rows = []
    for cell in cells if cells is not None else matrix.catalog(settings.paths.config_dir):
        sources = cell.get("topic_sources") or []
        if not cell["usable"] or cell["key"] is None:
            continue
        if cell.get("whole_report") and not sources:  # written from a whole report (Job page), no items to pick here
            continue
        row = {"cell": cell["key"], "label": cell["label"], "status": cell["status"], "sources": sources,
               "open": 0, "written": 0, "dismissed": 0, "report_date": None,
               "unknown_sources": [s for s in sources if s not in known],
               "multi": bool(cell["column"] and cell["column"].get("topic_kind") == "roundup"),
               "autopilot": bool(cell["column"] and cell["column"].get("topic_kind", "single") == "single")}
        for source in sources:
            if source not in cache:
                cache[source] = topic_store.load(source)
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


class ReportVersionChanged(ValueError):
    """The completed input changed after this batch claimed it."""


def pick_topics(spec: dict) -> list[dict]:
    """Items for a batch: per pick, the best-scored topics that have no script, no publication and no item yet."""
    sync_topics()
    taken = store.topic_ids_in_use() | store.published_topic_ids() | ap.used_topic_ids() | store.dismissed_topic_ids()
    items: list[dict] = []
    from core.video import content_guard

    paths = get_settings().paths
    config_dir = paths.config_dir
    if spec.get("report_identity"):
        from core import report_identity

        if report_identity.version(spec["report"], video_workflow.workspace_dir()) != spec["report_identity"]:
            raise ReportVersionChanged("报告已变更，本批次保留原版本回执；新版本由自动流程另行领取")
    for pick in (resolve_pick(p) for p in spec.get("picks", [])):
        column = video_columns.get(config_dir, pick["column"])
        if pick.get("whole"):  # the Job's report as it is, one video: never a topic source
            item = _whole_item(spec, pick)
            if item is not None:
                items.append({**item, "pick": pick})
            continue
        source = topic_store.resolve(paths.jobs_dir, pick.get("source", "flash"))
        if spec.get("report"):
            if source is None:
                raise ValueError("批次指定的选题来源不存在")
            _, topics = topic_store.read_report(source, spec["report"], video_workflow.workspace_dir())
        else:
            _, topics = topic_store.load(pick.get("source", "flash"))
        job = source.job if source else ""
        candidates = [content_guard.policy_topic(excerpt.topic_for(column, topic), job)
                      for topic in sorted(topics, key=lambda t: (bool(getattr(t, "stale", lambda: False)()), -(t.score or 0)))]
        for candidate in candidates:
            if candidate.get("matrix_dedup"):
                candidate["matrix_dedup"]["policy"] = story_pool.POLICY
        skipped = content_guard.known_duplicate_ids(candidates, config_dir)
        candidates = [candidate for candidate in candidates if candidate["id"] not in skipped]
        # news that is already old ranks after fresher news (it is still picked when nothing fresher is left)
        for candidate in candidates:
            if len([i for i in items if i["pick"] is pick]) >= int(pick.get("count", 1)):
                break
            if candidate["id"] in taken:
                continue
            taken.add(candidate["id"])
            items.append({"column_key": pick["column"], "topic": candidate,
                          "production": pick.get("production", ""), "pick": pick})
        # A finite queue of alternatives lets semantic cross-day duplicates be
        # replaced before writing; candidates do not reserve videos on their own.
        for item in items:
            if item["pick"] is pick and story_pool.enabled(item["topic"]):
                item["topic"] = {**item["topic"], "_story_fallbacks": [dict(c) for c in candidates if c["id"] not in taken]}
    for item in items:
        del item["pick"]
    return items


def _whole_item(spec: dict, pick: dict) -> Optional[dict]:
    """The item for a whole-report pick: the report the batch was started for, unless it was written already."""
    from core.video import reports

    report = spec.get("report")
    if not report:
        return None
    workspace = video_workflow.workspace_dir()
    relative = reports.relative(report, workspace)
    if [s for s in reports.scripts_of(relative, pick["column"]) if not s.get("archived")]:
        return None  # written already (by hand, or by an earlier batch); one put away does not count: that is how a redo is asked for
    record = reports.record_file(relative, job=spec.get("job", ""), created_by="autopilot")
    topic = {**reports.topic_for(record, workspace), "from_report": relative, "report_id": record["id"],
             "matrix_dedup": {"job": spec.get("job") or record.get("job", ""), "policy": story_pool.POLICY}}
    return {"column_key": pick["column"], "topic": topic, "production": pick.get("production", "")}


#: A source job cut off (the app restarted) is started once more, no more: each start spends its searches again.
DISCOVERY_STARTS = 2


def refresh_sources(spec: dict) -> list[topic_store.Source]:
    """The sources a batch's picks read that let the pipeline start their job (``topics.refresh: true``)."""
    jobs_dir = get_settings().paths.jobs_dir
    out: dict[str, topic_store.Source] = {}
    for pick in spec.get("picks", []):
        source = topic_store.resolve(jobs_dir, resolve_pick(pick).get("source", ""))
        if source is not None and source.refresh and not source.error:
            out[source.name] = source
    return list(out.values())


def _has_today(source: topic_store.Source) -> bool:
    files = topic_store.report_files(source, os.path.dirname(os.path.abspath(get_settings().paths.output_dir)))
    return bool(files) and files[0][0] == time.strftime("%Y-%m-%d")


def fresh_topics(spec: dict, run: dict) -> bool:
    """True when the batch can start picking.

    A source whose newest report is not today's has its job started — once per batch. Starting a job cancels the one
    already running under the same name, so a start on every tick would restart it forever."""
    if not spec.get("refresh"):
        return True
    stale = [s for s in refresh_sources(spec) if not _has_today(s)]
    if not stale:
        return True
    labels = "、".join(f"「{s.label}」" for s in stale)
    started_at = run.get("discovery_at")
    if any(_discovery_running(s.job) for s in stale):
        if started_at is None:  # someone else started it (by hand): wait for that one
            ap.update_run(run["id"], discovery_at=time.time())
            return False
        if time.time() - started_at <= discovery_wait_seconds(stale):
            return False  # the job is still going and within its own time limit: wait for it to end
        ap.update_run(run["id"], note=f"今天的{labels}超过了它自己的时限还没结束，用最新的一期")
        return True
    if int(run.get("discovery_tries") or 0) < DISCOVERY_STARTS:
        ap.update_run(run["id"], discovery_at=time.time(), discovery_tries=int(run.get("discovery_tries") or 0) + 1)
        for source in stale:
            _start_discovery(source.job)
        return False
    ap.update_run(run["id"], note=f"今天的{labels}没有跑出报告，用最新的一期")
    return True


def discovery_wait_seconds(sources: list[topic_store.Source]) -> int:
    """How long a batch waits for these sources' jobs: the longest job time limit, plus a little."""
    from core.config import load_job

    jobs_dir = get_settings().paths.jobs_dir
    limits = []
    for source in sources:
        runtime = (load_job(jobs_dir, source.job) or {}).get("runtime") or {}
        try:
            limits.append(int(runtime.get("timeout_seconds") or DISCOVERY_DEFAULT_TIMEOUT))
        except (TypeError, ValueError):
            limits.append(DISCOVERY_DEFAULT_TIMEOUT)
    return max(limits or [DISCOVERY_DEFAULT_TIMEOUT]) + DISCOVERY_SLACK_SECONDS


_discovery_lock = threading.RLock()


def discovery_status(name: str = "flash") -> dict:
    """A refreshable source's job: running now, when its newest report came out, how many searches it last took."""
    from core.state import StateManager

    settings = get_settings()
    source = topic_store.resolve(settings.paths.jobs_dir, name)
    if source is None or not source.refresh:
        raise ValueError(f"选题来源 {name} 不能从这里生成（它的 Job 没写 topics.refresh: true）")
    files = topic_store.report_files(source, os.path.dirname(os.path.abspath(settings.paths.output_dir)))
    last = StateManager.get(source.job) or {}
    return {"source": source.name, "job": source.job, "label": source.label,
            "running": _discovery_running(source.job),
            "report_date": files[0][0] if files else None,
            "report_at": os.path.getmtime(files[0][1]) if files else None,
            "searches": (last.get("last_usage") or {}).get("searches")}


def start_discovery_if_idle(name: str = "flash") -> bool:
    """Start a refreshable source's job unless it is running — starting a job cancels the one running under its name."""
    status = discovery_status(name)
    with _discovery_lock:  # the check and the start are one step, so a double click starts it once
        if _discovery_running(status["job"]):
            return False
        _start_discovery(status["job"])
        return True


def _job_names(job: str) -> set[str]:
    """Every name a job may be running under: its path (the Jobs page), its file name (older batches)."""
    return {job, job.rsplit("/", 1)[-1]}


def _discovery_running(job: str) -> bool:
    """Running here under either name, or in another process (the scheduler, the command line) holding its lock."""
    from core.lock import LockManager
    from web.runner.registry import TaskRegistry

    registry = TaskRegistry()
    return any(registry.is_running(name) or LockManager.is_locked(name) for name in _job_names(job))


def _start_discovery(job: str) -> None:
    from web.runner.executor import start_job

    settings = get_settings()
    with _discovery_lock:
        try:
            start_job(job, "autopilot", config_dir=settings.paths.config_dir, jobs_dir=settings.paths.jobs_dir)
        except Exception as exc:  # noqa: BLE001 - the job cannot start; the next tick sees it is not running
            logger.info("topic source %s not started: %s", job, exc)


# --- advancing one item ----------------------------------------------------
# What a script does next is decided by the executor (web/services/video_executor.py), the same place the user's
# buttons go through; here is only the batch's bookkeeping: its items, their retries and notes.

judge = video_executor.judge


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
        from core.video import content_guard

        paths = get_settings().paths
        topic = content_guard.with_policy({"created_by": "autopilot", "topic": item["topic"]}, paths.jobs_dir)["topic"]
        if topic.get("id") in content_guard.known_duplicate_ids([topic], paths.config_dir):
            gate = dict(item.get("gate") or {})
            gate["known_duplicate_skips"] = list(dict.fromkeys([*gate.get("known_duplicate_skips", []), topic["id"]]))
            _replace_candidate(item, gate, "此候选已确认重复")
            return took
        if content_guard.writing_peer(topic, paths.config_dir, paths.jobs_dir):
            ap.update_item(item["id"], note="等待同矩阵前一项确定主事件，再从剩余候选选题")
            return took
        try:
            content_guard.check_topic(topic, paths.config_dir, paths.jobs_dir)
        except content_guard.DuplicateContent as exc:
            ap.update_item(item["id"], state="CANCELLED", note=str(exc))
            return took
        topic = _prepare({**item, "topic": topic})
        if item.get("production"):
            topic = {**topic, "_production": item["production"]}
        if topic.get('theme_auto'):
            from web.services import theme_workbench
            try:
                script = theme_workbench.create_queued({**item, 'topic': topic})
            except (ValueError, LookupError) as exc:
                ap.update_item(item['id'], state='CANCELLED', note=str(exc))
                return took
        else:
            script = video_executor.create(item["column_key"], topic, "autopilot")
        if item.get("production"):
            store.update_script(script["id"], production=item["production"])
        ap.update_item(item["id"], script_id=script["id"], state="WORKING", note="已开始写稿")
        took["script"] = True
        return took
    script = store.get_script(item["script_id"])
    if script is None:
        _fail(item, "文稿记录丢失")
        return took
    selection = story_pool.selection(script["id"])
    gate = {**(item.get("gate") or {}), **({"story_selection": selection} if selection else {})}
    if selection:
        ap.update_item(item["id"], gate=gate)
    if script.get("archived"):
        # Only our explicit exhausted selection can replace an item. A manual
        # archival is final for this pipeline and never revives that script.
        if selection and not selection["selected"] and script["topic"].get("content_archive"):
            _reselect(item, script, gate)
        else:
            ap.update_item(item["id"], state="CANCELLED", note="文稿已归档，不再继续")
        return took
    out = video_executor.advance_auto(script, item.get("retries") or {}, can_start_script=can_start_script,
                                      can_start_render=can_start_render)
    if out["item"] and "gate" in out["item"]:
        out["item"]["gate"] = {**gate, **out["item"]["gate"]}
    if out["bump"]:
        _bump(item, out["bump"])
    if out["hold"]:
        if out["item"]:
            ap.update_item(item["id"], **out["item"])
        _hold(item, out["hold"])
    elif out["item"]:
        ap.update_item(item["id"], **out["item"])
    if selection and script["status"] in ("WRITING", "RESEARCHING"):
        ap.update_item(item["id"], note=f"已选主事件：{selection['selected']}；跳过 {selection['skipped']} 个重复候选")
    return out["took"]


def _reselect(item: dict, script: dict, gate: dict) -> None:
    attempts = list((item.get("gate") or {}).get("story_attempts") or [])
    attempts.append({"script_id": script["id"], "title": script["topic"].get("title", ""),
                     "reason": script.get("error") or "主事件均已覆盖", "selection": gate["story_selection"]})
    _replace_candidate(item, {**gate, "story_attempts": attempts}, script.get('error') or '主事件均已覆盖')


def _replace_candidate(item: dict, gate: dict, reason: str) -> None:
    from core.video import content_guard

    alternatives = list(item["topic"].get("_story_fallbacks") or [])
    taken = store.topic_ids_in_use() | store.published_topic_ids() | ap.used_topic_ids() | store.dismissed_topic_ids()
    taken |= content_guard.known_duplicate_ids(alternatives, get_settings().paths.config_dir)
    for index, topic in enumerate(alternatives):
        if topic["id"] in taken:
            continue
        replacement = {**topic, "_story_fallbacks": alternatives[index+1:]}
        ap.update_item(item["id"], topic=replacement, script_id=None, state="PENDING", retries={},
                       gate={k: v for k, v in gate.items() if k != "story_selection"}, note=f"旧事件已覆盖，自动改选：{topic['title']}")
        return
    ap.update_item(item["id"], state="CANCELLED", gate=gate,
                   note=f"候选不足，排重后少做一条：{reason}")


def _prepare(item: dict) -> dict:
    topic = dict(item["topic"])
    config_dir = get_settings().paths.config_dir
    column = video_columns.get(config_dir, item["column_key"])
    job = research_dossier.PRODUCTIONS.get(item.get("production"), "")
    domain = job and not job.startswith(("ai_media/", "monitoring/"))
    business = content_columns.for_production(config_dir, item["production"]) if domain else None
    audience = (business.audience if business else "") or column.audience
    fields = topic.setdefault("fields", {})
    if audience and not (fields.get("目标受众") or "").strip():
        fields["目标受众"] = audience
    return topic


def _fail(item: dict, note: str) -> dict:
    ap.update_item(item["id"], state="FAILED", note=note)
    return item


# --- the tick --------------------------------------------------------------

def tick() -> int:
    """Fire due plans, then advance every running batch one step. Safe to call from any process, any time."""
    try:
        with file_lock(os.path.join(get_settings().paths.state_dir, "autopilot-tick"), timeout=0):
            story_pool.reconcile()
            fire_due_plans()
            fire_auto()
            from web.services import theme_autopilot
            theme_autopilot.fire()
            reopen_approved()
            runs = sorted(ap.list_runs(limit=None, status="RUNNING"), key=lambda r: r["started_at"])  # oldest first: see below
            if runs:  # a batch in flight counts as use of the console: the launcher's idle watchdog must not close it
                from core import activity

                activity.use_state_dir(get_settings().paths.state_dir)
                activity.mark_activity()
            # The limits hold across batches (each Job's report is a batch of its own), so who asks first gets the free
            # slot. Oldest first: a script that failed or waits for its audit is retried before any newer batch starts,
            # instead of starving behind them for as long as new batches keep coming.
            counts = _in_flight(runs)
            return sum(_tick_run(run, counts) for run in runs)
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


def retry_held_renders() -> int:
    """Items held for failed renders go on, their earlier failures no longer counted: for when what broke them is fixed
    (an account's balance, a key, the model the renders use). Only items whose script is approved and not archived (an
    archived one was put away on purpose: it would be rendered again behind the user's back). Returns how many."""
    reopened = 0
    for item in ap.held_items(limit=500):
        script = store.get_script(item["script_id"]) if item["script_id"] else None
        if not (script and script["status"] == "APPROVED" and not script.get("archived")
                and item["note"].startswith("出片失败")):
            continue
        cutoff = max((r["created_at"] for r in store.list_renders(script["id"]) if r["status"] in ("FAILED", "CANCELLED")),
                     default="")  # the failures there are now; a render made after this one counts again
        ap.update_item(item["id"], state="WORKING", note="重新出片",
                       retries={**(item.get("retries") or {}), "renders_forgiven_before": cutoff})
        ap.update_run(item["run_id"], status="RUNNING")
        reopened += 1
    return reopened


def _in_flight(runs: list[dict]) -> dict:
    """Scripts being written and renders being made right now, over every one of these batches' items."""
    counts = {"scripts": 0, "renders": 0}
    for run in runs:
        for item in run.get("items") or []:
            if item["script_id"]:
                s = store.get_script(item["script_id"])
                counts["scripts"] += bool(s and (s["status"] in ("RESEARCHING", "WRITING")
                                                 or (s["status"] == "DRAFT" and s.get("stage"))))
                counts["renders"] += any(r["status"] in store.ACTIVE_RENDER_STATUSES for r in store.list_renders(item["script_id"]))
    return counts


def _tick_run(run: dict, counts: Optional[dict] = None) -> int:
    spec = run["spec"]
    if run["stage"] == "TOPICS":
        if not fresh_topics(spec, run):
            return 0
        try:
            items = pick_topics(spec)
        except ReportVersionChanged as exc:
            ap.update_run(run["id"], status="DONE", note=str(exc))
            return 0
        if not items:
            # keep why the topics were not fresh: that, not the empty pick, is usually what went wrong
            cause = (ap.get_run(run["id"]) or {}).get("note") or ""
            ap.update_run(run["id"], status="DONE", note="；".join(filter(None, [cause, "没有可写的新选题"])))
            return 0
        _add_items(run["id"], items)
        ap.update_run(run["id"], stage="ITEMS")
        run = ap.get_run(run["id"])
    if counts is None:  # a batch ticked on its own: the work in flight is its own and every other running batch's
        counts = _in_flight(ap.list_runs(limit=None, status="RUNNING"))
    for item in run["items"]:
        took = advance(item, can_start_script=counts["scripts"] < MAX_SCRIPTS, can_start_render=counts["renders"] < MAX_RENDERS)
        counts["scripts"] += took["script"]
        counts["renders"] += took["render"]
    run = ap.get_run(run["id"])
    if run["items"] and all(i["state"] in ("DONE", "FAILED", "HELD", "CANCELLED") for i in run["items"]):
        ap.update_run(run["id"], status="DONE", note=_summary(run))
    return 1


def _add_items(run_id: str, items: list[dict]) -> None:
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    with store.connect(write=True) as conn:
        for i, item in enumerate(items):
            conn.execute("INSERT INTO autopilot_items (id, run_id, slot, column_key, topic, production, updated_at)"
                         " VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (uuid.uuid4().hex, run_id, i, item["column_key"], json.dumps(item["topic"], ensure_ascii=False),
                          item.get("production", ""), now))


def _summary(run: dict) -> str:
    counts: dict[str, int] = {}
    for i in run["items"]:
        counts[i["state"]] = counts.get(i["state"], 0) + 1
    current = [(i.get("gate") or {}).get("story_selection") for i in run["items"]]
    current = [s for s in current if s]
    selections = current + [a["selection"] for i in run["items"] for a in (i.get("gate") or {}).get("story_attempts", [])]
    if not selections:
        return "，".join(f"{k} {v}" for k, v in sorted(counts.items()))
    pools = {s["pool_id"]: s["candidates"] for s in selections}
    skipped = {(s["pool_id"], d["title"]) for s in selections for d in s["decisions"] if d["decision"] == "skip"}
    replaced = sum(len((i.get("gate") or {}).get("story_attempts") or []) for i in run["items"])
    shortages = sum(not s["selected"] for s in current)
    return (f"候选 {sum(pools.values())}，采用 {sum(bool(s['selected']) for s in current)}，"
            f"重复跳过 {len(skipped)}，改选 {replaced}，素材不足 {shortages}；" +
            "，".join(f"{k} {v}" for k, v in sorted(counts.items())))


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
            late = now - plan["next_fire"]
            if late < 24 * 3600:
                start_run(plan["spec"], plan_id=plan["id"], started_by="autopilot")
                fired += 1
            else:  # recorded, so a skipped week shows on the board instead of passing unseen
                ap.finished_run(plan["spec"], f"错过了 {time.strftime('%m-%d %H:%M', time.localtime(plan['next_fire']))}"
                                f" 这一批（晚了 {late / 3600:.0f} 小时，超过一天不补跑）",
                                plan_id=plan["id"], started_by="autopilot")
    return fired


def fire_auto(now: Optional[float] = None) -> int:
    """A batch for each Job that has a new report its auto configs have not written from (core/video/auto.py).

    The Job's own schedule produced the report, so the batch does not start the Job (``refresh`` off); its picks are the
    Job's auto configs, one batch so the limits and the topics already taken hold across them."""
    paths = get_settings().paths
    root = os.path.dirname(os.path.abspath(paths.output_dir))
    fired = 0
    for found in video_auto.pending(paths.config_dir, paths.jobs_dir, paths.state_dir, root,
                                    time.time() if now is None else now):
        try:
            start_run({**found, "refresh": False},
                      started_by="autopilot")
            fired += 1
        except Exception:  # noqa: BLE001 - a config that cannot start must not stop the others or the tick
            logger.exception("auto batch for %s not started", found["job"])
            ap.finished_run({**found, "refresh": False},
                            "自动写稿没能开始，看日志", started_by="autopilot")  # also marks the report handled: no retry loop
    return fired

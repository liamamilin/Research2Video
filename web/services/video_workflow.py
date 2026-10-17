"""Run a video script from topic to draft: research job, then script writing.

The research step is an ordinary job (``jobs/video/<column>_<id>.yaml``), run
through the same runner as every other job, so its log, state, report index
and citation check are all the usual ones and it shows on the Jobs page.
Writing is either one model call over the finished report (``writer: legacy``,
core/video/writer.py) or the five-step adaptation (``writer: adapt``,
core/video/adapt/). Restyling an existing script always adapts, reusing the
fact sheet made for its report.

Runs in its own thread: the research takes minutes, and the job runner's
pool must stay free for the job itself.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Optional

from core import report_meta
from core.config import load_system_config
from core.llm import LLMConfig
from core.state import StateManager
from core.video import columns as video_columns
from core.video import length, store, writer
from core.video.adapt import draft as adapt_draft
from core.video.adapt import facts as adapt_facts
from core.video.adapt import pipeline as adapt_pipeline
from core.video.adapt import review as adapt_review
from core.video.adapt import styles as video_styles
from core.video.adapt.common import AdaptError
from web.settings import get_settings

logger = logging.getLogger("ai_research.web.video")

JOB_CATEGORY = "video"
#: How long to wait for the research job beyond its own timeout.
WAIT_SLACK_SECONDS = 300
_active: set[str] = set()


class RestyleError(RuntimeError):
    """This script cannot be restyled now; the message says why."""


def workspace_dir() -> str:
    return os.path.dirname(os.path.abspath(get_settings().paths.config_dir))


def _read_report(report_path: str) -> str:
    full_path = report_path if os.path.isabs(report_path) else os.path.join(workspace_dir(), report_path)
    with open(full_path, encoding="utf-8") as fh:
        return fh.read()


def research_job_name(column_key: str, script_id: str) -> str:
    return f"{JOB_CATEGORY}/{column_key}_{script_id[:8]}"


def job_spec(column: video_columns.Column, topic: dict, script_id: str) -> dict:
    fields = topic.get("fields") or {}
    fill = {
        "{topic_minutes}": str(column.target_minutes),
        "{topic_title}": topic.get("title", ""),
        "{topic_angle}": fields.get("核心角度", "") or "（未提供）",
        "{topic_audience}": fields.get("目标受众", "") or "（未提供）",
        "{topic_why}": fields.get("为什么有人在乎", "") or "（未提供）",
        "{topic_outline}": fields.get("建议大纲", "") or "（未提供）",
    }
    prompt = column.research_prompt
    for key, value in fill.items():
        prompt = prompt.replace(key, value)
    name = research_job_name(column.key, script_id).split("/", 1)[1]
    keywords = [k for k in re.split(r"[：:，,、]", topic.get("title", "")) if k.strip()][:5]
    return {
        "name": name,
        "description": f"{column.name}：{topic.get('title', '')}"[:200],
        "enabled": True,
        "keywords": keywords or [topic.get("title", "")],
        "language": "zh",
        "schedule": {"type": "manual"},
        "runtime": {"timeout_seconds": column.research_timeout_seconds, "skip_if_running": True},
        "prompt": prompt,
        "output": f"output/{JOB_CATEGORY}/{{datetime}}_{name}.md",
    }


def start(column_key: str, topic: dict, started_by: str) -> dict:
    """Create a script for ``topic`` and start researching it in the background."""
    settings = get_settings()
    column = video_columns.get(settings.paths.config_dir, column_key)
    script = store.create_script(column.key, topic, created_by=started_by)
    thread = threading.Thread(
        target=run, args=(script["id"],), kwargs={"started_by": started_by},
        name=f"video-script-{script['id'][:8]}", daemon=True,
    )
    thread.start()
    return script


def run(script_id: str, *, started_by: str = "", llm_config: Optional[LLMConfig] = None) -> str:
    """Research then write. Returns the final status (DRAFT or FAILED)."""
    if script_id in _active:
        return "RUNNING"
    _active.add(script_id)
    try:
        with store.beating(store.touch_script, script_id):
            return _run(script_id, started_by, llm_config)
    except Exception as exc:  # noqa: BLE001 - a crash must not leave it in flight
        logger.exception("video script %s crashed", script_id)
        store.update_script(script_id, status="FAILED", error=f"{type(exc).__name__}: {exc}")
        return "FAILED"
    finally:
        _active.discard(script_id)


def _run(script_id: str, started_by: str, llm_config: Optional[LLMConfig]) -> str:
    from web.runner.executor import start_job
    from web.services import yaml_io

    settings = get_settings()
    script = store.get_script(script_id)
    column = video_columns.get(settings.paths.config_dir, script["column_key"])
    topic = script["topic"]

    # 1. Research: an ordinary job.
    job_name = research_job_name(column.key, script_id)
    job_path = os.path.join(settings.paths.jobs_dir, f"{job_name}.yaml")
    os.makedirs(os.path.dirname(job_path), exist_ok=True)
    if not os.path.isfile(job_path):
        yaml_io.atomic_create(job_path, yaml_io.dump_yaml(job_spec(column, topic, script_id)))
    store.update_script(script_id, status="RESEARCHING", research_job=job_name, error=None)

    task = start_job(job_name, started_by or "video",
                     config_dir=settings.paths.config_dir, jobs_dir=settings.paths.jobs_dir)
    task.future.result(timeout=column.research_timeout_seconds + WAIT_SLACK_SECONDS)
    if task.status != "success":
        state = StateManager.get(job_name) or {}
        store.update_script(script_id, status="FAILED",
                            error=f"研究没有完成（{task.status}）：{state.get('last_error') or '见 Jobs 页日志'}")
        return "FAILED"

    report_path = (StateManager.get(job_name) or {}).get("last_output") or ""
    try:
        report = _read_report(report_path)
    except OSError as exc:
        store.update_script(script_id, status="FAILED", error=f"读不到研究报告 {report_path}：{exc}")
        return "FAILED"
    citation_check = (report_meta.get(report_path) or {}).get("citation_check")

    # 2. Write.
    # error=None: a stale note (e.g. a false "interrupted") must not outlive the work.
    store.update_script(script_id, status="WRITING", report_path=report_path, error=None)
    config, review_config = _llm_configs(llm_config)
    if column.writer == "adapt":
        try:
            style = video_styles.resolve(settings.paths.config_dir, column)
            _adapt_version(script_id, report=report, report_path=report_path, citation_check=citation_check,
                           column=column, topic=topic, style=style, started_by=started_by, llm_config=config,
                           review_llm_config=review_config)
        except (AdaptError, writer.ScriptError, video_styles.StyleError) as exc:
            store.update_script(script_id, status="FAILED", stage=None, error=f"改编失败：{exc}")
            return "FAILED"
        store.update_script(script_id, status="DRAFT", stage=None, error=None)
        return "DRAFT"
    try:
        written = writer.write(
            report=report,
            citation_check=citation_check,
            topic=topic,
            column_style=column.script_style,
            target_minutes=column.target_minutes,
            llm_config=config,
        )
    except writer.ScriptError as exc:
        store.update_script(script_id, status="FAILED", error=f"写稿失败：{exc}")
        return "FAILED"

    store.add_version(
        script_id,
        title=written.title,
        sections=written.sections,
        length_check=written.length_check,
        edit_type="GENERATED",
        meta={**written.meta, "report_path": report_path, "written_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")},
        created_by=started_by,
    )
    store.update_script(script_id, status="DRAFT", error=None)
    return "DRAFT"


def _llm_configs(llm_config: Optional[LLMConfig]) -> tuple:
    """(writing model, audit model or None). The audits use ``review_ai`` in system.yaml when it is set."""
    if llm_config is not None:
        return llm_config, None
    system = load_system_config(get_settings().paths.config_dir)
    review = LLMConfig.from_system(system, "review_ai") if system.get("review_ai") else None
    return LLMConfig.from_system(system, "ai"), review


def _adapt_version(script_id: str, *, report: str, report_path: str, citation_check: Optional[dict], column,
                   topic: dict, style, started_by: str, llm_config, review_llm_config=None) -> int:
    """Adapt the report in ``style`` and store the result as a new GENERATED version."""
    sheet_id = adapt_facts.sheet_id(report)
    cached = store.get_fact_sheet(sheet_id)
    adapted, _ = adapt_pipeline.adapt(
        report=report,
        citation_check=citation_check,
        topic=topic,
        column=column,
        style=style,
        llm_config=llm_config,
        review_llm_config=review_llm_config,
        sheet=cached["sheet"] if cached else None,
        on_sheet=lambda sheet: store.save_fact_sheet(sheet_id, report_path=report_path, sheet=sheet),
        on_stage=lambda name: store.update_script(script_id, stage=name),
    )
    return store.add_version(
        script_id,
        title=adapted.title,
        sections=adapted.sections,
        length_check=adapted.length_check,
        edit_type="GENERATED",
        meta={**adapted.meta, "report_path": report_path, "written_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")},
        created_by=started_by,
    )


def restyle(script_id: str, *, depth: Optional[str] = None, tone: Optional[str] = None,
            started_by: str = "") -> dict:
    """Write a new version of a script in another style, in the background."""
    settings = get_settings()
    script = store.get_script(script_id)
    if script["status"] not in ("DRAFT", "APPROVED") or script_id in _active:
        raise RestyleError(f"文稿当前是 {script['status']}，不能换风格")
    if not script["report_path"]:
        raise RestyleError("这篇文稿没有研究报告，不能换风格")
    try:
        _read_report(script["report_path"])
    except OSError as exc:
        raise RestyleError(f"读不到研究报告 {script['report_path']}") from exc
    column = video_columns.get(settings.paths.config_dir, script["column_key"])
    style = video_styles.resolve(settings.paths.config_dir, column, depth, tone)  # StyleError → 422
    store.update_script(script_id, status="WRITING", stage="排队", error=None, heartbeat_at=time.time())
    thread = threading.Thread(
        target=run_restyle, args=(script_id, style.depth.key, style.tone.key),
        kwargs={"started_by": started_by, "previous_status": script["status"]},
        name=f"video-restyle-{script_id[:8]}", daemon=True,
    )
    thread.start()
    return store.get_script(script_id)


def run_restyle(script_id: str, depth: str, tone: str, *, started_by: str = "",
                previous_status: str = "DRAFT", llm_config: Optional[LLMConfig] = None) -> str:
    """Adapt the script's report in the given style. Returns the final status."""
    if script_id in _active:
        return "RUNNING"
    _active.add(script_id)
    try:
        with store.beating(store.touch_script, script_id):
            settings = get_settings()
            script = store.get_script(script_id)
            column = video_columns.get(settings.paths.config_dir, script["column_key"])
            style = video_styles.resolve(settings.paths.config_dir, column, depth, tone)
            report = _read_report(script["report_path"])
            citation_check = (report_meta.get(script["report_path"]) or {}).get("citation_check")
            config, review_config = _llm_configs(llm_config)
            _adapt_version(script_id, report=report, report_path=script["report_path"],
                           citation_check=citation_check, column=column, topic=script["topic"], style=style,
                           started_by=started_by, llm_config=config, review_llm_config=review_config)
        store.update_script(script_id, status="DRAFT", stage=None, error=None)
        return "DRAFT"
    except Exception as exc:  # noqa: BLE001 - the earlier versions must stay usable
        logger.exception("restyling video script %s failed", script_id)
        store.update_script(script_id, status=previous_status, stage=None,
                            error=f"换风格失败：{type(exc).__name__}: {exc}")
        return previous_status
    finally:
        _active.discard(script_id)


def _base_adaptation(script: dict, version: Optional[int] = None) -> tuple[dict, Optional[dict]]:
    """(the edited version's meta, its fact sheet if it was adapted). The current version by default."""
    number = version or script["current_version"]
    base = store.get_version(script["id"], number) if number else None
    meta = (base or {}).get("meta") or {}
    row = store.get_fact_sheet(meta["fact_sheet_id"]) if meta.get("fact_sheet_id") else None
    return meta, (row["sheet"] if row else None)


def _style_of(meta: dict, column):
    style = meta.get("style") or {}
    if not style:
        return None
    try:
        return video_styles.resolve(get_settings().paths.config_dir, column, style.get("depth"), style.get("tone"))
    except video_styles.StyleError:
        return None


def _rules_only(issues: list[dict], plan: Optional[dict], sections: list[dict], **extra) -> dict:
    return {**adapt_review.assemble(issues, None, None, plan, sections),
            "rounds": 0, "deletions": [], "initial": [], **extra}


def rule_checks(version: dict, column) -> dict:
    """Checks for a version: the stored ones (older shapes converted), or the rule checks alone."""
    meta = version.get("meta") or {}
    if meta.get("checks"):
        return adapt_review.normalize(meta["checks"], len(version["sections"]))
    issues = adapt_review.rules(version["sections"], column=column, target_minutes=column.target_minutes)
    return _rules_only(issues, None, version["sections"])


def save_edit(script_id: str, *, title: str, sections: list[dict], edited_by: str,
              base_version: Optional[int] = None) -> int:
    """Store an edited version: re-measure length and re-run the rule checks.

    An edit of an adapted version keeps its facts, plan and style, so its
    citations are recomputed from the facts each paragraph names. The model
    checks are not re-run on save (they cost a call); the version says so.
    """
    script = store.get_script(script_id)
    column = video_columns.get(get_settings().paths.config_dir, script["column_key"])
    base_meta, sheet = _base_adaptation(script, base_version)
    plan = base_meta.get("plan") if sheet else None

    clean = [
        {
            "title": str(s.get("title") or "").strip(),
            "paragraphs": [
                {
                    "text": str(p.get("text") or "").strip(),
                    "citations": [str(u) for u in p.get("citations") or []],
                    "evidence": p.get("evidence") or ("CITED" if p.get("citations") else "UNCITED"),
                    "note": p.get("note"),
                    "facts": [str(f) for f in p.get("facts") or []],
                    "introduces": [str(t) for t in p.get("introduces") or []],
                    "recap": bool(p.get("recap")),
                }
                for p in s.get("paragraphs") or []
                if str(p.get("text") or "").strip()
            ],
        }
        for s in sections
    ]
    clean = [s for s in clean if s["paragraphs"]]
    if sheet:
        finalized = adapt_draft.finalize(clean, sheet)
        for given, done in zip(clean, finalized, strict=True):
            for before, after in zip(given["paragraphs"], done["paragraphs"], strict=True):
                after["note"] = before["note"] or after["note"]  # a reviewer's note outlives the edit
        clean = finalized
    else:
        for section in clean:
            for para in section["paragraphs"]:
                for key in ("facts", "introduces", "recap"):
                    para.pop(key)

    style = _style_of(base_meta, column) if sheet else None
    issues = adapt_review.rules(clean, column=column, target_minutes=column.target_minutes,
                                plan=plan, sheet=sheet, style=style)
    meta: dict = {
        "problems": writer.lint(clean),
        "checks": _rules_only(issues, plan, clean, model_checks_stale=True),
    }
    budgets = None
    if sheet:
        meta.update({k: base_meta.get(k) for k in ("writer", "style", "prompts", "fact_sheet_id", "plan",
                                                   "description")})
        if plan and len(plan.get("beats") or []) == len(clean):
            budgets = [b["budget"] for b in plan["beats"]]
    version = store.add_version(
        script_id,
        title=title.strip(),
        sections=clean,
        length_check=length.measure(clean, column.target_minutes, section_budgets=budgets),
        edit_type="EDITED",
        meta=meta,
        created_by=edited_by,
    )
    return version

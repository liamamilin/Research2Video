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

import copy
import logging
import os
from dataclasses import is_dataclass, replace
import re
import threading
import time
from typing import Optional

from core import report_material, report_meta
from core.config import load_system_config
from core.llm import LLMConfig
from core.state import StateManager
from core.video import columns as video_columns
from core.video import content_guard, length, research_dossier, store, story_pool, writer
from core.video import plans as video_plans
from core.video.adapt import draft as adapt_draft
from core.video.adapt import facts as adapt_facts
from core.video.adapt import pipeline as adapt_pipeline
from core.video.adapt import review as adapt_review
from core.video.adapt import styles as video_styles
from core.video.adapt.common import AdaptError
from web.settings import get_settings

logger = logging.getLogger("ai_research.web.video")

JOB_CATEGORY = "video"
#: Per attempt and per call limits for the audits of a script written in one call (see ``_audit_short``).
AUDIT_REQUEST_SECONDS = 120
AUDIT_TIMEOUT_SECONDS = 240
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


def report_of(script: dict, report_path: str = "") -> str:
    """The text a script is written and checked against: its report, or for an item written without research that
    item's part of it (core/video/excerpt.py). Raises OSError when the report or the item is not there."""
    if script.get("topic", {}).get("article"):
        from web.services.video_articles import source_of
        return source_of(script)["text"]
    from core.video import excerpt

    text = writer.report_body(_read_report(report_path or script["report_path"]))
    topic = script.get("topic") or {}
    if not topic.get("from_item"):
        return text
    part = excerpt.item_excerpt(text, topic.get("from_item_title") or topic.get("title") or "",
                                summary=(topic.get("fields") or {}).get("一句话") or "",
                                include_nested=research_dossier.enabled(script))
    if part is None:
        raise OSError(f"报告里找不到条目「{topic.get('title')}」，没法只用它那一节写稿")
    return part


def research_job_name(column_key: str, script_id: str) -> str:
    return f"{JOB_CATEGORY}/{column_key}_{script_id[:8]}"


#: The research prompt's older placeholder names and the topic field each one stands for.
TOPIC_ALIASES = {
    "topic_angle": "核心角度", "topic_audience": "目标受众", "topic_why": "为什么有人在乎",
    "topic_outline": "建议大纲", "topic_change": "变化", "topic_date": "发布日期", "topic_sources": "来源网址",
    "topic_subjects": "对比对象", "topic_dimensions": "对比维度",
}
MISSING = "（未提供）"


def fill_topic(text: str, column: video_columns.Column, topic: dict) -> str:
    """``text`` with the topic in its placeholders: ``{topic.标题}`` / ``{topic.<field>}`` for any topic field,
    ``{topic_minutes}``, and the older names in ``TOPIC_ALIASES``. A field the topic lacks reads （未提供）."""
    fields = topic.get("fields") or {}

    def value(name: str) -> str:
        if name in ("title", "标题"):
            return topic.get("title", "")
        return str(fields.get(name, "") or "") or MISSING

    text = text.replace("{topic_minutes}", str(column.target_minutes)).replace("{topic_title}", topic.get("title", ""))
    for alias, name in TOPIC_ALIASES.items():
        text = text.replace("{" + alias + "}", value(name))
    return re.sub(r"\{topic\.([^{}\s]+)\}", lambda m: value(m.group(1)), text)


def job_spec(column: video_columns.Column, topic: dict, script_id: str) -> dict:
    prompt = fill_topic(column.research_prompt, column, topic)
    name = research_job_name(column.key, script_id).split("/", 1)[1]
    keywords = [k for k in re.split(r"[：:，,、]", topic.get("title", "")) if k.strip()][:5]
    spec = {
        "name": name,
        "description": f"{column.name}：{topic.get('title', '')}"[:200],
        "enabled": True,
        "keywords": keywords or [topic.get("title", "")],
        "language": column.research_language,
        "schedule": {"type": "manual"},
        "runtime": {"timeout_seconds": column.research_timeout_seconds, "skip_if_running": True},
        "prompt": prompt,
        "output": f"output/{JOB_CATEGORY}/{{datetime}}_{name}.md",
    }
    if topic.get("theme_research"):
        spec.update(topic["theme_research"])
    return spec


def start(column_key: str, topic: dict, started_by: str) -> dict:
    """Create a script for ``topic`` and start researching it in the background."""
    settings = get_settings()
    column = video_columns.for_new_script(settings.paths.config_dir, column_key)
    fields = topic.setdefault("fields", {})
    explicit_audience = bool((fields.get("目标受众") or "").strip())
    if column.audience and not (fields.get("目标受众") or "").strip():
        fields["目标受众"] = column.audience  # a topic that names no audience is for the cell's
    from core.video import content_columns, production
    topic = dict(topic)
    production_id = topic.pop("_production", "") or topic.get("article_production", "")
    config = production.get(settings.paths.config_dir, settings.paths.jobs_dir, production_id) if production_id else None
    context = content_columns.snapshot(settings.paths.config_dir, production_id, config.job) if config else {}
    context = topic.pop("_content_context", None) or context
    if context.get("audience") and not explicit_audience:
        fields["目标受众"] = context["audience"]
    if started_by not in content_guard.SAMPLE_CREATORS:
        topic = content_guard.new_topic(topic, context, settings.paths.config_dir, settings.paths.jobs_dir)
    script = store.create_script(column.key, topic, created_by=started_by,
                                 production=production_id, content_context=context)
    if topic.get("article"):
        store.update_script(script["id"], status="WRITING", production=topic.get("article_production", ""))
    spawn(script["id"], started_by)
    return script


def spawn(script_id: str, started_by: str = "") -> None:
    """Run (or resume) a script in a background thread."""
    threading.Thread(
        target=run, args=(script_id,), kwargs={"started_by": started_by},
        name=f"video-script-{script_id[:8]}", daemon=True,
    ).start()


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
        if (store.get_script(script_id) or {}).get("current_version"):
            # the draft was already saved and only the audits after it broke: keep the draft
            store.update_script(script_id, status="DRAFT", stage=None, error=f"审计没有完成：{type(exc).__name__}: {exc}")
            return "DRAFT"
        store.update_script(script_id, status="FAILED", error=f"{type(exc).__name__}: {exc}")
        return "FAILED"
    finally:
        _active.discard(script_id)
        try:
            story_pool.reconcile()
        except Exception:  # recovery must not hide the author's original result
            logger.exception("video event reservations could not be reconciled")


def _run(script_id: str, started_by: str, llm_config: Optional[LLMConfig]) -> str:
    from web.runner.executor import start_job
    from web.services import yaml_io

    settings = get_settings()
    script = store.get_script(script_id)
    column = video_columns.get(settings.paths.config_dir, script["column_key"])
    topic = script["topic"]

    if topic.get("article"):
        from web.services import video_articles
        if script["current_version"]:
            if script.get("stage"):
                return video_articles.audit(script)
            store.update_script(script_id, status="DRAFT", stage=None, error=None)
            return "DRAFT"
        return video_articles.write(script, started_by=started_by, llm_config=llm_config)

    # 1. Research: an ordinary job, unless the topic comes with a report already (a short video cut from a longer
    # one is written from that one's research, its sources and citation check).
    report_path = topic.get("from_report") or script["report_path"] or ""
    if not report_path and column.research_mode == "none" and not topic.get("theme_research"):
        store.update_script(script_id, status="FAILED",
                            error=f"配方 {column.key} 不做调研（research.mode: none），选题要带上研究报告")
        return "FAILED"
    if not report_path:
        job_name = research_job_name(column.key, script_id)
        # a restart cut this run off after its research finished: the job's own state remembers the report
        done = (StateManager.get(job_name) or {}).get("last_output") or ""
        if done and os.path.isfile(done if os.path.isabs(done) else os.path.join(workspace_dir(), done)):
            report_path = done
    if not report_path:
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
        report = report_of(script, report_path)
    except OSError as exc:
        store.update_script(script_id, status="FAILED", error=f"读不到研究报告 {report_path}：{exc}")
        return "FAILED"
    citation_check = (report_meta.get(report_path) or {}).get("citation_check")
    _attach_report(script, report_path, column, started_by)

    if script["current_version"] and column.writer != "adapt":
        version = store.get_version(script_id, script["current_version"])
        optional = (version.get("meta", {}).get("checks") or {}).get("mode") == "on_request"
        if optional and script.get("stage") != "事实审计":
            store.update_script(script_id, status="DRAFT", stage=None, error=None)
            return "DRAFT"
        # 2b. Resume: the draft was saved and the audits after it were cut off — only the audits are left.
        return _resume_audit(script_id, script, report, report_path, column)
    if script["current_version"]:
        store.update_script(script_id, status="DRAFT", stage=None, error=None)
        return "DRAFT"

    # 2. Write.
    return _write(script_id, report=report, report_path=report_path, citation_check=citation_check, column=column,
                  topic=topic, started_by=started_by, llm_config=llm_config)


def _write(script_id: str, *, report: str, report_path: str, citation_check, column, topic: dict, started_by: str,
           llm_config: Optional[LLMConfig], failed_status: str = "FAILED", style=None) -> str:
    """Write a new version directly from ``report``. Returns DRAFT, or
    ``failed_status`` when writing failed (a rewrite keeps the script a DRAFT: its earlier versions still stand)."""
    if topic.get("article"):
        from web.services import video_articles
        return video_articles.write(store.get_script(script_id), started_by=started_by, llm_config=llm_config)
    settings = get_settings()
    # error=None: a stale note (e.g. a false "interrupted") must not outlive the work.
    store.update_script(script_id, status="WRITING", report_path=report_path, error=None)
    script = content_guard.with_policy(store.get_script(script_id), settings.paths.jobs_dir)
    from core.video.content_columns import apply_script_context
    column = apply_script_context(column, script.get("content_context") or {})
    joint_input = research_dossier.enabled(script) or bool(topic.get("theme_research"))
    dossier_report, dossier_selection = report, "item" if topic.get("from_item") else "report"
    guarded = bool(script["topic"].get("matrix_dedup")) and not content_guard.protected(script_id)
    config, review_config = _llm_configs(llm_config, column)
    dedup_meta, filtered_material = {}, ""
    if guarded:
        store.update_script(script_id, topic=script["topic"])
        topic = script["topic"]
        reference = (report_meta.get(report_path) or {}).get("material") or {}
        full_path = report_path if os.path.isabs(report_path) else os.path.join(workspace_dir(), report_path)
        original = report_material.read(reference, full_path, _read_report(report_path)) if reference else ""
        try:
            if story_pool.enabled(topic):
                store.update_script(script_id, stage="选题")
                key = content_guard.topic_scope(topic, settings.paths.config_dir)
                story_pool.reconcile()
                peers = content_guard.coverage(topic, settings.paths.config_dir, settings.paths.jobs_dir, script_id=script_id)
                peers += story_pool.reservations(key, script_id)
                if script["current_version"] and topic.get("story"):
                    story = topic["story"]
                    pool = story_pool.get(story["pool_id"])
                else:
                    if topic.get("from_item") and original:
                        # A flash item cannot wander into another item from its feed.
                        selected = content_guard.sources(report)
                        original = "\n\n---\n\n".join(p["text"] for p in story_pool.packets(report, original)
                                                                 if content_guard.sources(p["text"]) & selected)
                    selection_report = research_dossier.selection_brief(report, script)
                    pool = story_pool.load_or_create(key, selection_report, original, peers, config)
                    story, _ = story_pool.take(script_id, pool, peers)
                topic = {**topic, "title": story["title"], "story": story}
                script = {**script, "topic": topic}
                filtered_material = story_pool.material_for(pool, story)
                if joint_input:
                    dossier_report, dossier_selection = research_dossier.select_report(dossier_report, story, pool, script)
                    if dossier_selection == "column_explanation":
                        selected = content_guard.sources(dossier_report)
                        related = [packet["text"] for packet in pool["packets"]
                                   if content_guard.sources(packet["text"]) & selected]
                        filtered_material = "\n\n---\n\n".join(related) or filtered_material
                report = story_pool.brief(story) + "\n\n" + filtered_material
                dedup_meta = story_pool.selection(script_id)
                # News keeps the selected event as its writing scope. A coherent
                # domain explainer follows its completed dossier main question;
                # the stored event and coverage reservation remain unchanged.
                if dossier_selection != "column_explanation":
                    column = replace(column, script_style=column.script_style + "\n" + story_pool.brief(story))
            else:
                report, filtered_material, dedup_meta = content_guard.prepare(
                    script, report, original, settings.paths.config_dir, settings.paths.jobs_dir)
                dossier_report = report
        except story_pool.NoCandidate as exc:
            peers = [{"script_id": d["peer_id"], "title": d["title"]} for d in exc.decisions if d["peer_id"]]
            return _duplicate_script(script_id, content_guard.DuplicateContent(peers, str(exc)), failed_status)
        except content_guard.DuplicateContent as exc:
            return _duplicate_script(script_id, exc, failed_status)
    if column.writer == "adapt":
        if guarded and filtered_material:
            report, citation_check = filtered_material, None
        try:
            style = style or video_styles.resolve(settings.paths.config_dir, column)
            waiting = video_plans.open_plan(script_id)
            if waiting and waiting["state"] == "PENDING" and waiting["style"] != _style_keys(style):
                video_plans.mark(waiting["id"], "SUPERSEDED", only_from=("PENDING",))  # made for another style
                waiting = None
            if waiting and waiting["state"] == "CONFIRMED":
                # the user confirmed this plan and its draft was cut off (a restart): write from it, no new plan
                _version_from_plan(script_id, waiting, report=report, report_path=report_path, column=column,
                                   started_by=started_by, llm_config=config, review_llm_config=review_config)
            elif pauses(store.get_script(script_id), column, started_by):
                if waiting is None:  # a plan already waiting (a restart cut off a redo) is shown again, at no cost
                    _make_plan(script_id, report=report, report_path=report_path, citation_check=citation_check,
                               column=column, topic=topic, style=style, started_by=started_by, llm_config=config,
                               previous_status=failed_status)
                store.update_script(script_id, status="PLAN_READY", stage=None, error=None)
                return "PLAN_READY"
            else:
                _adapt_version(script_id, report=report, report_path=report_path, citation_check=citation_check,
                               column=column, topic=topic, style=style, started_by=started_by, llm_config=config,
                               review_llm_config=review_config)
        except content_guard.DuplicateContent as exc:
            return _duplicate_script(script_id, exc, failed_status)
        except (AdaptError, writer.ScriptError, video_styles.StyleError) as exc:
            store.update_script(script_id, status=failed_status, stage=None, error=f"改编失败：{exc}")
            return failed_status
        store.update_script(script_id, status="DRAFT", stage=None, error=None)
        return "DRAFT"
    try:
        store.update_script(script_id, stage="写稿")
        material_ref = (report_meta.get(report_path) or {}).get("material") or {}
        full_path = report_path if os.path.isabs(report_path) else os.path.join(workspace_dir(), report_path)
        material = (report_material.read(material_ref, full_path, _read_report(report_path))
                    if material_ref else "")
        if guarded:
            material = filtered_material
        if topic.get("from_item") and material and not topic.get("story"):
            # A discovered topic is one item, not the entire feed. Keep the
            # original blocks that contain its links, with its selected context.
            from core.provenance import extract_urls, normalize_url

            urls = {normalize_url(url) for url in extract_urls(report)}
            parts = [part for part in material.split("\n\n---\n\n")
                     if urls & {normalize_url(url) for url in extract_urls(part)}]
            material = "\n\n---\n\n".join(parts)
        writing_topic = topic
        if dossier_selection == "column_explanation":
            question = re.search(r"(?m)^##\s+本期主问题[：:]\s*(.+)$", dossier_report)
            writing_topic = {key: value for key, value in topic.items() if key != "story"}
            writing_topic["title"] = question.group(1).strip()
        written = writer.write(
            report=dossier_report if joint_input else material or report,
            citation_check=None if material else citation_check,
            topic=writing_topic,
            column_style=column.script_style + (
                "\n本矩阵近期已经讲过以下选题，请从剩余素材选择新选题，标题、开场和正文都不要复述："
                + "；".join(p["title"] for p in dedup_meta.get("peers", [])) if dedup_meta.get("peers") else ""),
            target_minutes=column.target_minutes,
            llm_config=config,
            voice=column.voice_id or None,
            paragraph_units=tuple(column.paragraph_units),
            topic_fields=column.writer_topic_fields(),
            **({"original_material": material} if joint_input else {}),
        )
        written.meta["input_kind"] = ("report_and_original_material" if joint_input and material
                                      else "original_material" if material else "report")
        if joint_input:
            written.meta["research_dossier"] = research_dossier.receipt(dossier_report, material, dossier_selection)
        if material:
            written.meta["material"] = material_ref
        if guarded:
            content_guard.check(script, written.sections, settings.paths.config_dir, settings.paths.jobs_dir)
            written.meta["content_dedup"] = dedup_meta
        if topic.get("story"):
            written.meta["story"] = topic["story"]
    except content_guard.DuplicateContent as exc:
        return _duplicate_script(script_id, exc, failed_status)
    except writer.ScriptError as exc:
        store.update_script(script_id, status=failed_status, stage=None, error=f"写稿失败：{exc}")
        return failed_status

    meta = {**written.meta, "report_path": report_path, "written_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "cell": video_columns.provenance(settings.paths.config_dir, column.key)}
    store.add_version(
        script_id,
        title=written.title,
        sections=written.sections,
        length_check=written.length_check,
        edit_type="GENERATED",
        meta={**meta, "checks": adapt_review.unrequested()},
        created_by=started_by,
        story_claim=(topic.get("story") or {}).get("event_key", ""),
    )
    store.update_script(script_id, status="DRAFT", error=None, stage=None)
    return "DRAFT"


def _duplicate_script(script_id: str, exc: content_guard.DuplicateContent, failed_status: str) -> str:
    reason = str(exc)
    if content_guard.archive_duplicate(script_id, reason, [p["script_id"] for p in exc.peers]):
        store.update_script(script_id, status=failed_status, stage=None, error=reason)
    return failed_status


def _attach_report(script: dict, report_path: str, column, started_by: str) -> None:
    """Record the report this script is written from (spec/video-reports.md), once per report file."""
    from core.video import reports

    if script.get("report_id") and script.get("report_path") == report_path:
        return
    reused = bool(script["topic"].get("from_report"))
    uses = (video_columns.provenance(get_settings().paths.config_dir, column.key) or {}).get("uses") or {}
    reports.attach(script["id"], report_path,
                   job="" if reused else research_job_name(column.key, script["id"]),
                   topic=None if reused else script["topic"],
                   research="" if reused else ((uses.get("research") or {}).get("name") or ""),
                   created_by=started_by or script.get("created_by") or "")


def _resume_audit(script_id: str, script: dict, report: str, report_path: str, column) -> str:
    """Redo the fact and style audit of the current version, which a restart or a timeout left rule-only."""
    from types import SimpleNamespace

    config, review_config = _llm_configs(None, column)
    version = store.get_version(script_id, script["current_version"])
    rule_issues = adapt_review.rules(version["sections"], column=column, target_minutes=column.target_minutes)
    store.update_script(script_id, status="DRAFT", error=None, stage="事实审计")
    written = SimpleNamespace(title=version["title"], sections=version["sections"], meta=version["meta"],
                              length_check=version["length_check"])
    audit_meta = _audit_short(script_id, written, report, column, config, review_config, rule_issues)
    meta = {k: v for k, v in version["meta"].items() if k not in ("checks", "problems", "audit_usage")}
    store.update_version_audit(script_id, version["version"], sections=written.sections,
                               meta={**meta, "report_path": report_path, **audit_meta},
                               length_check=written.length_check, title=written.title)
    store.update_script(script_id, stage=None)
    return "DRAFT"


def _audit_short(script_id: str, written, report: str, column, config, review_config, rule_issues: list) -> dict:
    """Rule checks and the fact audit of a script written in one call, as the version's ``checks``.

    The audit never fails the script: when it cannot run, the checks say so and the reason goes in ``problems``."""
    from core.llm import LLMClient
    from core.video import audit as video_audit

    base = review_config or config
    # The review model is often a slow free one: cap each
    # attempt and the whole call so a slow audit costs minutes, not the 10-minute attempts the writer is allowed.
    if is_dataclass(base):
        # a long script (a roundup is about 1000 units) gives the slow review model twice the time
        total = column.audit_timeout_seconds or AUDIT_TIMEOUT_SECONDS * (2 if column.target_minutes >= 3 else 1)
        base = replace(base, request_timeout=min(base.request_timeout, AUDIT_REQUEST_SECONDS),
                       timeout=min(base.timeout, total))
    client = LLMClient(base)
    try:
        checks, note = video_audit.audit(client, written.sections, report, rule_issues=rule_issues,
                                          column_style=column.script_style, audience=column.audience,
                                          style_severity=adapt_review.WARN)
    except Exception as exc:  # noqa: BLE001 - an audit that crashes must not lose the draft
        logger.exception("fact audit of script %s failed", script_id)
        checks, note = _rules_only(rule_issues, None, written.sections), f"事实审计没有完成：{type(exc).__name__}: {exc}"
    checks = _current_copy_checks(checks, written.sections, written.meta)
    problems = [p for p in (written.meta.get("problems") or []) if not p.startswith(STALE_AUDIT_NOTES)]
    problems += [note] if note else []
    meta = {"checks": checks, "problems": problems, "audit_usage": dict(client.usage),
            "audit_prompt_version": video_audit.PROMPT_VERSION}
    # A failed fact audit provides no basis for model-driven repairs. Keep the
    # draft and pending marks; resume the audit instead of making blind edits.
    return _revise_short(script_id, written, report, column, config, meta) if checks.get("model_ran") else meta


#: Notes a finished audit replaces: the one saying the audits were still running, and the ones saying one failed.
STALE_AUDIT_NOTES = ("事实审计", "风格审计", "新观众审计")
#: Quality takes priority over the old single rewrite; still bound model calls when a draft cannot be repaired.
SHORT_REVISE_ROUNDS = 3
#: Rules whose finding a rewrite of a paragraph cannot fix (the length is the whole script's).
UNREVISABLE_RULES = ("E4", "N3", "B1")


def _current_copy_checks(checks: dict, sections: list[dict], meta: dict) -> dict:
    out = {**checks, "issues": adapt_review.current_issues(checks.get("issues", []), sections, meta)}
    out["summary"] = adapt_review.summarize(out, len(sections))
    return out


def _revise_short(script_id: str, written, report: str, column, config, audit_meta: dict, *,
                  allow_editorial: bool = True) -> dict:
    """Repair fact/source problems, then rerun the audits, up to the bounded quality budget.

    Rewrites need explicit fact verdicts; an empty issue list cannot silently clear a review mark.
    Concrete listening problems get bounded editorial passes, without becoming approval gates.
    Failed calls preserve the draft and its unresolved findings."""
    from core.llm import LLMClient
    from core.video import audit as video_audit
    from core.video import editorial as video_editorial
    from core.video.adapt.common import paragraphs as walk, pid
    from core.video.adapt.pipeline import _resolved

    rounds = min(5, max(getattr(column, "revise_rounds", 0), SHORT_REVISE_ROUNDS))
    checks = audit_meta["checks"]
    initial = list(checks["issues"])
    problems = list(audit_meta["problems"])
    script = store.get_script(script_id) or {}
    citation_check = (report_meta.get(script.get("report_path") or "") or {}).get("citation_check")
    trusted = writer.trusted_urls(report, citation_check)
    client = LLMClient(config)
    base = _llm_configs(None, column)[1] or config
    auditor = LLMClient(replace(base, request_timeout=min(base.request_timeout, AUDIT_REQUEST_SECONDS),
                                timeout=min(base.timeout, column.audit_timeout_seconds or AUDIT_TIMEOUT_SECONDS))
                        if is_dataclass(base) else base)
    completed = 0
    repair_completed = 0
    editorial_used = False
    editorial_attempt = None
    editorial_attempts = []
    direction = written.meta.get("editorial_brief") or {}
    candidate_repair_usage = []
    editorial_limit = video_editorial.MAX_ROUNDS if allow_editorial else 0
    for _ in range(rounds + editorial_limit + 1):
        sections = written.sections
        editorial = {}
        open_issues = [i for i in checks["issues"]
                       if (i["audit"] == "fact" or i.get("source") == "audience_copy")
                       and i["rule"] not in UNREVISABLE_RULES]
        todo = adapt_review.targets(sections, {**checks, "issues": open_issues, "newcomer": None})
        for si, pi, para in walk(sections):
            if para.get("evidence") == "NEEDS_REVIEW":
                todo.setdefault(pid(si, pi), []).append("【待核实】" + (para.get("review_reason") or "对照报告补齐事实依据和适用条件"))
        core_needed = bool(todo)
        if len(editorial_attempts) < editorial_limit:
            editorial = adapt_review.editorial_targets(sections, checks)
            editorial_used = editorial_used or bool(editorial)
            if editorial:
                for key, notes in todo.items():
                    editorial.setdefault(key, []).extend(notes)
                editorial_attempt = {"applied": False, "before": copy.deepcopy(sections), "targets": editorial,
                                     "before_title": written.title, "mode": "whole_script",
                                     "prompt_version": video_editorial.PROMPT_VERSION}
                editorial_attempts.append(editorial_attempt)
        if not editorial and core_needed and repair_completed >= rounds:
            break
        if not todo and not editorial:
            break
        store.update_script(script_id, stage=f"{'主编打磨' if editorial else '按审计意见改写'}（第 {completed + 1} 轮）")
        try:
            if editorial:
                source_sheet = written.meta.get("source_sheet")
                proposal = video_editorial.rewrite(
                    client, title=written.title, sections=sections,
                    material=video_editorial.source_material(source_sheet) if source_sheet else writer.report_body(report),
                    direction=direction, feedback=editorial,
                    column_style=column.script_style, target_minutes=column.target_minutes,
                    sheet=source_sheet, history=editorial_attempts[:-1],
                )
                revised_title = proposal["title"]
                revised, _ = writer._finalize(writer._Script.model_validate(proposal), trusted,
                                              column.target_minutes, column.voice_id or None)
                writer._require_chinese(revised)
                changed = [p for _, _, p in walk(revised)]
                editorial_attempt.update(title=revised_title, sections=copy.deepcopy(revised),
                                         editorial_brief=proposal["editorial_brief"])
            else:
                revised_title = written.title
                revised, changed = video_audit.revise(client, sections, report, todo, column.script_style,
                                                     trusted_sources=trusted)
        except Exception as exc:  # noqa: BLE001 - retain the audited draft on a failed rewrite
            logger.exception("revising script %s failed", script_id)
            problems.append(f"按审计意见改写没有成功：{type(exc).__name__}: {exc}")
            if editorial:
                editorial_attempt["reason"] = problems[-1]
            break
        if revised == sections and revised_title == written.title:
            break
        changed_objects = {id(p) for p in changed}
        rewritten_ids = {pid(si, pi) for si, pi, p in walk(revised) if id(p) in changed_objects}
        store.update_script(script_id, stage="改写后重新审计")
        rules = adapt_review.rules(revised, column=column, target_minutes=column.target_minutes)
        previous_checks = checks
        previous_problems = list(problems)
        try:
            checks, note = video_audit.audit(
                auditor, revised, report, rule_issues=rules, column_style=column.script_style, audience=column.audience,
                style_severity=adapt_review.WARN,
                # Factual repairs must explicitly resolve the known doubt. A
                # fresh editorial proposal may resolve listening and source
                # problems together: audit its whole text, without inventing a doubt for every new
                # paragraph merely because the narrative changed. Its own review
                # flags and any findings still require explicit resolution.
                verification_required=None if editorial else rewritten_ids,
            )
        except Exception as exc:  # noqa: BLE001 - never mistake an unexpected audit failure for approval
            logger.exception("post-rewrite audit of script %s failed", script_id)
            checks = _rules_only(rules, None, revised)
            checks["audits_complete"] = False
            video_audit.reconcile_evidence(revised, [], [], required=rewritten_ids)
            note = f"事实审计没有完成：{type(exc).__name__}: {exc}"
        problems = [p for p in problems if not p.startswith(STALE_AUDIT_NOTES)]
        if note:
            problems.append(note)
        completed += 1
        if not editorial:
            repair_completed += 1
        checks = _current_copy_checks(checks, revised, written.meta)
        checks.update(rounds=completed, deletions=[], initial=_resolved(initial, checks["issues"]))
        if editorial and not checks["summary"]["passed"] and checks.get("model_ran"):
            # Keep a better story eligible for selection: repair its concrete
            # source errors before comparing, without another editorial loop.
            candidate = writer.WrittenScript(title=revised_title, sections=revised,
                                             length_check=length.measure(revised, column.target_minutes),
                                             meta=written.meta)
            before_repair = copy.deepcopy(revised)
            repair = _revise_short(script_id, candidate, report, column, config,
                                   {"checks": checks, "problems": [], "audit_usage": {}},
                                   allow_editorial=False)
            candidate_repair_usage.append(repair.get("audit_usage"))
            revised, checks = candidate.sections, repair["checks"]
            completed += checks.get("rounds", 0)
            editorial_attempt["source_repair"] = copy.deepcopy({"before": before_repair, **repair})
            editorial_attempt["sections"] = copy.deepcopy(revised)
            checks.update(rounds=completed, initial=_resolved(initial, checks["issues"]))
        if editorial and not checks["summary"]["passed"]:
            # A taste edit must not replace an already source-checked draft with
            # newly misleading or unverified wording. Keep the attempt reviewable.
            editorial_attempt.update(applied=False, sections=revised, checks=checks,
                                     reason="编辑稿未通过事实复核，保留此前已核对的文稿", note=note)
            checks = previous_checks
            problems = previous_problems
            if core_needed:
                editorial_limit = 0
                continue  # Repair the retained original using its completed audit.
            break
        if editorial:
            editorial_attempt.update(checks=copy.deepcopy(checks))
            try:
                decision = video_editorial.compare(client, before_title=written.title, before=sections,
                                                    after_title=revised_title, after=revised,
                                                    column_style=column.script_style)
                editorial_attempt["decision"] = decision
            except Exception as exc:  # noqa: BLE001 - keep the checked original on an incomplete editorial comparison
                editorial_attempt["reason"] = f"编辑比较没有完成：{type(exc).__name__}: {exc}"
                checks, problems = previous_checks, previous_problems
                break
            if decision["choice"] != "B":
                checks, problems = previous_checks, previous_problems
                continue  # retain the better script; a rejected proposal can inform the next attempt
            editorial_attempt["applied"] = True
            direction = proposal["editorial_brief"]
        written.title = revised_title
        written.sections = revised
        written.length_check = length.measure(revised, column.target_minutes, voice=column.voice_id or None)
        if not checks.get("model_ran"):
            break  # retry via the audit stage rather than rewriting against an incomplete audit
    editorial_attempt = video_editorial.retained_attempt(editorial_attempts)
    return {**audit_meta, "checks": {**checks, "editorial_attempted": editorial_used}, "problems": problems,
            **({"editorial_attempt": editorial_attempt} if editorial_attempt else {}),
            **({"editorial_attempts": editorial_attempts} if editorial_attempts else {}),
            "audit_usage": _sum_usage(audit_meta.get("audit_usage"), client.usage, auditor.usage,
                                      *candidate_repair_usage)}


def _sum_usage(*usages: Optional[dict]) -> dict:
    out: dict = {}
    for usage in usages:
        for key, value in (usage or {}).items():
            out[key] = out.get(key, 0) + value
    return out


def _llm_configs(llm_config: Optional[LLMConfig], column=None) -> tuple:
    """(writing model, fact-audit model or None). The fact audit uses ``review_ai`` in system.yaml when it is set,
    unless the recipe says ``audit.model: writer``."""
    if llm_config is not None:
        return llm_config, None
    if column is not None and column.audit_model == "writer":
        return LLMConfig.from_system(load_system_config(get_settings().paths.config_dir), "ai"), None
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
    paths = get_settings().paths
    content_guard.check(content_guard.with_policy(store.get_script(script_id), paths.jobs_dir), adapted.sections,
                        paths.config_dir, paths.jobs_dir)
    return store.add_version(
        script_id,
        title=adapted.title,
        sections=adapted.sections,
        length_check=adapted.length_check,
        edit_type="GENERATED",
        meta={**adapted.meta, "report_path": report_path, "written_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "cell": video_columns.provenance(get_settings().paths.config_dir, column.key),
              **({"story": topic["story"], "content_dedup": story_pool.selection(script_id)} if topic.get("story") else {})},
        created_by=started_by,
        story_claim=(topic.get("story") or {}).get("event_key", ""),
    )


# --- pausing after the plan (writing.pause_after_plan, spec/video-steps.md) ----------------------------------

def pauses(script: dict, column, started_by: str) -> bool:
    """Whether this write stops at the plan for the user. A batch's script never does, whoever starts the write:
    a batch does not wait for the user (and its next tick would find nothing to do)."""
    return (column.writer == "adapt" and column.pause_after_plan and started_by != "autopilot"
            and script.get("created_by") != "autopilot")


def _style_keys(style) -> dict:
    return {"depth": style.depth.key, "tone": style.tone.key}


def _make_plan(script_id: str, *, report: str, report_path: str, citation_check: Optional[dict], column,
               topic: dict, style, started_by: str, llm_config, previous_status: str, note: str = "",
               previous: Optional[dict] = None) -> dict:
    """Steps ① and ② of the adaptation; the plan is stored to wait for the user."""
    sheet_id = adapt_facts.sheet_id(report)
    cached = store.get_fact_sheet(sheet_id)
    planned, _ = adapt_pipeline.make_plan(
        report=report,
        citation_check=citation_check,
        topic=topic,
        column=column,
        style=style,
        llm_config=llm_config,
        sheet=cached["sheet"] if cached else None,
        on_sheet=lambda sheet: store.save_fact_sheet(sheet_id, report_path=report_path, sheet=sheet),
        on_stage=lambda name: store.update_script(script_id, stage=name),
        note=note,
        previous=previous,
    )
    return video_plans.save(script_id, plan=planned.plan, problems=planned.problems, usage=planned.usage,
                            sheet_id=sheet_id, style=_style_keys(style), note=note,
                            previous_status=previous_status, created_by=started_by)


def _version_from_plan(script_id: str, row: dict, *, report: str, report_path: str, column, started_by: str,
                       llm_config, review_llm_config=None) -> int:
    """Steps ③–⑤ from a confirmed plan, stored as a new GENERATED version."""
    style = video_styles.resolve(get_settings().paths.config_dir, column, row["style"].get("depth"),
                                 row["style"].get("tone"))
    cached = store.get_fact_sheet(row["sheet_id"])
    if cached is None:
        raise AdaptError("这份方案用的事实底稿不见了，请放弃方案后重新写稿")
    adapted = adapt_pipeline.write_from_plan(
        report=report,
        planned=adapt_pipeline.Planned(plan=row["plan"], problems=row["problems"], usage=row["usage"]),
        sheet=cached["sheet"],
        column=column,
        style=style,
        llm_config=llm_config,
        review_llm_config=review_llm_config,
        on_stage=lambda name: store.update_script(script_id, stage=name),
    )
    version = store.add_version(
        script_id,
        title=adapted.title,
        sections=adapted.sections,
        length_check=adapted.length_check,
        edit_type="GENERATED",
        meta={**adapted.meta, "report_path": report_path, "written_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "cell": video_columns.provenance(get_settings().paths.config_dir, column.key),
              "plan_confirmed": {"plan_id": row["id"], "by": row["confirmed_by"], "at": row["confirmed_at"],
                                 "edits": row["edits"], "note": row["note"]}},
        created_by=started_by,
    )
    video_plans.mark(row["id"], "USED", version=version)
    return version


def _waiting_plan(script_id: str) -> dict:
    script = store.get_script(script_id)
    if script is None or script["status"] != "PLAN_READY" or script_id in _active:
        raise RestyleError(f"文稿当前是 {(script or {}).get('status')}，没有等确认的方案")
    row = video_plans.pending(script_id)
    if row is None:
        raise RestyleError("没有等确认的方案")
    return row


def confirm_plan(script_id: str, *, edits: Optional[dict] = None, started_by: str = "") -> dict:
    """The user confirms the waiting plan (maybe rewording it); the draft is then written in the background."""
    row = _waiting_plan(script_id)
    plan = row["plan"]
    try:
        changed = video_plans.apply_edits(plan, edits or {})
    except ValueError as exc:
        raise RestyleError(str(exc)) from exc
    if not video_plans.confirm(row["id"], plan=plan, edits=changed, by=started_by):
        raise RestyleError("这份方案已经确认、重做或放弃了")
    store.update_script(script_id, status="WRITING", stage="排队", error=None, heartbeat_at=time.time())
    threading.Thread(target=run_from_plan, args=(script_id,), kwargs={"started_by": started_by},
                     name=f"video-plan-write-{script_id[:8]}", daemon=True).start()
    return {**store.get_script(script_id), "edits": changed}


def run_from_plan(script_id: str, *, started_by: str = "", llm_config: Optional[LLMConfig] = None) -> str:
    """Write the draft from the confirmed plan. A failure puts the plan back to wait, with the reason."""
    if script_id in _active:
        return "RUNNING"
    _active.add(script_id)
    row = None
    try:
        with store.beating(store.touch_script, script_id):
            script = store.get_script(script_id)
            row = video_plans.open_plan(script_id)
            if row is None or row["state"] != "CONFIRMED":
                raise AdaptError("没有已确认的方案")
            column = video_columns.get(get_settings().paths.config_dir, script["column_key"])
            report = report_of(script)
            config, review_config = _llm_configs(llm_config, column)
            store.update_script(script_id, stage=None)
            _version_from_plan(script_id, row, report=report, report_path=script["report_path"], column=column,
                               started_by=started_by, llm_config=config, review_llm_config=review_config)
        store.update_script(script_id, status="DRAFT", stage=None, error=None)
        return "DRAFT"
    except Exception as exc:  # noqa: BLE001 - the plan stays; the user can confirm it again, redo or discard it
        logger.exception("writing video script %s from its plan failed", script_id)
        if row is not None:
            video_plans.mark(row["id"], "PENDING", only_from=("CONFIRMED",))
        store.update_script(script_id, status="PLAN_READY", stage=None,
                            error=f"按方案写稿失败：{type(exc).__name__}: {exc}")
        return "PLAN_READY"
    finally:
        _active.discard(script_id)


def redo_plan(script_id: str, *, note: str, started_by: str = "") -> dict:
    """Make the plan again with the user's note, in the background. The old plan waits until the new one is in."""
    row = _waiting_plan(script_id)
    if not note.strip():
        raise RestyleError("写一句对方案的意见再重做")
    store.update_script(script_id, status="WRITING", stage="定方案", error=None, heartbeat_at=time.time())
    threading.Thread(target=run_redo_plan, args=(script_id, row["id"], note.strip()),
                     kwargs={"started_by": started_by}, name=f"video-plan-redo-{script_id[:8]}", daemon=True).start()
    return store.get_script(script_id)


def run_redo_plan(script_id: str, plan_id: str, note: str, *, started_by: str = "",
                  llm_config: Optional[LLMConfig] = None) -> str:
    if script_id in _active:
        return "RUNNING"
    _active.add(script_id)
    try:
        with store.beating(store.touch_script, script_id):
            script = store.get_script(script_id)
            row = video_plans.get(plan_id)
            column = video_columns.get(get_settings().paths.config_dir, script["column_key"])
            style = video_styles.resolve(get_settings().paths.config_dir, column, row["style"].get("depth"),
                                         row["style"].get("tone"))
            report = report_of(script)
            citation_check = (report_meta.get(script["report_path"]) or {}).get("citation_check")
            config, _ = _llm_configs(llm_config, column)
            _make_plan(script_id, report=report, report_path=script["report_path"], citation_check=citation_check,
                       column=column, topic=script["topic"], style=style, started_by=started_by, llm_config=config,
                       previous_status=row["previous_status"], note=note, previous=row["plan"])
        store.update_script(script_id, status="PLAN_READY", stage=None, error=None)
        return "PLAN_READY"
    except Exception as exc:  # noqa: BLE001 - the old plan still waits
        logger.exception("redoing the plan of video script %s failed", script_id)
        store.update_script(script_id, status="PLAN_READY", stage=None,
                            error=f"重做方案失败，原方案还在：{type(exc).__name__}: {exc}")
        return "PLAN_READY"
    finally:
        _active.discard(script_id)


def discard_plan(script_id: str, *, started_by: str = "") -> dict:
    """Drop the waiting plan: the script goes back to what it was (a first write: FAILED, so it can be rewritten)."""
    row = _waiting_plan(script_id)
    if not video_plans.mark(row["id"], "DISCARDED", only_from=("PENDING",)):
        raise RestyleError("这份方案已经确认、重做或放弃了")
    script = store.get_script(script_id)
    back = row["previous_status"] if script.get("current_version") else "FAILED"
    store.update_script(script_id, status=back, stage=None,
                        error="方案已放弃；要写稿就重跑「写稿」" if back == "FAILED" else None)
    return store.get_script(script_id)


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
        report_of(script)
    except OSError as exc:
        raise RestyleError(f"无法使用研究报告 {script['report_path']}：{exc}") from exc
    column = video_columns.get(settings.paths.config_dir, script["column_key"])
    if column.writer != "adapt":
        raise RestyleError(f"配方 {column.key} 一次写成（writing.pipeline: single），没有换风格这一步；"
                           "要换写法就改配方的 writing.style 后重新生成")
    style = video_styles.resolve(settings.paths.config_dir, column, depth, tone)  # StyleError → 422
    store.update_script(script_id, status="WRITING", stage="排队", error=None, heartbeat_at=time.time())
    thread = threading.Thread(
        target=run_restyle, args=(script_id, style.depth.key, style.tone.key),
        kwargs={"started_by": started_by, "previous_status": script["status"]},
        name=f"video-restyle-{script_id[:8]}", daemon=True,
    )
    thread.start()
    return store.get_script(script_id)


def rewrite(script_id: str, *, started_by: str = "") -> dict:
    """Write a new version from the script's own report, in the background — no new research (spec/video-steps.md)."""
    script = store.get_script(script_id)
    if script["status"] not in ("DRAFT", "APPROVED", "FAILED") or script_id in _active:
        raise RestyleError(f"文稿当前是 {script['status']}，不能重写")
    if not script["report_path"] and not script["topic"].get("article"):
        raise RestyleError("这篇文稿没有研究报告，不能重写")
    try:
        report_of(script)
    except OSError as exc:
        raise RestyleError(f"无法使用研究报告 {script['report_path']}：{exc}") from exc
    store.update_script(script_id, status="WRITING", stage="排队", error=None, heartbeat_at=time.time())
    threading.Thread(target=run_rewrite, args=(script_id,),
                     kwargs={"started_by": started_by, "previous_status": script["status"]},
                     name=f"video-rewrite-{script_id[:8]}", daemon=True).start()
    return store.get_script(script_id)


def run_rewrite(script_id: str, *, started_by: str = "", previous_status: str = "DRAFT",
                llm_config: Optional[LLMConfig] = None) -> str:
    """Rewrite from the same report in the recipe's (or the current version's) style. Returns the final status.

    A new version is a draft (as after a restyle); a rewrite that fails leaves the script as it was — an approved
    script stays approved, with the same approved version."""
    if script_id in _active:
        return "RUNNING"
    _active.add(script_id)
    failed = previous_status
    try:
        with store.beating(store.touch_script, script_id):
            settings = get_settings()
            script = store.get_script(script_id)
            column = video_columns.get(settings.paths.config_dir, script["column_key"])
            report = report_of(script)
            citation_check = (report_meta.get(script["report_path"]) or {}).get("citation_check")
            style = None
            if column.writer == "adapt" and script["current_version"]:
                meta = (store.get_version(script_id, script["current_version"]) or {}).get("meta") or {}
                style = _style_of(meta, column)  # keep the style the current version was written in
            store.update_script(script_id, stage=None)
            return _write(script_id, report=report, report_path=script["report_path"], citation_check=citation_check,
                          column=column, topic=script["topic"], started_by=started_by, llm_config=llm_config,
                          failed_status=failed, style=style)
    except Exception as exc:  # noqa: BLE001 - the earlier versions must stay usable
        logger.exception("rewriting video script %s failed", script_id)
        store.update_script(script_id, status=failed, stage=None, error=f"重写失败：{type(exc).__name__}: {exc}")
        return failed
    finally:
        _active.discard(script_id)


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
            report = report_of(script)
            citation_check = (report_meta.get(script["report_path"]) or {}).get("citation_check")
            config, review_config = _llm_configs(llm_config, column)
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
        saved = adapt_review.normalize(meta["checks"], len(version["sections"]))
        if saved.get("mode") == "on_request":
            return saved
        # Old model success cannot bypass a newly applicable local copy check.
        # Overlay it for this read, preserving every stored audit and version.
        checks = {**saved, "issues": adapt_review.current_issues(saved.get("issues", []), version["sections"], meta)}
        checks["summary"] = adapt_review.summarize(checks, len(version["sections"]))
        return checks
    issues = adapt_review.rules(version["sections"], column=column, target_minutes=column.target_minutes)
    issues = adapt_review.current_issues(issues, version["sections"], meta)
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
                    **({"review_reason": str(p["review_reason"])} if p.get("review_reason") else {}),
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
    if base_meta.get("article"):
        from core.video.article_writer import edited_checks
        from web.services.video_articles import source_of
        meta = {"article": base_meta["article"], "input_kind": "article",
                "checks": edited_checks(clean, base_meta, source_of(script))}
    elif (base_meta.get("checks") or {}).get("mode") == "on_request":
        meta = {"problems": [], "generation": "goal_first", "checks": adapt_review.unrequested()}
    else:
        issues = adapt_review.rules(clean, column=column, target_minutes=column.target_minutes,
                                    plan=plan, sheet=sheet, style=style)
        meta = {"problems": writer.lint(clean),
                "checks": _rules_only(issues, plan, clean, model_checks_stale=True)}
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
        length_check=length.measure(clean, None if base_meta.get("article") else column.target_minutes,
                                    section_budgets=budgets, voice=column.voice_id),
        edit_type="EDITED",
        meta=meta,
        created_by=edited_by,
    )
    return version

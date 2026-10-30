"""Video scripts API: topics → scripts → review → approval → AutoVideo renders.

See spec/video-scripts.md and, for styles and restyling, spec/video-adaptation.md.
"""

from __future__ import annotations

import json
import os
from typing import Literal, Optional

from fastapi import Query, APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.video import columns as video_columns
from core.video.autovideo import AutoVideoError, AutoVideoUnreachable
from core.video import credibility, excerpt, outputs, progress, store
from core.video import plans as video_plans
from core.video import steps as video_steps
from core.video import topics as video_topics
from core.video.adapt import styles as video_styles
from web import audit
from web.deps import require_admin, require_editor, require_viewer
from web.models import ApiError
from web.routes import config as config_routes
from web.services import video_executor, video_render, video_workflow
from web.settings import get_settings

router = APIRouter(prefix="/api/video", tags=["video"])
from web.routes.video_articles import router as article_router
router.include_router(article_router)
from web.routes.video_screenshots import router as screenshots_router
router.include_router(screenshots_router)


def _not_found(what: str) -> HTTPException:
    return HTTPException(404, detail=ApiError.make("not_found", f"{what}不存在"))


def _columns() -> dict:
    """The columns new scripts can be made with (blocked and broken recipes left out)."""
    try:
        return video_columns.load(get_settings().paths.config_dir)
    except video_columns.ColumnError as exc:
        raise HTTPException(500, detail=ApiError.make("bad_column_config", str(exc))) from exc


class _Columns(dict):
    """The columns that build. One built for another length than its recipe's, or without its research (``deep_dive-m8``,
    ``flash-nr``) is not listed, there being no list of every length: it is made when a script asks for it."""

    def __init__(self, config_dir: str, columns: dict, every_recipe: Optional[dict] = None):
        super().__init__(columns)
        self._config_dir = config_dir
        self._recipes = every_recipe
        self._variants: dict = {}

    def get(self, key, default=None):
        if key in self:
            return self[key]
        from core.video import recipes

        if recipes.split_variant_key(key):
            if key not in self._variants:
                if self._recipes is None:
                    self._recipes = recipes.expanded(self._config_dir)
                recipe = recipes.find(self._config_dir, key, every=self._recipes)
                self._variants[key] = recipe.column if recipe is not None else None
            if self._variants[key] is not None:
                return self._variants[key]
        return default


def _known_columns() -> dict:
    """Every column that builds, blocked ones included: existing scripts stay readable, approvable and renderable."""
    from core.video import recipes

    config_dir = get_settings().paths.config_dir
    every = recipes.expanded(config_dir)
    return _Columns(config_dir, {key: r.column for key, r in every.items() if r.column is not None}, every)


def _creatable(key: str):
    """The column a new script may be made with: a listed one, or one at another length or without research that
    its recipe allows."""
    from core.video import recipes

    columns = _columns()
    if key in columns:
        return columns[key]
    if recipes.split_variant_key(key):
        try:
            return video_columns.for_new_script(get_settings().paths.config_dir, key)
        except video_columns.ColumnError:
            pass
    raise HTTPException(422, detail=ApiError.make("unknown_column", f"没有这个栏目：{key}"))


def _script_or_404(script_id: str) -> dict:
    script = store.get_script(script_id)
    if script is None:
        raise _not_found("文稿")
    return script


def _render_out(render: dict) -> dict:
    from core.video.visual_materials import summary
    client = video_render.get_client()
    return {
        **{k: render[k] for k in (
            "id", "script_id", "version", "status", "title", "autovideo_job_id", "active_stage",
            "attempt_count", "resolved_video_form", "error_code", "error_message",
            "submitted_at", "completed_at", "created_at", "updated_at")},
        "source_chars": len(render["source_text"]),
        "timings": video_render.render_timings(render),
        "visuals": summary(render.get("visuals")),
        "autovideo_url": client.job_page_url(render["autovideo_job_id"]) if render["autovideo_job_id"] else None,
        "files": [
            {**a, "url": f"/api/video/renders/{render['id']}/files/{a['name']}"}
            for a in render.get("artifacts") or []
        ],
    }


def _script_summary(script: dict, published: Optional[dict] = None, *, versions: Optional[dict] = None) -> dict:
    current = (versions.get(script["id"]) if versions is not None else
               store.get_version(script["id"], script["current_version"]) if script["current_version"] else None)
    return {
        **script,
        "credibility": credibility.label(current, auditing=bool(script.get("stage"))) if current else None,
        "auto": script.get("created_by") == "autopilot",
        "title": current["title"] if current else script["topic"].get("title", ""),
        "length_check": current["length_check"] if current else None,
        "published": progress.is_published(script, (published if published is not None else store.published_versions_by_script()).get(script["id"], set())),
    }


# --- Columns & topics ----------------------------------------------------------


@router.get("/columns")
def list_columns(user=Depends(require_viewer)) -> list[dict]:
    from core.video import recipes as matrix

    config_dir = get_settings().paths.config_dir
    return [{**r.column.as_dict(), "status": r.status}
            for r in matrix.expanded(config_dir).values() if r.usable]


@router.get("/cells")
def list_cells(user=Depends(require_viewer)) -> list[dict]:
    """Every recipe with its status, for choosing what to write (spec/video-recipes.md)."""
    from core.video import recipes as matrix

    try:
        return matrix.catalog(get_settings().paths.config_dir)
    except video_columns.ColumnError as exc:
        raise HTTPException(500, detail=ApiError.make("bad_recipe", str(exc))) from exc


@router.get("/coverage")
def cell_coverage(user=Depends(require_viewer)) -> list[dict]:
    """Per cell: topic sources, topics waiting and already taken (the content matrix view of /topics)."""
    from web.services import video_autopilot

    return video_autopilot.coverage()


@router.get("/board")
def home_board(user=Depends(require_viewer)) -> dict:
    """Counts per pipeline stage and what needs the user, for the home page."""
    from web.services import video_board

    paths = get_settings().paths
    return video_board.board(_known_columns(), config_dir=paths.config_dir, jobs_dir=paths.jobs_dir)


@router.get("/alerts")
def automation_alerts(user=Depends(require_viewer)) -> list[dict]:
    """What ran on its own this week and went wrong (empty batches, failed or restarted jobs, missed plans)."""
    from web.services import automation_health

    return automation_health.alerts()


class AlertAckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=3, max_length=300)


@router.post("/alerts/ack")
def ack_alert(body: AlertAckIn, user=Depends(require_editor)) -> dict:
    """The user has seen an alert: it leaves the board."""
    from core.video import autopilot_store

    autopilot_store.ack_alert(body.key, user["username"])
    audit.log("automation_alert_ack", user=user["username"], target=body.key, result="acked")
    return {"key": body.key, "acked": True}


@router.get("/shipments")
def shipments(user=Depends(require_viewer), compact: bool = False) -> list[dict]:
    """Approved scripts with their render and publication state, for the 成片与发布 page."""
    from web.services import video_board

    rows = video_board.shipments(_known_columns())
    if compact:
        from web.services.video_list_payload import compact as project
        return [project(row) for row in rows]
    return rows


@router.get("/quality")
def draft_quality(limit: int = 200, user=Depends(require_viewer)) -> dict:
    """How much of each approved script's generated draft survived editing (core/video/quality.py)."""
    from core.video import quality

    reports = []
    for script in store.list_scripts(limit=min(max(limit, 1), 500)):
        row = quality.script_report(script, [store.get_version(script["id"], v["version"])
                                             for v in store.list_versions(script["id"])])
        if row is not None:
            reports.append(row)
    return {"columns": quality.summarize(reports), "scripts": reports}


@router.get("/styles")
def list_styles(user=Depends(require_viewer)) -> dict:
    """Depths and tones a script can be written in."""
    try:
        return video_styles.catalog(get_settings().paths.config_dir)
    except video_styles.StyleError as exc:
        raise HTTPException(500, detail=ApiError.make("bad_style_config", str(exc))) from exc


def _topics():
    from core.video import topic_store
    from web.services import video_autopilot

    video_autopilot.sync_topics()
    return topic_store


@router.get("/topics/sources")
def list_topic_sources(user=Depends(require_viewer)) -> list[dict]:
    """Every job that declares ``topics:`` (and ``manual``): its newest report, and why a report gave no topics."""
    from web.services import video_autopilot

    return _topic_source_rows(video_autopilot.sync_topics())


def _topic_source_rows(rows: list[dict]) -> list[dict]:
    from core.video import topic_store

    errors = topic_store.import_errors()
    for row in rows:
        failed = errors.get(row["name"], [])
        # only the newest report matters: older ones in an earlier format stay as they were
        row["report_error"] = failed[0]["error"] if failed and failed[0]["date"] == row["latest"] else ""
    manual = topic_store.load(topic_store.MANUAL)[1]
    return rows + [{"name": topic_store.MANUAL, "job": None, "label": "手动添加", "format": None, "prefix": "M",
                    "refresh": False, "error": "", "reports": 0, "latest": None, "report_error": "",
                    "count": len(manual)}]


@router.get("/topics/browser")
def topic_browser(user=Depends(require_viewer)) -> dict:
    """One fresh snapshot for the topic picker's first screen, including its default source's topics."""
    from web.services import video_autopilot

    cells = list_cells(user=user)
    rows = video_autopilot.sync_topics()
    coverage = video_autopilot.coverage(cells=cells, source_rows=rows)
    sources = _topic_source_rows(rows)
    current = next((c for c in coverage if c["cell"] == "flash"), None)
    current = current or next((c for c in coverage if c["sources"]), None) or next(iter(coverage), None)
    source = (current["sources"][0] if current["sources"] else "manual" if current["autopilot"] else "") if current else ""
    # Unknown sources stay visible in the coverage warning; no topics are invented for them.
    known = {"manual"} | {value for row in rows for value in (row["name"], row["job"])}
    initial = _topic_listing(source, None) if source in known else {"round": None, "rounds": [], "topics": []}
    return {"cells": cells, "coverage": coverage, "sources": sources, "initial": {"source": source, **initial}}


@router.get("/topics")
def list_topics(round: Optional[str] = None, source: str = "opportunities",
                user=Depends(require_viewer)) -> dict:
    """Topics of one report of a source (the newest by default); ``source=manual`` lists the ones added by hand."""
    from web.services import video_autopilot

    _topics()
    if not video_autopilot.known_source(source):
        raise HTTPException(422, detail=ApiError.make(
            "unknown_source", f"没有叫 {source} 的选题来源：在 Job 的 YAML 里写 topics: 才能当来源"))
    return _topic_listing(source, round)


def _topic_listing(source: str, round_date: Optional[str]) -> dict:
    from core.video import topic_store
    from web.services import topic_progress

    round_date, topics = topic_store.load(source, round_date)
    used = store.topic_ids_in_use()
    published = store.published_topic_ids()
    covered = topic_progress.scripts_by_topic([t.id for t in topics])
    dismissed = store.dismissed_topic_ids()
    return {
        "round": round_date,
        "rounds": topic_store.report_dates(source),
        "topics": [{**t.as_dict(), "source": t.source, "scripted": t.id in used, "published": t.id in published,
                    "dismissed": t.id in dismissed, "scripts": covered.get(t.id, [])} for t in topics],
    }


class ManualTopicIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=4, max_length=200)
    angle: str = Field(default="", max_length=2000)
    audience: str = Field(default="", max_length=200)
    fields: dict[str, str] = Field(default_factory=dict, description="Any other topic fields, e.g. 变化, 发布日期")


@router.post("/topics", status_code=201)
def add_topic(body: ManualTopicIn, user=Depends(require_editor)) -> dict:
    """A topic written by hand. It sits in the pool like any other (source ``manual``)."""
    topic_store = _topics()
    fields = {**body.fields, "核心角度": body.angle, "目标受众": body.audience}
    try:
        topic = topic_store.add_manual(body.title, fields, user["username"])
    except ValueError as exc:
        raise HTTPException(422, detail=ApiError.make("topic_required", str(exc))) from exc
    audit.log("video_topic_add", user=user["username"], target=topic.id, result="added")
    return {**topic.as_dict(), "source": topic.source}


class DismissIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_id: str = Field(min_length=3, max_length=120)
    dismissed: bool = True


@router.post("/topics/dismiss")
def dismiss_topic(body: DismissIn, user=Depends(require_editor)) -> dict:
    """Mark a topic "not writing" (or bring it back). Reversible; it only hides it from the counts and the pipeline."""
    store.set_dismissed(body.topic_id, body.dismissed)
    audit.log("video_topic_dismiss", user=user["username"], target=body.topic_id,
              result="dismissed" if body.dismissed else "restored")
    return {"topic_id": body.topic_id, "dismissed": body.dismissed}


@router.post("/autopilot/retry-held")
def retry_held_renders(user=Depends(require_editor)) -> dict:
    """Run again the items held for failed renders (their earlier failures stop counting), once what broke them is fixed."""
    from web.services import video_autopilot

    reopened = video_autopilot.retry_held_renders()
    audit.log("video_retry_held", user=user["username"], result=str(reopened))
    return {"reopened": reopened}


@router.get("/topics/discovery")
def discovery_status(source: str = "flash", user=Depends(require_viewer)) -> dict:
    """A source the console may run (``topics.refresh: true``): running now, and when its newest report came out."""
    from web.services import video_autopilot

    try:
        return video_autopilot.discovery_status(source)
    except ValueError as exc:
        raise HTTPException(422, detail=ApiError.make("not_refreshable", str(exc))) from exc


@router.post("/topics/discovery", status_code=202)
def start_discovery(source: str = "flash", user=Depends(require_editor)) -> dict:
    """Run that source's job now (it spends its searches). Refused while it is already running."""
    from web.services import video_autopilot

    try:
        started = video_autopilot.start_discovery_if_idle(source)
    except ValueError as exc:
        raise HTTPException(422, detail=ApiError.make("not_refreshable", str(exc))) from exc
    if not started:
        raise HTTPException(409, detail=ApiError.make("discovery_running", "这个选题来源的 Job 正在跑，等它跑完"))
    status = video_autopilot.discovery_status(source)
    audit.log("video_discovery_start", user=user["username"], target=status["job"], result="started")
    return status


# --- Research reports ----------------------------------------------------------
# Every report a script is written from, recorded once; a new script can start from any of them (spec/video-reports.md)


@router.get("/reports")
def list_research_reports(limit: int = 100, user=Depends(require_viewer)) -> list[dict]:
    from core.video import reports

    workspace = video_workflow.workspace_dir()
    return [{**r, "on_disk": reports.exists_on_disk(r, workspace)}
            for r in reports.list_reports(limit=min(max(limit, 1), 500))]


@router.get("/reports/{report_id}")
def get_research_report(report_id: str, user=Depends(require_viewer)) -> dict:
    from core.video import reports

    report = reports.get(report_id)
    if report is None:
        raise HTTPException(404, detail=ApiError.make("not_found", "没有这份研究报告"))
    return {**report, "on_disk": reports.exists_on_disk(report, video_workflow.workspace_dir())}


class ReportRecordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=500, description="A Markdown report under output/, as the Reports page lists it")


@router.post("/reports")
def record_research_report(body: ReportRecordIn, user=Depends(require_editor)) -> dict:
    """Record any engine report (once) so a video can be written straight from it, with its title as the topic."""
    from core import report_meta
    from core.video import reports
    from web.routes.reports import resolve_output_file

    full = resolve_output_file(body.path, allowed_exts=(".md",))
    workspace = video_workflow.workspace_dir()
    relative = os.path.relpath(full, os.path.realpath(workspace)).replace(os.sep, "/")
    if relative.startswith("../"):
        raise HTTPException(422, detail=ApiError.make("invalid_path", "这份报告不在工作区里，不能写成视频"))
    meta = report_meta.get(relative) or {}
    report = reports.record_file(relative, job=str(meta.get("job") or ""), created_by=user["username"])
    return {**report, "on_disk": True, "title": reports.title_of(full)}


# --- Scripts -----------------------------------------------------------------


class TopicIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="", max_length=200, description="Required, except for a comparison (made from its subjects)")
    angle: str = ""
    audience: str = ""
    subjects: list[str] = Field(default_factory=list, max_length=2, description="The two things a comparison sets against each other")
    dimensions: str = Field(default="", max_length=200, description="What a comparison compares them on")


class ScriptCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str = "deep_dive"
    production: str = Field(default="", description="来源生产绑定；业务栏目由该绑定确定，旧 column 仍是文稿配方")
    topic_id: Optional[str] = Field(default=None, description="A content-opportunity topic id, e.g. 2026-09-29/C3")
    topic: Optional[TopicIn] = None
    topic_ids: list[str] = Field(
        default_factory=list, description="A roundup's items: flash-news topic ids (F<n>) of one discovery report")
    video_form: Optional[str] = Field(
        default=None, description="One of the column's allowed forms; the column's default when left out")
    from_script: Optional[str] = Field(
        default=None, description="Write from this script's research report instead of researching again")
    report_id: Optional[str] = Field(
        default=None, description="Write from this recorded research report (GET /reports) instead of researching;"
                                  " the topic defaults to the one the report was researched for")


def _roundup_topic(body: ScriptCreateIn) -> dict:
    if body.topic_id is not None or not body.topic_ids:
        raise HTTPException(422, detail=ApiError.make("topic_required", "盘点要选 2–10 条快讯条目（topic_ids）"))
    topic_store = _topics()
    found = [topic_store.get(tid) for tid in body.topic_ids]
    missing = [tid for tid, item in zip(body.topic_ids, found) if item is None]
    if missing or any(not item.report_path for item in found):
        raise HTTPException(422, detail=ApiError.make(
            "unknown_topic", f"找不到这些条目：{', '.join(missing)}" if missing else "盘点只能选报告里的条目"))
    if len({item.report_path for item in found}) != 1 or len(set(body.topic_ids)) != len(body.topic_ids):
        raise HTTPException(422, detail=ApiError.make("roundup_items", "盘点的条目要来自同一份报告，且不重复"))
    report = found[0].report_path
    try:
        return video_topics.roundup_topic(found, report, body.topic.title if body.topic else "")
    except ValueError as exc:
        raise HTTPException(422, detail=ApiError.make("roundup_items", str(exc))) from exc


def _comparison_topic(body: ScriptCreateIn) -> dict:
    if body.topic_id is not None or body.topic_ids or body.topic is None:
        raise HTTPException(422, detail=ApiError.make("topic_required", "对比要填两个对象和对比维度（topic.subjects、topic.dimensions）"))
    try:
        return video_topics.comparison_topic(body.topic.subjects, body.topic.dimensions, body.topic.angle,
                                             body.topic.audience)
    except ValueError as exc:
        raise HTTPException(422, detail=ApiError.make("comparison_subjects", str(exc))) from exc


@router.post("/scripts", status_code=202)
def create_script(body: ScriptCreateIn, request: Request, user=Depends(require_editor)) -> dict:
    chosen = _creatable(body.column)
    if body.production:
        from core.video import production
        paths = get_settings().paths
        config = production.get(paths.config_dir, paths.jobs_dir, body.production)
        if config is None or not config.usable or config.recipe != chosen.key:
            raise HTTPException(422, detail=ApiError.make("invalid_production", "生产绑定与文稿配方不匹配"))
    if body.video_form is not None and body.video_form not in (chosen.allowed_forms or (chosen.video_form,)):
        raise HTTPException(422, detail=ApiError.make(
            "form_not_allowed", f"这个栏目只能用这些视频形式：{', '.join(chosen.allowed_forms or (chosen.video_form,))}"))
    report = None
    report_topic = None
    if body.report_id is not None:
        from core.video import reports

        if body.from_script is not None:
            raise HTTPException(422, detail=ApiError.make("one_report", "report_id 和 from_script 只能给一个"))
        report = reports.get(body.report_id)
        if report is None or not reports.exists_on_disk(report, video_workflow.workspace_dir()):
            raise HTTPException(422, detail=ApiError.make("unknown_report", "找不到这份研究报告，或报告文件已不在"))
        if body.topic_id is None and not body.topic_ids and chosen.topic_kind == "single":
            if body.topic is None and report["topic_id"]:
                body = body.model_copy(update={"topic_id": report["topic_id"]})
            elif body.topic is None or not (body.topic.subjects or body.topic.dimensions):
                # not researched for a topic, or a title typed for it: the report itself is the subject, no pool row
                typed = body.topic or TopicIn()
                report_topic = reports.topic_for(report, video_workflow.workspace_dir(),
                                                 typed.title, typed.angle, typed.audience)
                if len(report_topic["title"]) < 4:
                    raise HTTPException(422, detail=ApiError.make("topic_required", "选题标题至少 4 个字"))
    kind = chosen.topic_kind
    if kind == "roundup":
        topic = _roundup_topic(body)
    elif kind == "comparison":
        topic = _comparison_topic(body)
    elif report_topic is not None:
        topic = report_topic
    else:
        if body.topic_ids or (body.topic is not None and (body.topic.subjects or body.topic.dimensions)):
            raise HTTPException(422, detail=ApiError.make("topic_not_for_column", "这个栏目的选题不用条目列表或对比对象"))
        if (body.topic_id is None) == (body.topic is None):
            raise HTTPException(422, detail=ApiError.make("topic_required", "请选择一个内容机会，或填写一个选题"))
        if body.topic is not None and len(body.topic.title.strip()) < 4:
            raise HTTPException(422, detail=ApiError.make("topic_required", "选题标题至少 4 个字"))
        topic_store = _topics()
        if body.topic is not None:  # a title typed here becomes a topic in the pool first, like one added by hand
            made = topic_store.add_manual(body.topic.title, {"核心角度": body.topic.angle,
                                                             "目标受众": body.topic.audience}, user["username"])
            body = body.model_copy(update={"topic_id": made.id, "topic": None})
        found = topic_store.get(body.topic_id)
        if found is None:
            raise HTTPException(422, detail=ApiError.make("unknown_topic", f"找不到选题 {body.topic_id}"))
        topic = excerpt.topic_for(chosen, found)  # not researched: written from the item's part of its report
    if body.video_form is not None:
        topic = {**topic, "video_form": body.video_form}
    if body.from_script is not None:
        parent = store.get_script(body.from_script)
        if parent is None or not parent.get("report_path"):
            raise HTTPException(422, detail=ApiError.make("unknown_parent", "找不到这篇文稿，或它还没有研究报告"))
        topic = {**topic, "from_report": parent["report_path"], "parent_script": parent["id"]}
    if report is not None:
        topic = {**topic, "from_report": report["report_path"], "report_id": report["id"]}
    if chosen.research_mode == "none" and not topic.get("from_report"):
        raise HTTPException(422, detail=ApiError.make(
            "report_required", "这个配方不做调研（research.mode: none）：要从已有文稿切（from_script）或选发现报告里的条目"))
    if body.production:
        topic = {**topic, "_production": body.production}
    script = video_executor.create(body.column, topic, user["username"])
    audit.log("video_script_create", user=user["username"], target=script["id"], result="started",
              ip=request.client.host if request.client else None)
    return _script_summary(script)


@router.get("/scripts")
def list_scripts(limit: int = 100000, archived: bool = False, user=Depends(require_viewer), compact: bool = False) -> list[dict]:
    published = store.published_versions_by_script()
    renders = store.render_statuses_by_script()
    scripts = store.list_scripts(limit=100000) + store.list_scripts(limit=100000, archived=True)
    paths = get_settings().paths
    owners, _ = outputs.script_owners(paths.config_dir, paths.jobs_dir, scripts)
    scripts = sorted((s for s in scripts if bool(s.get("archived") or owners[s["id"]]["matrix_archived"]) == archived),
                     key=lambda s: (s["created_at"], s["id"]), reverse=True)[:min(max(limit, 1), 100000)]
    versions = store.current_versions([s["id"] for s in scripts])
    columns = _known_columns()
    from web.services import distributions
    distribution_rows = distributions.overview(scripts)
    rows = [{**_script_summary(s, published, versions=versions),
             "distributions": [d for d in distribution_rows[s["id"]] if d["version"] == s["current_version"]],
             **{k: v for k, v in owners[s["id"]].items() if k != "matrix_archived"},
             "column_label": '本地视频' if s['topic'].get('local_video') else owners[s["id"]]["production_name"] or (columns.get(s["column_key"]).name if columns.get(s["column_key"]) else s["column_key"]),
             **progress.render_state(s, renders.get(s["id"], []))} for s in scripts]
    if compact:
        from web.services.video_list_payload import compact as project
        return [project(row) for row in rows]
    return rows


class ArchiveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    archived: bool = True


@router.post("/scripts/{script_id}/archive")
def archive_script(script_id: str, body: Optional[ArchiveIn] = None, user=Depends(require_editor)) -> dict:
    """Hide a script from the board and the lists (or bring it back). Nothing is deleted."""
    script = _script_or_404(script_id)
    flag = body.archived if body else True
    if flag and script["status"] in ("RESEARCHING", "WRITING"):
        raise HTTPException(409, detail=ApiError.make("busy", "文稿还在研究或写稿，完成后才能归档"))
    fields = {"archived": 1 if flag else 0}
    if not flag and script["topic"].get("content_archive"):
        topic = dict(script["topic"])
        topic["restored_content_archive"] = topic.pop("content_archive")
        fields.update(topic=topic, error=None)
    store.update_script(script_id, **fields)
    if flag:
        from core.video import autopilot_store

        autopilot_store.cancel_open_items(script_id, "文稿已归档，不再继续")  # no "waiting for you" left behind
    audit.log("video_script_archive", user=user["username"], target=script_id, result="archived" if flag else "restored")
    return get_script(script_id, user=user)


@router.get("/scripts/{script_id}")
def get_script(script_id: str, version: Optional[int] = None, user=Depends(require_viewer)) -> dict:
    from web.services.video_configuration import context
    from web.services import platform_manager, distributions, topic_progress
    script = _script_or_404(script_id)
    shown = version or script["current_version"]
    current = store.get_version(script_id, shown) if shown else None
    if version and current is None:
        raise _not_found("这个版本")
    column = _known_columns().get(script["column_key"])
    if current is not None and column is not None:
        current = {**current, "checks": video_workflow.rule_checks(current, column)}
    return {
        **script,
        "selection_skip": topic_progress.selection_skips([script]).get(script_id),
        "credibility": credibility.label(current, auditing=bool(script.get("stage"))) if current else None,
        "auto": script.get("created_by") == "autopilot",
        "column": column.as_dict() if column else None,
        "configuration": context(script, current, get_settings().paths.config_dir, get_settings().paths.jobs_dir),
        "version_shown": current,
        "versions": store.list_versions(script_id),
        "renders": [_render_out(r) for r in store.list_renders(script_id)],
        "publications": store.list_publications(script_id),
        "platform_uploads": platform_manager.script_uploads(script_id),
        "distributions": distributions.overview([script])[script_id],
        "rechecks": store.list_rechecks(script_id)[:5],
        # research → write → audit → approve → render, read off the records above; and who started what
        "steps": video_executor.steps_of(script),
        "events": video_steps.events(script_id),
        # the plan waiting for the user, when the recipe pauses after the plan (writing.pause_after_plan)
        "pending_plan": _plan_out(video_plans.pending(script_id)) if script["status"] == "PLAN_READY" else None,
    }


@router.get('/scripts/{script_id}/compare')
def compare_script(script_id: str, from_version: int = Query(ge=1), to_version: int = Query(ge=1),
                   before_render: str = '', after_render: str = '', user=Depends(require_viewer)) -> dict:
    """Compare saved script versions and their actual videos; never starts a generation."""
    from web.services.video_comparison import compare
    return compare(_script_or_404(script_id), from_version, to_version,
                   [_render_out(r) for r in store.list_renders(script_id)], before_render, after_render)


def _plan_out(row: Optional[dict]) -> Optional[dict]:
    if row is None:
        return None
    return {k: row[k] for k in ("id", "plan", "problems", "style", "note", "created_by", "created_at")}


@router.post("/scripts/{script_id}/steps/{step}/rerun", status_code=202)
def rerun_step(script_id: str, step: str, user=Depends(require_editor)) -> dict:
    """Run one step again (spec/video-steps.md): resume a failed research, rewrite from the same report, rerun the
    audit, or render the approved version. A finished research is never redone here (it costs searches)."""
    _script_or_404(script_id)
    try:
        video_executor.rerun(script_id, step, user["username"])
    except video_executor.StepError as exc:
        raise HTTPException(409, detail=ApiError.make("step_not_runnable", str(exc))) from exc
    audit.log("video_step_rerun", user=user["username"], target=script_id, result=step)
    return get_script(script_id, user=user)


class PlanBeatEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(default=None, max_length=80)
    point: Optional[str] = Field(default=None, max_length=400)


class PlanConfirmIn(BaseModel):
    """The words the user changed; anything left out stays as the plan has it. ``beats``: one entry per beat."""
    model_config = ConfigDict(extra="forbid")

    question: Optional[str] = Field(default=None, max_length=200)
    core_message: Optional[str] = Field(default=None, max_length=100)
    hook_idea: Optional[str] = Field(default=None, max_length=400)
    beats: Optional[list[PlanBeatEdit]] = None


class PlanRedoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(min_length=1, max_length=2000)


def _plan_action(script_id: str, user: dict, action: str, run) -> dict:
    _script_or_404(script_id)
    try:
        run()
    except video_executor.StepError as exc:
        raise HTTPException(409, detail=ApiError.make("plan_not_waiting", str(exc))) from exc
    audit.log(f"video_plan_{action}", user=user["username"], target=script_id, result="ok")
    return get_script(script_id, user=user)


@router.post("/scripts/{script_id}/plan/confirm", status_code=202)
def confirm_plan(script_id: str, body: Optional[PlanConfirmIn] = None, user=Depends(require_editor)) -> dict:
    """Confirm the waiting plan (spec/video-steps.md), maybe reworded, and write the draft from it. Costs the writing
    model."""
    edits = body.model_dump(exclude_none=True) if body else {}
    if "beats" in edits:
        edits["beats"] = [b.model_dump() for b in body.beats]
    return _plan_action(script_id, user, "confirm",
                        lambda: video_executor.confirm_plan(script_id, user["username"], edits))


@router.post("/scripts/{script_id}/plan/redo", status_code=202)
def redo_plan(script_id: str, body: PlanRedoIn, user=Depends(require_editor)) -> dict:
    """Make the plan again with the user's note. Costs the writing model."""
    return _plan_action(script_id, user, "redo", lambda: video_executor.redo_plan(script_id, user["username"], body.note))


@router.post("/scripts/{script_id}/plan/discard")
def discard_plan(script_id: str, user=Depends(require_editor)) -> dict:
    """Drop the waiting plan; the script goes back to what it was."""
    return _plan_action(script_id, user, "discard", lambda: video_executor.discard_plan(script_id, user["username"]))


class ParagraphIn(BaseModel):
    text: str
    citations: list[str] = Field(default_factory=list)
    evidence: Optional[str] = None
    note: Optional[str] = None
    facts: list[str] = Field(default_factory=list)
    introduces: list[str] = Field(default_factory=list)
    recap: bool = False


class SectionIn(BaseModel):
    title: str
    paragraphs: list[ParagraphIn]


class ScriptEditIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=2, max_length=80)
    sections: list[SectionIn] = Field(min_length=1)
    #: The version the edit started from; its facts, plan and style carry over. Current by default.
    base_version: Optional[int] = None


@router.put("/scripts/{script_id}")
def edit_script(script_id: str, body: ScriptEditIn, user=Depends(require_editor)) -> dict:
    """Save an edit as a new version. An approved script goes back to DRAFT."""
    script = _script_or_404(script_id)
    if script["status"] not in ("DRAFT", "APPROVED"):
        raise HTTPException(409, detail=ApiError.make("not_editable", f"文稿当前是 {script['status']}，还不能编辑"))
    if body.base_version is not None and store.get_version(script_id, body.base_version) is None:
        raise _not_found("这个版本")
    version = video_workflow.save_edit(
        script_id, title=body.title, sections=[s.model_dump() for s in body.sections],
        edited_by=user["username"], base_version=body.base_version,
    )
    store.update_script(script_id, status="DRAFT")
    return get_script(script_id, version=version, user=user)


class RestyleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    depth: Optional[str] = None
    tone: Optional[str] = None


@router.post("/scripts/{script_id}/restyle", status_code=202)
def restyle_script(script_id: str, body: Optional[RestyleIn] = None, user=Depends(require_editor)) -> dict:
    """Write a new version in another style from the same research (the column's default style if none given)."""
    _script_or_404(script_id)
    try:
        script = video_workflow.restyle(script_id, depth=body.depth if body else None,
                                        tone=body.tone if body else None, started_by=user["username"])
    except video_styles.StyleError as exc:
        raise HTTPException(422, detail=ApiError.make("bad_style", str(exc))) from exc
    except video_workflow.RestyleError as exc:
        raise HTTPException(409, detail=ApiError.make("not_restylable", str(exc))) from exc
    audit.log("video_script_restyle", user=user["username"], target=script_id,
              result=f"{body.depth if body else ''}/{body.tone if body else ''}")
    return _script_summary(script)


@router.get("/scripts/{script_id}/facts")
def get_facts(script_id: str, version: Optional[int] = None, user=Depends(require_viewer)) -> dict:
    """The fact sheet a version was adapted from."""
    script = _script_or_404(script_id)
    number = version or script["current_version"]
    shown = store.get_version(script_id, number) if number else None
    sheet_id = ((shown or {}).get("meta") or {}).get("fact_sheet_id")
    row = store.get_fact_sheet(sheet_id) if sheet_id else None
    if row is None:
        raise _not_found("这个版本的事实底稿")
    return {"id": row["id"], "report_path": row["report_path"], "created_at": row["created_at"], **row["sheet"]}


@router.get("/scripts/{script_id}/publish")
def get_publish_pack(script_id: str, version: Optional[int] = None, user=Depends(require_viewer)) -> dict:
    """Title, description, chapters, tags and cover text for uploading a version (the approved one by default)."""
    script = _script_or_404(script_id)
    number = version or script["approved_version"] or script["current_version"]
    shown = store.get_version(script_id, number) if number else None
    if shown is None:
        raise _not_found("这个版本")
    column = _known_columns().get(script["column_key"])
    if column is None and not script['topic'].get('local_video'):
        raise HTTPException(422, detail=ApiError.make("unknown_column", f"没有这个栏目：{script['column_key']}"))
    return video_render.publish_pack(script, shown, column)


class PublicationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=8, max_length=500)
    published_on: Optional[str] = Field(default=None, description="YYYY-MM-DD; today by default")
    version: Optional[int] = None
    render_id: Optional[str] = None
    platform: str = Field(default="bilibili", pattern=r"^[a-z0-9_-]{1,20}$")
    note: str = Field(default="", max_length=500)
    account_uid: str = Field(default="", max_length=300)


_BILIBILI_HOSTS = ("bilibili.com", "b23.tv")


def _publication_fields(body: PublicationIn, script: dict) -> dict:
    from datetime import date, datetime, timedelta

    from core.video.publication_identity import identify
    from web.services import distributions

    try:
        _, canonical = identify(body.platform, body.url, strict=True)
    except ValueError as exc:
        raise HTTPException(422, detail=ApiError.make("bad_url", str(exc))) from exc
    profile = distributions.accounts().get(body.platform, {})
    if body.account_uid and body.account_uid != profile.get("uid"):
        raise HTTPException(422, detail=ApiError.make("account_changed", "平台账号已改变，请刷新后核对。"))
    if body.published_on is None:
        published_on = date.today().isoformat()
    else:
        try:
            day = datetime.strptime(body.published_on, "%Y-%m-%d").date()
        except ValueError as exc:
            raise HTTPException(422, detail=ApiError.make("bad_date", "发布日期应为 YYYY-MM-DD")) from exc
        if day > date.today() + timedelta(days=1):
            raise HTTPException(422, detail=ApiError.make("bad_date", "发布日期不能在未来"))
        published_on = body.published_on
    render = None
    if body.render_id is not None:
        render = store.get_render(body.render_id)
        if render is None or render["script_id"] != script["id"]:
            raise _not_found("这次出片")
    version = body.version or (render["version"] if render else script["approved_version"])
    if version is not None and store.get_version(script["id"], version) is None:
        raise _not_found("这个版本")
    if render and render["version"] != version:
        raise HTTPException(422, detail=ApiError.make("version_mismatch", "发布记录的文稿版本与所选成片不一致。"))
    return {"url": canonical, "published_on": published_on, "version": version, "render_id": body.render_id,
            "platform": body.platform, "note": body.note.strip(), "account_uid": body.account_uid,
            "account_name": profile.get("name", "") if body.account_uid else ""}


@router.post("/scripts/{script_id}/publications", status_code=201)
def add_publication(script_id: str, body: PublicationIn, user=Depends(require_editor)) -> dict:
    """Record where a script's video was published (a link and a date); nothing is posted anywhere."""
    script = _script_or_404(script_id)
    fields = _publication_fields(body, script)
    shown = store.get_version(script_id, fields["version"]) if fields["version"] else None
    try:
        made = store.add_publication(script_id, title=(shown or {}).get("title", ""), recorded_by=user["username"], **fields)
    except ValueError as exc:
        raise HTTPException(422, detail=ApiError.make("publication_conflict", str(exc))) from exc
    audit.log("video_publication_add", user=user["username"], target=script_id, result=fields["url"][:120])
    return made


@router.delete("/scripts/{script_id}/publications/{publication_id}")
def delete_publication(script_id: str, publication_id: str, user=Depends(require_editor)) -> dict:
    _script_or_404(script_id)
    found = store.get_publication(publication_id)
    if found is None or found["script_id"] != script_id or not store.delete_publication(publication_id):
        raise _not_found("这条发布记录")
    audit.log("video_publication_delete", user=user["username"], target=script_id, result=publication_id)
    return {"deleted": publication_id}


@router.post("/scripts/{script_id}/recheck", status_code=202)
def recheck_script(script_id: str, user=Depends(require_editor)) -> dict:
    """Search again for changes since a flash script was written (its approved version); reports, never blocks."""
    from web.services import video_recheck

    script = _script_or_404(script_id)
    column = _known_columns().get(script["column_key"])
    if column is None or not column.recheck:
        raise HTTPException(409, detail=ApiError.make("no_recheck", "这个栏目不需要发布前复核"))
    if script["status"] != "APPROVED" or not script["approved_version"]:
        raise HTTPException(409, detail=ApiError.make("not_approved", "请先定稿再复核"))
    version = store.get_version(script_id, script["approved_version"])
    try:
        made = video_recheck.start(script, version, started_by=user["username"])
    except store.ActiveRecheckError as exc:
        raise HTTPException(409, detail=ApiError.make("recheck_running", str(exc))) from exc
    audit.log("video_recheck", user=user["username"], target=script_id, result=f"v{version['version']}")
    return made


class ApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Optional[int] = None


@router.post("/scripts/{script_id}/approve")
def approve_script(script_id: str, body: Optional[ApproveIn] = None, user=Depends(require_editor)) -> dict:
    """定稿: lock a version (the current one by default) as what gets voiced."""
    script = _script_or_404(script_id)
    if script["status"] not in ("DRAFT", "APPROVED"):
        raise HTTPException(409, detail=ApiError.make("not_ready", f"文稿当前是 {script['status']}，还不能定稿"))
    version = (body.version if body else None) or script["current_version"]
    if store.get_version(script_id, version) is None:
        raise _not_found("这个版本")
    try:
        video_executor.approve(script_id, version, user["username"])
    except video_executor.StepError as exc:
        raise HTTPException(409, detail=ApiError.make("approve_not_allowed", str(exc))) from exc
    audit.log("video_script_approve", user=user["username"], target=script_id, result=f"v{version}")
    return get_script(script_id, user=user)


class RenderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice_id: Optional[str] = None
    production_mode: Optional[Literal["standard", "fast"]] = None


@router.post("/scripts/{script_id}/render", status_code=202)
def render_script(script_id: str, body: Optional[RenderIn] = None, user=Depends(require_editor)) -> dict:
    """Hand the approved version to AutoVideo."""
    script = _script_or_404(script_id)
    if script["status"] != "APPROVED":
        raise HTTPException(409, detail=ApiError.make("not_approved", "请先定稿再出片"))
    try:
        render = video_executor.render(script_id, user["username"], voice_id=body.voice_id if body else None,
                                       **({"production_mode": body.production_mode} if body and body.production_mode is not None else {}))
    except video_executor.StepError as exc:
        raise HTTPException(409, detail=ApiError.make("render_not_allowed", str(exc))) from exc
    audit.log("video_render", user=user["username"], target=script_id, result=render["id"])
    return _render_out(render)


class VideoModelIn(BaseModel):
    """The model that writes a video's storyboard in AutoVideo. The key is only ever named, never sent: AutoVideo reads it
    from this app's ``.env`` by its variable name."""

    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(min_length=1, max_length=300)
    model: str = Field(min_length=1, max_length=120)
    key_env: str = Field(default="", max_length=80)
    fallback_models: list[str] = Field(default_factory=list, max_length=8)

    def settings(self) -> dict:
        return {**self.model_dump(), "env_file": config_routes._env_path() if self.key_env else ""}


def _autovideo_call(call):
    try:
        return call(video_render.get_client())
    except AutoVideoUnreachable as exc:
        raise HTTPException(503, detail=ApiError.make("autovideo_unreachable", str(exc))) from exc
    except AutoVideoError as exc:
        raise HTTPException(exc.status_code if exc.status_code in (404, 422) else 502,
                            detail=ApiError.make(exc.code.lower(), exc.message or exc.code)) from exc


@router.get("/model")
def get_video_model(user=Depends(require_admin)) -> dict:
    """The provider and model AutoVideo uses now (and whether its key is found), for the settings page."""
    client = video_render.get_client()
    out = {"base_url": client.base_url, "reachable": False, "config": None, "error": None}
    try:
        out["config"] = client.llm_config()
        out["reachable"] = True
    except Exception as exc:  # noqa: BLE001 - the settings page shows why it is empty
        out["error"] = str(exc)
    return out


@router.put("/model")
def put_video_model(body: VideoModelIn, user=Depends(require_admin)) -> dict:
    """Choose the provider/model for videos rendered from now on (a render already running keeps what it started with)."""
    config = _autovideo_call(lambda c: c.set_llm_config(body.settings()))
    audit.log("video_model_update", user=user["username"], target=body.base_url, result=body.model)
    return config


@router.delete("/model")
def reset_video_model(user=Depends(require_admin)) -> dict:
    """Back to AutoVideo's own default (what its ``.env`` says)."""
    config = _autovideo_call(lambda c: c.reset_llm_config())
    audit.log("video_model_reset", user=user["username"])
    return config


@router.post("/model/test")
def test_video_model(body: VideoModelIn, user=Depends(require_admin)) -> dict:
    """One tiny request to the chosen provider, without saving it."""
    return _autovideo_call(lambda c: c.test_llm_config(body.settings()))


@router.get("/autovideo")
def autovideo_status(user=Depends(require_viewer)) -> dict:
    """Whether AutoVideo can take a job now, and which voices it has."""
    client = video_render.get_client()
    out = {"base_url": client.base_url, "reachable": False, "ready": False, "blockers": [], "voices": [], "error": None}
    try:
        client.health()
        out["reachable"] = True
        config = client.config_status()
        out["ready"] = bool(config.get("ready"))
        out["blockers"] = list(config.get("blockers") or [])
        out["voices"] = [{"id": v.get("id"), "label": v.get("label") or v.get("id")}
                         for v in client.voices() if v.get("available")]
    except Exception as exc:  # noqa: BLE001 - status is advisory
        out["error"] = str(exc)
    return out


@router.get("/renders/{render_id}")
def get_render(render_id: str, user=Depends(require_viewer)) -> dict:
    render = store.get_render(render_id)
    if render is None:
        raise _not_found("出片记录")
    return _render_out(render)


@router.post("/renders/{render_id}/reveal")
def reveal_render_file(render_id: str, name: str = "video.mp4", user=Depends(require_editor)) -> dict:
    """Open the folder holding a finished video on the machine running this app, so it can be uploaded by hand."""
    render = store.get_render(render_id)
    if render is None:
        raise _not_found("出片记录")
    try:
        shown = video_render.reveal(render, name)
    except FileNotFoundError:
        raise _not_found("文件") from None
    audit.log("video_render_reveal", user=user["username"], target=render_id, details={"name": name, "opened": shown["opened"]})
    return shown


@router.get("/renders/{render_id}/files/{name}")
def get_render_file(render_id: str, name: str, user=Depends(require_viewer)) -> FileResponse:
    render = store.get_render(render_id)
    if render is None:
        raise _not_found("出片记录")
    path = video_render.safe_file(render, name)
    if path is None:
        raise _not_found("文件")
    content_type = next((a.get("content_type") for a in render["artifacts"] if a.get("name") == name), None)
    return FileResponse(path, media_type=content_type, filename=name)


# --- Unattended pipeline -------------------------------------------------------


class AutopilotPick(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: Optional[str] = None
    cell: Optional[str] = None  # a matrix cell: its column and topic source come from the matrix
    count: int = Field(default=1, ge=1, le=10)  # explicit manual selections only
    all: bool = False
    source: str = Field(default="flash", pattern="^(flash|opportunities)$")

    @model_validator(mode="after")
    def _one_of(self):
        if bool(self.column) == bool(self.cell):
            raise ValueError("column 和 cell 二选一")
        return self


class AutopilotSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    picks: list[AutopilotPick] = Field(min_length=1, max_length=6)
    refresh: bool = Field(default=True, description="Run today's flash discovery first when it is not there yet")


class AutopilotRunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: Optional[AutopilotSpec] = None


class AutopilotPlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    expression: str = Field(description="Five-field cron in the host's timezone, e.g. '0 6 * * 1' for Mondays 06:00")
    spec: Optional[AutopilotSpec] = None


@router.get("/autopilot")
def autopilot_overview(user=Depends(require_viewer)) -> dict:
    """Weekly plans and recent batches, each item with where it stands."""
    from core.video import autopilot_store

    runs = autopilot_store.list_runs(limit=10)
    from web.services import video_queue_state, video_autopilot
    queue = video_queue_state.snapshot(runs)
    ids = [item["script_id"] for run in runs for item in run.get("items", []) if item.get("script_id")]
    versions = store.current_versions(ids)
    with store.connect() as conn:
        auditing = {row["id"] for row in conn.execute("SELECT id FROM scripts WHERE COALESCE(stage, '') != ''")}
    for run in runs:  # the label as it is now (an edit or a finished audit changes it), not the one at approval time
        for item in run.get("items", []):
            item['progress'] = queue['items'].get(item['id'])
            current = versions.get(item.get("script_id"))
            if current:
                item["credibility"] = credibility.label(current, auditing=item["script_id"] in auditing)
    return {"plans": autopilot_store.list_plans(), "runs": runs,
            "queue": {**queue['summary'], 'limits': {'scripts': video_autopilot.MAX_SCRIPTS,
                                                     'renders': video_autopilot.MAX_RENDERS}}}


@router.post("/autopilot/runs", status_code=202)
def autopilot_start(body: Optional[AutopilotRunIn] = None, user=Depends(require_editor)) -> dict:
    """Start one batch now (the default one: 3 flash + 2 one-point). Stops at a finished render; never publishes."""
    from web.services import video_autopilot

    spec = body.spec.model_dump(exclude_none=True) if body and body.spec else None
    try:
        run = video_autopilot.start_run(spec, started_by=user["username"])
    except Exception as exc:  # noqa: BLE001 - an unknown cell
        raise HTTPException(422, detail=ApiError.make("bad_spec", str(exc))) from exc
    audit.log("video_autopilot_run", user=user["username"], target=run["id"], result="started")
    return run


@router.post("/autopilot/runs/{run_id}/cancel")
def autopilot_cancel(run_id: str, user=Depends(require_editor)) -> dict:
    """Stop advancing a batch. Work already running (a render) finishes on its own."""
    from core.video import autopilot_store

    run = autopilot_store.get_run(run_id)
    if run is None:
        raise _not_found("这个批次")
    for item in run["items"]:
        if item["state"] in ("PENDING", "WORKING"):
            autopilot_store.update_item(item["id"], state="CANCELLED", note="已取消")
    autopilot_store.update_run(run_id, status="CANCELLED")
    audit.log("video_autopilot_cancel", user=user["username"], target=run_id, result="cancelled")
    return autopilot_store.get_run(run_id)


@router.post("/autopilot/plans", status_code=201)
def autopilot_save_plan(body: AutopilotPlanIn, user=Depends(require_editor)) -> dict:
    from web.services import video_autopilot

    try:
        plan = video_autopilot.save_plan(body.name, body.expression, body.spec.model_dump(exclude_none=True) if body.spec else None,
                                         created_by=user["username"])
    except ValueError as exc:
        raise HTTPException(422, detail=ApiError.make("bad_schedule", str(exc))) from exc
    audit.log("video_autopilot_plan", user=user["username"], target=plan["id"], result=body.expression)
    return plan


class AutopilotPlanSpecIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: AutopilotSpec


@router.put("/autopilot/plans/{plan_id}/spec")
def autopilot_edit_plan_spec(plan_id: str, body: AutopilotPlanSpecIn, user=Depends(require_editor)) -> dict:
    """Change what the weekly plan writes (cells and counts); the schedule stays."""
    from core.video import autopilot_store
    from web.services import video_autopilot

    if autopilot_store.get_plan(plan_id) is None:
        raise _not_found("这个计划")
    spec = body.spec.model_dump(exclude_none=True)
    try:
        for pick in spec["picks"]:
            video_autopilot.resolve_pick(pick)
    except ValueError as exc:
        raise HTTPException(422, detail=ApiError.make("bad_spec", str(exc))) from exc
    autopilot_store.update_plan(plan_id, spec=spec)
    audit.log("video_autopilot_plan_spec", user=user["username"], target=plan_id, result=json.dumps(spec, ensure_ascii=False))
    return autopilot_store.get_plan(plan_id)


@router.delete("/autopilot/plans/{plan_id}")
def autopilot_delete_plan(plan_id: str, user=Depends(require_editor)) -> dict:
    from core.video import autopilot_store

    if not autopilot_store.delete_plan(plan_id):
        raise _not_found("这个计划")
    audit.log("video_autopilot_plan_delete", user=user["username"], target=plan_id, result="deleted")
    return {"deleted": plan_id}


# --- Reference videos ------------------------------------------------------------


@router.get("/references")
def reference_videos(q: str = Query(min_length=4, max_length=120), limit: int = Query(default=8, ge=1, le=20),
                     user=Depends(require_viewer)) -> dict:
    """Other people's videos on a topic (B 站, YouTube) found by a web search: titles and links only, nothing
    downloaded. Costs one search run."""
    from core.config import load_system_config
    from core.search import SearchClient, SearchConfig
    from core.video import reference

    client = SearchClient(SearchConfig.from_system(load_system_config(get_settings().paths.config_dir)))
    from core.errors import SearchUnavailableError

    try:
        videos = reference.find(client, q, limit=limit)
    except SearchUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"query": q, "videos": videos, "searches": client.calls}


class DistributionDraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    render_id: str = Field(min_length=1, max_length=64)
    account_uid: str = Field(min_length=1, max_length=300)
    payload: dict
    previous_payload: dict | None = None


@router.put("/distributions/{platform}/draft")
def save_distribution_draft(platform: str, body: DistributionDraftIn, user=Depends(require_editor)) -> dict:
    from web.services import distributions, platform_manager
    from core.social.adapter import AdapterError
    try:
        if platform not in distributions.PLATFORMS:
            raise ValueError("这个平台尚未接入。")
        result = distributions.save_draft(body.render_id, platform, body.account_uid, body.payload, body.previous_payload)
    except (ValueError, platform_manager.Error, AdapterError) as exc:
        raise HTTPException(409, detail=ApiError.make("draft_unavailable", str(exc))) from exc
    audit.log("platform_draft_save", user=user["username"], target=platform, result=body.render_id)
    return result


class DistributionAdaptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    render_id: str = Field(min_length=1, max_length=64)
    payload: dict


@router.post("/distributions/{platform}/adapt")
def adapt_distribution(platform: str, body: DistributionAdaptIn, user=Depends(require_editor)) -> dict:
    from web.services import distributions, platform_manager
    from core.social.adapter import AdapterError
    try:
        if platform not in distributions.PLATFORMS:
            raise ValueError("这个平台尚未接入。")
        return distributions.adapt_preview(body.render_id, platform, body.payload)
    except (ValueError, platform_manager.Error, AdapterError) as exc:
        raise HTTPException(409, detail=ApiError.make("adaptation_unavailable", str(exc))) from exc


@router.get("/distributions/{platform}/history/{render_id}")
def distribution_history(platform: str, render_id: str, user=Depends(require_editor)) -> dict:
    from web.services import distributions, platform_manager
    from core.social.adapter import AdapterError
    try:
        if platform not in distributions.PLATFORMS:
            raise ValueError("这个平台尚未接入。")
        return distributions.history(render_id, platform)
    except (ValueError, platform_manager.Error, AdapterError) as exc:
        raise HTTPException(409, detail=ApiError.make("draft_unavailable", str(exc))) from exc

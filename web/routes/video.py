"""Video scripts API: topics → scripts → review → approval → AutoVideo renders.

See spec/video-scripts.md and, for styles and restyling, spec/video-adaptation.md.
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from core.video import columns as video_columns
from core.video import flash as video_flash
from core.video import store
from core.video import topics as video_topics
from core.video.adapt import styles as video_styles
from web import audit
from web.deps import require_editor, require_viewer
from web.models import ApiError
from web.services import video_render, video_workflow
from web.settings import get_settings

router = APIRouter(prefix="/api/video", tags=["video"])


def _not_found(what: str) -> HTTPException:
    return HTTPException(404, detail=ApiError.make("not_found", f"{what}不存在"))


def _columns() -> dict:
    try:
        return video_columns.load(get_settings().paths.config_dir)
    except video_columns.ColumnError as exc:
        raise HTTPException(500, detail=ApiError.make("bad_column_config", str(exc))) from exc


def _script_or_404(script_id: str) -> dict:
    script = store.get_script(script_id)
    if script is None:
        raise _not_found("文稿")
    return script


def _render_out(render: dict) -> dict:
    client = video_render.get_client()
    return {
        **{k: render[k] for k in (
            "id", "script_id", "version", "status", "title", "autovideo_job_id", "active_stage",
            "attempt_count", "resolved_video_form", "error_code", "error_message",
            "submitted_at", "completed_at", "created_at", "updated_at")},
        "source_chars": len(render["source_text"]),
        "autovideo_url": client.job_page_url(render["autovideo_job_id"]) if render["autovideo_job_id"] else None,
        "files": [
            {**a, "url": f"/api/video/renders/{render['id']}/files/{a['name']}"}
            for a in render.get("artifacts") or []
        ],
    }


def _script_summary(script: dict) -> dict:
    current = store.get_version(script["id"], script["current_version"]) if script["current_version"] else None
    return {
        **script,
        "title": current["title"] if current else script["topic"].get("title", ""),
        "length_check": current["length_check"] if current else None,
    }


# --- Columns & topics ----------------------------------------------------------


@router.get("/columns")
def list_columns(user=Depends(require_viewer)) -> list[dict]:
    from core.video import matrix

    config_dir = get_settings().paths.config_dir
    return [{**c.as_dict(), "status": (matrix.provenance(config_dir, c.key) or {}).get("status", "enabled")}
            for c in _columns().values()]


@router.get("/cells")
def list_cells(user=Depends(require_viewer)) -> list[dict]:
    """Every cell of the content matrix with its status, for choosing what to write (spec/video-matrix.md)."""
    from core.video import matrix

    try:
        return matrix.catalog(get_settings().paths.config_dir)
    except video_columns.ColumnError as exc:
        raise HTTPException(500, detail=ApiError.make("bad_matrix", str(exc))) from exc


@router.get("/styles")
def list_styles(user=Depends(require_viewer)) -> dict:
    """Depths and tones a script can be written in."""
    try:
        return video_styles.catalog(get_settings().paths.config_dir)
    except video_styles.StyleError as exc:
        raise HTTPException(500, detail=ApiError.make("bad_style_config", str(exc))) from exc


@router.get("/topics")
def list_topics(round: Optional[str] = None, source: str = "opportunities",
                user=Depends(require_viewer)) -> dict:
    """Topics of a round (the latest by default): content opportunities, or ``source=flash`` for flash-news items."""
    if source not in ("opportunities", "flash"):
        raise HTTPException(422, detail=ApiError.make("unknown_source", "source 应为 opportunities 或 flash"))
    loader = video_flash.load if source == "flash" else video_topics.load
    round_date, topics = loader(get_settings().paths.output_dir, round)
    used = store.topic_ids_in_use()
    return {
        "round": round_date,
        "topics": [{**t.as_dict(), "scripted": t.id in used} for t in topics],
    }


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
    topic_id: Optional[str] = Field(default=None, description="A content-opportunity topic id, e.g. 2026-09-29/C3")
    topic: Optional[TopicIn] = None
    topic_ids: list[str] = Field(
        default_factory=list, description="A roundup's items: flash-news topic ids (F<n>) of one discovery report")
    video_form: Optional[str] = Field(
        default=None, description="One of the column's allowed forms; the column's default when left out")
    from_script: Optional[str] = Field(
        default=None, description="Write from this script's research report instead of researching again")


def _roundup_topic(body: ScriptCreateIn) -> dict:
    if body.topic_id is not None or not body.topic_ids:
        raise HTTPException(422, detail=ApiError.make("topic_required", "盘点要选 2–10 条快讯条目（topic_ids）"))
    output_dir = get_settings().paths.output_dir
    found = [video_topics.find(output_dir, tid) for tid in body.topic_ids]
    missing = [tid for tid, item in zip(body.topic_ids, found) if item is None]
    if missing or any(not item.code.startswith("F") for item in found):
        raise HTTPException(422, detail=ApiError.make("unknown_topic", f"找不到快讯条目：{', '.join(missing) or '只能选快讯条目'}"))
    if len({item.round for item in found}) != 1 or len(set(body.topic_ids)) != len(body.topic_ids):
        raise HTTPException(422, detail=ApiError.make("roundup_items", "盘点的条目要来自同一份发现报告，且不重复"))
    report = os.path.relpath(video_flash.report_path(output_dir, found[0].round), os.path.dirname(output_dir))
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
    if body.column not in _columns():
        raise HTTPException(422, detail=ApiError.make("unknown_column", f"没有这个栏目：{body.column}"))
    chosen = _columns()[body.column]
    if body.video_form is not None and body.video_form not in (chosen.allowed_forms or (chosen.video_form,)):
        raise HTTPException(422, detail=ApiError.make(
            "form_not_allowed", f"这个栏目只能用这些视频形式：{', '.join(chosen.allowed_forms or (chosen.video_form,))}"))
    kind = chosen.topic_kind
    if kind == "roundup":
        topic = _roundup_topic(body)
    elif kind == "comparison":
        topic = _comparison_topic(body)
    else:
        if body.topic_ids or (body.topic is not None and (body.topic.subjects or body.topic.dimensions)):
            raise HTTPException(422, detail=ApiError.make("topic_not_for_column", "这个栏目的选题不用条目列表或对比对象"))
        if (body.topic_id is None) == (body.topic is None):
            raise HTTPException(422, detail=ApiError.make("topic_required", "请选择一个内容机会，或填写一个选题"))
        if body.topic is not None and len(body.topic.title.strip()) < 4:
            raise HTTPException(422, detail=ApiError.make("topic_required", "选题标题至少 4 个字"))
        if body.topic_id is not None:
            found = video_topics.find(get_settings().paths.output_dir, body.topic_id)
            if found is None:
                raise HTTPException(422, detail=ApiError.make("unknown_topic", f"找不到选题 {body.topic_id}"))
            topic = found.as_dict()
        else:
            topic = {"id": None, "title": body.topic.title.strip(),
                     "fields": {"核心角度": body.topic.angle.strip(), "目标受众": body.topic.audience.strip()}}
    if body.video_form is not None:
        topic = {**topic, "video_form": body.video_form}
    if body.from_script is not None:
        parent = store.get_script(body.from_script)
        if parent is None or not parent.get("report_path"):
            raise HTTPException(422, detail=ApiError.make("unknown_parent", "找不到这篇文稿，或它还没有研究报告"))
        topic = {**topic, "from_report": parent["report_path"], "parent_script": parent["id"]}
    script = video_workflow.start(body.column, topic, started_by=user["username"])
    audit.log("video_script_create", user=user["username"], target=script["id"], result="started",
              ip=request.client.host if request.client else None)
    return _script_summary(script)


@router.get("/scripts")
def list_scripts(limit: int = 100, user=Depends(require_viewer)) -> list[dict]:
    return [_script_summary(s) for s in store.list_scripts(limit=min(max(limit, 1), 500))]


@router.get("/scripts/{script_id}")
def get_script(script_id: str, version: Optional[int] = None, user=Depends(require_viewer)) -> dict:
    script = _script_or_404(script_id)
    shown = version or script["current_version"]
    current = store.get_version(script_id, shown) if shown else None
    if version and current is None:
        raise _not_found("这个版本")
    column = _columns().get(script["column_key"])
    if current is not None and column is not None:
        current = {**current, "checks": video_workflow.rule_checks(current, column)}
    return {
        **script,
        "column": column.as_dict() if column else None,
        "version_shown": current,
        "versions": store.list_versions(script_id),
        "renders": [_render_out(r) for r in store.list_renders(script_id)],
    }


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
    column = _columns().get(script["column_key"])
    if column is None:
        raise HTTPException(422, detail=ApiError.make("unknown_column", f"没有这个栏目：{script['column_key']}"))
    return video_render.publish_pack(script, shown, column)


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
    store.update_script(script_id, status="APPROVED", approved_version=version)
    audit.log("video_script_approve", user=user["username"], target=script_id, result=f"v{version}")
    return get_script(script_id, user=user)


class RenderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice_id: Optional[str] = None


@router.post("/scripts/{script_id}/render", status_code=202)
def render_script(script_id: str, body: Optional[RenderIn] = None, user=Depends(require_editor)) -> dict:
    """Hand the approved version to AutoVideo."""
    script = _script_or_404(script_id)
    if script["status"] != "APPROVED":
        raise HTTPException(409, detail=ApiError.make("not_approved", "请先定稿再出片"))
    try:
        render = video_render.create(script, voice_id=body.voice_id if body else None)
    except video_render.RenderError as exc:
        raise HTTPException(409, detail=ApiError.make("render_not_allowed", str(exc))) from exc
    video_render.start(render["id"])
    audit.log("video_render", user=user["username"], target=script_id, result=render["id"])
    return _render_out(render)


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

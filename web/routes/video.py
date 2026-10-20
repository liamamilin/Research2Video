"""Video scripts API: topics → scripts → review → approval → AutoVideo renders.

See spec/video-scripts.md and, for styles and restyling, spec/video-adaptation.md.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from fastapi import Query, APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.video import columns as video_columns
from core.video import flash as video_flash
from core.video import credibility, store
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


def _script_summary(script: dict, published: Optional[set] = None) -> dict:
    current = store.get_version(script["id"], script["current_version"]) if script["current_version"] else None
    return {
        **script,
        "credibility": credibility.label(current, auditing=bool(script.get("stage"))) if current else None,
        "auto": script.get("created_by") == "autopilot",
        "title": current["title"] if current else script["topic"].get("title", ""),
        "length_check": current["length_check"] if current else None,
        "published": script["id"] in (published if published is not None else store.published_script_ids()),
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


@router.get("/coverage")
def cell_coverage(user=Depends(require_viewer)) -> list[dict]:
    """Per cell: topic sources, topics waiting and already taken (the content matrix view of /topics)."""
    from web.services import video_autopilot

    return video_autopilot.coverage()


@router.get("/board")
def home_board(user=Depends(require_viewer)) -> dict:
    """Counts per pipeline stage and what needs the user, for the home page."""
    from web.services import video_board

    return video_board.board(_columns())


@router.get("/shipments")
def shipments(user=Depends(require_viewer)) -> list[dict]:
    """Approved scripts with their render and publication state, for the 成片与发布 page."""
    from web.services import video_board

    return video_board.shipments(_columns())


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


@router.get("/topics")
def list_topics(round: Optional[str] = None, source: str = "opportunities",
                user=Depends(require_viewer)) -> dict:
    """Topics of a round (the latest by default): content opportunities, or ``source=flash`` for flash-news items."""
    if source not in ("opportunities", "flash"):
        raise HTTPException(422, detail=ApiError.make("unknown_source", "source 应为 opportunities 或 flash"))
    loader = video_flash.load if source == "flash" else video_topics.load
    out = get_settings().paths.output_dir
    round_date, topics = loader(out, round)
    used = store.topic_ids_in_use()
    published = store.published_topic_ids()
    covered = store.scripts_by_topic()
    dismissed = store.dismissed_topic_ids()
    return {
        "round": round_date,
        "rounds": (video_flash.report_dates(out) if source == "flash" else video_topics.round_dates(out)),
        "topics": [{**t.as_dict(), "scripted": t.id in used, "published": t.id in published,
                    "dismissed": t.id in dismissed, "scripts": covered.get(t.id, [])} for t in topics],
    }


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


@router.get("/topics/discovery")
def discovery_status(user=Depends(require_viewer)) -> dict:
    """The flash discovery job: running now, and when its newest report came out."""
    from web.services import video_autopilot

    return video_autopilot.discovery_status()


@router.post("/topics/discovery", status_code=202)
def start_discovery(user=Depends(require_editor)) -> dict:
    """Start today's flash discovery (about 20 searches). Refused while it is already running."""
    from web.services import video_autopilot

    if not video_autopilot.start_discovery_if_idle():
        raise HTTPException(409, detail=ApiError.make("discovery_running", "快讯发现正在跑，等它跑完"))
    audit.log("video_discovery_start", user=user["username"], target="ai_flash_discovery", result="started")
    return video_autopilot.discovery_status()


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
def list_scripts(limit: int = 100, archived: bool = False, user=Depends(require_viewer)) -> list[dict]:
    published = store.published_script_ids()
    return [_script_summary(s, published)
            for s in store.list_scripts(limit=min(max(limit, 1), 500), archived=archived)]


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
    store.update_script(script_id, archived=1 if flag else 0)
    audit.log("video_script_archive", user=user["username"], target=script_id, result="archived" if flag else "restored")
    return get_script(script_id, user=user)


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
        "credibility": credibility.label(current, auditing=bool(script.get("stage"))) if current else None,
        "auto": script.get("created_by") == "autopilot",
        "column": column.as_dict() if column else None,
        "version_shown": current,
        "versions": store.list_versions(script_id),
        "renders": [_render_out(r) for r in store.list_renders(script_id)],
        "publications": store.list_publications(script_id),
        "rechecks": store.list_rechecks(script_id)[:5],
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


class PublicationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=8, max_length=500)
    published_on: Optional[str] = Field(default=None, description="YYYY-MM-DD; today by default")
    version: Optional[int] = None
    render_id: Optional[str] = None
    platform: str = Field(default="bilibili", pattern=r"^[a-z0-9_-]{1,20}$")
    note: str = Field(default="", max_length=500)


_BILIBILI_HOSTS = ("bilibili.com", "b23.tv")


def _publication_fields(body: PublicationIn, script: dict) -> dict:
    from datetime import date, datetime, timedelta
    from urllib.parse import urlsplit

    parts = urlsplit(body.url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        raise HTTPException(422, detail=ApiError.make("bad_url", "链接应以 http:// 或 https:// 开头"))
    if body.platform == "bilibili" and not any(host == h or host.endswith("." + h) for h in _BILIBILI_HOSTS):
        raise HTTPException(422, detail=ApiError.make("bad_url", "B 站的链接应在 bilibili.com 或 b23.tv 下"))
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
    version = body.version or script["approved_version"]
    if version is not None and store.get_version(script["id"], version) is None:
        raise _not_found("这个版本")
    if body.render_id is not None:
        render = store.get_render(body.render_id)
        if render is None or render["script_id"] != script["id"]:
            raise _not_found("这次出片")
    return {"url": body.url.strip(), "published_on": published_on, "version": version, "render_id": body.render_id,
            "platform": body.platform, "note": body.note.strip()}


@router.post("/scripts/{script_id}/publications", status_code=201)
def add_publication(script_id: str, body: PublicationIn, user=Depends(require_editor)) -> dict:
    """Record where a script's video was published (a link and a date); nothing is posted anywhere."""
    script = _script_or_404(script_id)
    fields = _publication_fields(body, script)
    shown = store.get_version(script_id, fields["version"]) if fields["version"] else None
    made = store.add_publication(script_id, title=(shown or {}).get("title", ""), recorded_by=user["username"], **fields)
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
    column = _columns().get(script["column_key"])
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


# --- Unattended pipeline -------------------------------------------------------


class AutopilotPick(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: Optional[str] = None
    cell: Optional[str] = None  # a matrix cell: its column and topic source come from the matrix
    count: int = Field(default=1, ge=1, le=10)
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
    for run in runs:  # the label as it is now (an edit or a finished audit changes it), not the one at approval time
        for item in run.get("items", []):
            script = store.get_script(item["script_id"]) if item.get("script_id") else None
            current = store.get_version(script["id"], script["current_version"]) if script and script["current_version"] else None
            if current:
                item["credibility"] = credibility.label(current, auditing=bool(script.get("stage")))
    return {"plans": autopilot_store.list_plans(), "runs": runs}


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
    return {"query": q, "videos": reference.find(client, q, limit=limit), "searches": client.calls}

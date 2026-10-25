"""Public-page captures, local previews, and explicit paragraph assignments."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from core.video import screenshots, sources, store
from core.video.autovideo import AutoVideoError, AutoVideoUnreachable
from web import audit
from web.deps import require_editor, require_viewer
from web.models import ApiError
from web.services import video_render, video_workflow

router = APIRouter()


def invalid(exc: Exception, status: int = 422) -> HTTPException:
    return HTTPException(status, detail=ApiError.make("screenshot_invalid", str(exc)))


def _version(script_id: str, version: int, *, writable: bool = False) -> tuple[dict, dict]:
    script = store.get_script(script_id)
    current = store.get_version(script_id, version) if script else None
    if not script or not current:
        raise HTTPException(404, detail=ApiError.make("not_found", "文稿版本不存在"))
    if writable and (script["current_version"] != version or script.get("stage")
                     or script["status"] not in {"DRAFT", "APPROVED"}):
        raise invalid(screenshots.ScreenshotError("请在当前文稿完成后编辑配图，历史版本只读"), 409)
    return script, current


def _decision_out(decision: dict) -> dict:
    result = {k: decision[k] for k in ("need_id", "status", "reason", "screenshot_id", "cached") if k in decision}
    vision = (((decision.get("quality") or {}).get("image") or {}).get("vision") or {})
    if vision.get("state") == "passed":
        result["understanding"] = {k: vision.get(k) for k in ("summary", "paragraph_ids", "focus_reason")}
    return result


@router.get("/scripts/{script_id}/screenshots")
def overview(script_id: str, version: int = Query(ge=1), user=Depends(require_viewer)):
    script, current = _version(script_id, version)
    listed = []
    try:
        if script.get("topic", {}).get("article"):
            from web.services.video_articles import source_of
            source = source_of(script)
            if source.get("source_url"):
                listed.append({"url": source["source_url"], "title": source["title"]})
        else:
            listed.extend(sources.report_sources(video_workflow.report_of(script), fallback=True))
    except (OSError, KeyError, TypeError, ValueError):
        pass
    for section in current["sections"]:
        for paragraph in section["paragraphs"]:
            listed.extend({"url": url, "title": url} for url in paragraph.get("citations", []) if url.startswith(("http://", "https://")))
    unique = {row["url"]: row for row in reversed(listed)}
    try:
        selected = screenshots.selection(script_id, current)
        problem = ""
    except screenshots.ScreenshotError as exc:
        selected = {"form": "object_showcase", "materials": []}
        problem = str(exc)
    from core.video.visual_materials import summary
    automatic = store.latest_render_visuals(script_id, version)
    if automatic and automatic.get("chapter_hash") != screenshots.chapter_hash(current):
        automatic = None
    if automatic and not screenshots.has_selection(script_id, version) and automatic.get("selection", {}).get("materials"):
        selected = {**automatic["selection"], "origin": "automatic"}
    return {"sources": list(reversed(list(unique.values()))), "assets": screenshots.assets(script_id),
            "selection": selected, "chapters": screenshots.chapters(current), "problem": problem,
            "automation_enabled": bool((video_render._video_config().get("visual_materials") or {}).get("enabled")),
            "automatic": {**summary(automatic), "render_id": automatic["render_id"],
                          "decisions": [_decision_out(d) for d in automatic.get("decisions", [])]} if automatic else None}


class Clip(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: int = Field(ge=0, le=1440)
    y: int = Field(ge=0, le=900)
    width: int = Field(ge=128, le=1440)
    height: int = Field(ge=128, le=900)


class CaptureIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    version: int = Field(ge=1)
    url: str = Field(min_length=1, max_length=2048)
    mode: Literal["viewport", "full_page", "element", "crop"] = "viewport"
    selector: str = Field(default="", max_length=500)
    clip: Clip | None = None


@router.post("/scripts/{script_id}/screenshots", status_code=201)
def capture(script_id: str, body: CaptureIn, user=Depends(require_editor)):
    _version(script_id, body.version, writable=True)
    try:
        raw, meta = screenshots.capture(body.url, mode=body.mode, selector=body.selector,
                                        clip=body.clip.model_dump() if body.clip else None)
        record = screenshots.save(script_id, body.version, raw, meta, created_by=user["username"])
    except screenshots.ScreenshotError as exc:
        raise invalid(exc) from exc
    # Saving the acquired picture precedes registration. An unavailable engine
    # must not discard the user's screenshot or force a fresh page capture.
    problem = ""
    try:
        record = screenshots.register(script_id, record["id"], video_render.get_client())
    except (AutoVideoError, AutoVideoUnreachable):
        problem = "截图已保存，视频引擎登记失败，请点击重新登记。"
    audit.log("video_screenshot_capture", user=user["username"], target=script_id, result=record["id"])
    return {"asset": record, "problem": problem}


@router.post("/scripts/{script_id}/screenshots/{asset_id}/register")
def register(script_id: str, asset_id: str, version: int = Query(ge=1), user=Depends(require_editor)):
    _version(script_id, version, writable=True)
    try:
        return screenshots.register(script_id, asset_id, video_render.get_client(), force=True)
    except screenshots.ScreenshotError as exc:
        raise invalid(exc) from exc
    except (AutoVideoError, AutoVideoUnreachable) as exc:
        raise invalid(screenshots.ScreenshotError("视频引擎暂时无法登记图片，截图已保留，请稍后重试"), 503) from exc


@router.get("/scripts/{script_id}/screenshots/{asset_id}/image")
def image(script_id: str, asset_id: str, user=Depends(require_viewer)):
    try:
        _, path = screenshots.load(script_id, asset_id)
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})
    except screenshots.ScreenshotError as exc:
        raise invalid(exc, 404) from exc


class Placement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chapter_index: int = Field(ge=0)
    paragraph_index: int = Field(ge=0)


class Material(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    screenshot_id: str
    caption: str = Field(default="", max_length=80)
    role: Literal["reference", "overall", "detail", "comparison"] = "reference"
    placements: list[Placement] = Field(min_length=1, max_length=100)


class SelectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    form: Literal["object_showcase", "dynamic_collage"] = "object_showcase"
    materials: list[Material] = Field(default_factory=list, max_length=12)


@router.put("/scripts/{script_id}/image-materials")
def select(script_id: str, body: SelectionIn, user=Depends(require_editor)):
    _, current = _version(script_id, body.version, writable=True)
    try:
        result = screenshots.select(script_id, current, body.form, [row.model_dump() for row in body.materials],
                                    video_render.get_client().base_url)
    except screenshots.ScreenshotError as exc:
        raise invalid(exc) from exc
    audit.log("video_image_materials_save", user=user["username"], target=script_id, result=f"v{body.version}")
    return result

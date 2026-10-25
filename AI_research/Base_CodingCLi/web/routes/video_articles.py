"""Authenticated article imports and creation; no synthetic research reports."""
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from core.video import articles, production, store
from web import audit
from web.deps import require_editor, require_viewer
from web.models import ApiError
from web.settings import get_settings

router = APIRouter()


def invalid(exc: Exception) -> HTTPException:
    return HTTPException(422, detail=ApiError.make("article_invalid", str(exc)))


@router.post("/articles/import", status_code=201)
async def import_article(file: UploadFile = File(...), user=Depends(require_editor)):
    try:
        raw = await file.read(articles.MAX_BYTES + 1)
        return articles.save_import(get_settings().paths.state_dir, file.filename or "article.txt", raw)
    except articles.ArticleError as exc:
        raise invalid(exc) from exc
    finally:
        await file.close()


class CreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=articles.MAX_CHARS)
    title: str = Field(default="", max_length=80)
    mode: Literal["read", "adapt"]
    kind: Literal["literary", "information"]
    import_id: str | None = None
    column: str = "topic_digest"
    production: str = ""
    author: str = Field(default="", max_length=80)
    source_url: str = Field(default="", max_length=300)
    published_on: str = ""
    origin: Literal["original", "copied", "ai"] = "copied"


@router.post("/articles/scripts", status_code=202)
def create_article(body: CreateIn, user=Depends(require_editor)):
    from web.routes.video import _creatable, _script_summary
    from web.services import video_executor
    paths = get_settings().paths
    config = production.get(paths.config_dir, paths.jobs_dir, body.production) if body.production else None
    if body.production and (not config or not config.usable):
        raise invalid(articles.ArticleError("所选矩阵视频配置不可用，请重新选择"))
    chosen = _creatable(config.recipe if config else body.column)
    try:
        source = articles.snapshot(paths.state_dir, body=body.text, title=body.title, import_id=body.import_id,
                                   author=body.author, source_url=body.source_url, published_on=body.published_on,
                                   origin=body.origin)
    except articles.ArticleError as exc:
        raise invalid(exc) from exc
    topic = {"title": source["title"], "fields": {"目标受众": chosen.audience or "普通观众"},
             "article": {"source": source, "mode": body.mode, "kind": body.kind},
             **({"article_production": config.key} if config else {})}
    script = video_executor.create(chosen.key, topic, user["username"])
    audit.log("video_article_create", user=user["username"], target=script["id"], result=body.mode)
    return _script_summary(store.get_script(script["id"]))


@router.get("/scripts/{script_id}/article")
def get_article(script_id: str, user=Depends(require_viewer)):
    from web.services.video_articles import source_of
    script = store.get_script(script_id)
    if not script or not script["topic"].get("article"):
        raise HTTPException(404, detail=ApiError.make("not_found", "这篇文稿没有文章原文"))
    try:
        source = source_of(script)
        imported = articles.read_import(get_settings().paths.state_dir, source["import_id"])
        return {**source, "mode": script["topic"]["article"]["mode"], "kind": script["topic"]["article"]["kind"],
                "extracted_text": imported["text"], "filename": imported["filename"], "warnings": imported["warnings"],
                "original_url": f"/api/video/scripts/{script_id}/article/original"}
    except articles.ArticleError as exc:
        raise invalid(exc) from exc


@router.get("/scripts/{script_id}/article/original")
def download_article(script_id: str, user=Depends(require_viewer)):
    source = get_article(script_id, user)
    imported = articles.read_import(get_settings().paths.state_dir, source["import_id"])
    return FileResponse(imported["original_path"], filename=imported["filename"], media_type="application/octet-stream")

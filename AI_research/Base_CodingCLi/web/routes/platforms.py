"""Authenticated publishing, explicit replies, and opt-in post-publication comments."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from web import audit
from web.deps import require_admin, require_editor, require_viewer
from web.models import ApiError
from web.services import bilibili, platform_manager as manager
from web.services import platform_analytics as analytics

router = APIRouter(prefix="/api/platforms/bilibili", tags=["platforms"])


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CredentialIn(Input):
    credentials: dict


class FirstCommentIn(Input):
    enabled: bool = False
    text: str = Field(default="", max_length=1000)
    pin: bool = False


class SettingsIn(Input):
    auto_upload: bool
    sync_comments: bool
    tid: int = Field(ge=0, le=65535)
    first_comment: FirstCommentIn = Field(default_factory=FirstCommentIn)


class UploadIn(Input):
    render_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(max_length=2000)
    tags: list[str] = Field(min_length=1, max_length=10)
    tid: int = Field(ge=1, le=65535)
    first_comment: FirstCommentIn = Field(default_factory=FirstCommentIn)


class VideoIn(Input):
    bvid: str = Field(pattern=r"^BV[0-9A-Za-z]{10}$")


class SyncIn(VideoIn):
    page: int = Field(default=1, ge=1, le=1000)
    root: str = Field(default="", pattern=r"^[0-9]*$")


class CommentIn(Input):
    draft: str = Field(default="", max_length=1000)
    handled: bool = False


class ReplyIn(Input):
    text: str = Field(min_length=1, max_length=1000)


def call(action, *args, **kwargs):
    try:
        return action(*args, **kwargs)
    except bilibili.BilibiliError as exc:
        raise HTTPException(409, detail=ApiError.make("platform_unavailable", str(exc))) from exc


@router.get("")
def overview(user=Depends(require_viewer)) -> dict:
    return call(manager.overview)


class AnalyticsSettingsIn(Input):
    enabled: bool


@router.get("/analytics")
def analytics_cache(period: int = Query(default=7), q: str = Query(default="", max_length=200),
                    scope: str = Query(default="all", pattern="^(all|workflow)$"),
                    matrix: str = Query(default="", max_length=200),
                    sort: str = Query(default="date", pattern="^(date|view|favorite_rate)$"),
                    page: int = Query(default=1, ge=1, le=10000), size: int = Query(default=25, ge=1, le=100),
                    user=Depends(require_viewer)) -> dict:
    if period not in (7, 30):
        raise HTTPException(422, detail=ApiError.make("invalid_period", "仅支持近7天或近30天。"))
    return call(analytics.cached, period=period, query=q, scope=scope, matrix=matrix, sort=sort, page=page, size=size)


@router.get("/analytics/videos/{bvid}")
def analytics_detail(bvid: str, days: int = Query(default=30), user=Depends(require_viewer)) -> dict:
    if not bilibili.BV.fullmatch(bvid) or days not in (7, 30):
        raise HTTPException(422, detail=ApiError.make("invalid_video", "请使用有效的BV号和7/30天范围。"))
    return call(analytics.detail, bvid, days)


@router.post("/analytics/sync", status_code=202)
def analytics_sync(user=Depends(require_editor)) -> dict:
    return call(analytics.request_sync)


@router.put("/analytics/settings")
def analytics_settings(body: AnalyticsSettingsIn, user=Depends(require_admin)) -> dict:
    try:
        result = call(analytics.set_enabled, body.enabled)
    except (ValueError, RuntimeError, OSError) as exc:
        raise HTTPException(409, detail=ApiError.make("analytics_unavailable", "采集设置未能完成，请检查独立调度器状态。")) from exc
    audit.log("platform_analytics_settings", user=user["username"], target="bilibili", result=str(body.enabled))
    return result


@router.get("/categories")
def categories(user=Depends(require_viewer)) -> dict:
    return call(manager.categories)


@router.post("/login")
def login(user=Depends(require_admin)) -> dict:
    return call(bilibili.start_login, manager.directory() / "login.json")


@router.post("/login/poll")
def login_poll(user=Depends(require_admin)) -> dict:
    result = call(bilibili.poll_login, manager.directory() / "login.json")
    if result["status"] == "done":
        call(manager.connect, result.pop("credentials"))
        (manager.directory() / "login.json").unlink(missing_ok=True)
        audit.log("platform_connect", user=user["username"], target="bilibili")
    return result


@router.post("/credentials")
def import_credentials(body: CredentialIn, user=Depends(require_admin)) -> dict:
    result = call(manager.connect, body.credentials)
    audit.log("platform_connect", user=user["username"], target="bilibili")
    return result


@router.post("/verify")
def verify(user=Depends(require_admin)) -> dict:
    return call(manager.verify)


@router.delete("/account")
def disconnect(user=Depends(require_admin)) -> dict:
    call(manager.disconnect)
    audit.log("platform_disconnect", user=user["username"], target="bilibili")
    return {"connected": False}


@router.put("/settings")
def settings(body: SettingsIn, user=Depends(require_admin)) -> dict:
    if body.auto_upload:
        call(manager.uid)
        if body.tid < 1 or not bilibili.biliup_binary():
            raise HTTPException(409, detail=ApiError.make("platform_not_ready", "自动投稿需要连接账号、安装 biliup 并选择默认投稿分区。"))
    patch = body.model_dump(exclude_unset=True)
    if "first_comment" in patch:
        if body.first_comment.enabled:
            call(manager.uid)
        patch["first_comment"] = call(manager.clean_first_comment, body.first_comment.model_dump())
    result = manager.store().save_settings(patch)
    audit.log("platform_settings", user=user["username"], target="bilibili",
              result=str({k: v for k, v in patch.items() if k != "first_comment"}))
    return result


@router.get("/preview/{render_id}")
def preview(render_id: str, user=Depends(require_editor)) -> dict:
    return call(manager.preview, render_id)


@router.put("/first-comment-template")
def first_comment_template(body: FirstCommentIn, user=Depends(require_admin)) -> dict:
    result = call(manager.save_first_comment_template, body.model_dump())
    audit.log("platform_first_comment_template", user=user["username"], target="bilibili")
    return result


@router.post("/uploads", status_code=202)
def enqueue(body: UploadIn, user=Depends(require_editor)) -> dict:
    result = call(manager.enqueue, body.render_id, body.model_dump(exclude={"render_id"}))
    audit.log("platform_enqueue", user=user["username"], target=result["id"])
    return result


@router.post("/uploads/{task_id}/cancel")
def cancel(task_id: str, user=Depends(require_editor)) -> dict:
    task = manager.store().get_upload(task_id)
    if not task or task["uid"] != call(manager.uid) or not manager.store().update_upload(task_id, status="CANCELLED", expected="QUEUED"):
        raise HTTPException(409, detail=ApiError.make("cannot_cancel", "只能取消当前账号还在排队的投稿。"))
    return {"status": "CANCELLED"}


@router.get("/uploads/{task_id}")
def upload_task(task_id: str, user=Depends(require_viewer)) -> dict:
    return call(manager.upload_task, task_id)


@router.post("/uploads/{task_id}/reconcile")
def reconcile(task_id: str, body: VideoIn, user=Depends(require_editor)) -> dict:
    return call(manager.reconcile, task_id, body.bvid)


@router.post("/uploads/{task_id}/retry")
def retry(task_id: str, user=Depends(require_editor)) -> dict:
    task = manager.store().get_upload(task_id)
    if not task or task["uid"] != call(manager.uid) or task["status"] not in ("FAILED", "CANCELLED"):
        raise HTTPException(409, detail=ApiError.make("cannot_retry", "只有投稿前检查失败或已取消排队的任务可以重试。"))
    call(manager.eligible, task["render_id"])
    manager.store().update_upload(task_id, status="QUEUED", expected=task["status"])
    return manager.store().get_upload(task_id)


@router.post("/uploads/{task_id}/first-comment/retry")
def retry_first_comment(task_id: str, user=Depends(require_editor)) -> dict:
    result = call(manager.retry_first_comment, task_id)
    audit.log("platform_first_comment_retry", user=user["username"], target=task_id)
    return result


@router.post("/uploads/{task_id}/first-comment/pin/retry")
def retry_first_comment_pin(task_id: str, user=Depends(require_editor)) -> dict:
    result = call(manager.retry_first_comment, task_id, pin=True)
    audit.log("platform_first_comment_pin_retry", user=user["username"], target=task_id)
    return result


@router.post("/videos")
def watch(body: VideoIn, user=Depends(require_editor)) -> dict:
    return call(manager.watch, body.bvid)


@router.post("/sync")
def sync(body: SyncIn, user=Depends(require_editor)) -> dict:
    return call(manager.sync, body.bvid, body.page, body.root)


@router.put("/comments/{bvid}/{rpid}")
def edit(bvid: str, rpid: str, body: CommentIn, user=Depends(require_editor)) -> dict:
    owner = call(manager.uid)
    db = manager.store()
    if not db.get_comment(owner, bvid, rpid):
        raise HTTPException(404, detail=ApiError.make("not_found", "评论不存在"))
    db.edit_comment(owner, bvid, rpid, draft=body.draft, handled=body.handled)
    return db.get_comment(owner, bvid, rpid)


@router.post("/comments/{bvid}/{rpid}/reply")
def reply(bvid: str, rpid: str, body: ReplyIn, user=Depends(require_editor)) -> dict:
    if not body.text.strip():
        raise HTTPException(422, detail=ApiError.make("empty_reply", "回复不能为空。"))
    result = call(manager.send_reply, bvid, rpid, body.text.strip())
    audit.log("platform_reply", user=user["username"], target=f"{bvid}/{rpid}", result=result["reply_rpid"])
    return result

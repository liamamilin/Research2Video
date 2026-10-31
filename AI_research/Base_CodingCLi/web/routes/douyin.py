"""Douyin operations use the console's existing roles and CSRF protection."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from core.douyin import DouyinError
from web import audit
from web.deps import require_admin, require_editor, require_viewer
from web.models import ApiError
from web.services import douyin

router = APIRouter(prefix="/api/platforms/douyin", tags=["platforms"])


def call(function, *args):
    try:
        return function(*args)
    except (DouyinError, douyin.platform_manager.Error) as exc:
        raise HTTPException(409, detail=ApiError.make("douyin_unavailable", str(exc))) from exc


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FirstCommentIn(Input):
    enabled: bool = False
    text: str = Field(default="", max_length=500)
    pin: bool = False


class UploadIn(Input):
    first_comment: FirstCommentIn = Field(default_factory=FirstCommentIn)
    account_uid: str = Field(default="", max_length=64)
    render_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=30)
    description: str = Field(default="", max_length=1000)
    tags: list[str] = Field(default_factory=list, max_length=5)


class ReconcileIn(Input):
    video: str = Field(default="", max_length=500)


@router.get("")
def overview(user=Depends(require_viewer)):
    return call(douyin.overview)


@router.post("/login", status_code=202)
def login(user=Depends(require_admin)):
    result = call(douyin.start_login)
    audit.log("platform_login", user=user["username"], target="douyin", result="started")
    return result


@router.get("/login/{key}")
def login_status(key: str, user=Depends(require_admin)):
    return call(douyin.login_status, key)


@router.post("/verify", status_code=202)
def verify(user=Depends(require_admin)):
    return call(douyin.verify)


@router.delete("/account")
def disconnect(user=Depends(require_admin)):
    result = call(douyin.disconnect)
    audit.log("platform_disconnect", user=user["username"], target="douyin")
    return result


@router.get("/preview/{render_id}")
def preview(render_id: str, user=Depends(require_editor)):
    return call(douyin.preview, render_id)


@router.post("/uploads", status_code=202)
def enqueue(body: UploadIn, user=Depends(require_editor)):
    payload = body.model_dump(exclude={"render_id", "first_comment"})
    if 'first_comment' in body.model_fields_set:
        payload['first_comment'] = body.first_comment.model_dump(exclude_unset=True)
    result = call(douyin.enqueue, body.render_id, payload)
    audit.log("platform_enqueue", user=user["username"], target="douyin", result=result["id"])
    return result


@router.get("/uploads/{key}")
def task(key: str, user=Depends(require_viewer)):
    return douyin.public_job(call(douyin.upload_task, key))


@router.post("/uploads/{key}/reconcile", status_code=202)
def reconcile(key: str, body: ReconcileIn, user=Depends(require_editor)):
    result = call(douyin.reconcile, key, body.video)
    audit.log("platform_reconcile", user=user["username"], target="douyin", result=key)
    return result


@router.post("/uploads/{key}/cancel")
def cancel(key: str, user=Depends(require_editor)):
    return call(douyin.action, key, "cancel")


@router.post("/uploads/{key}/retry", status_code=202)
def retry(key: str, user=Depends(require_editor)):
    return call(douyin.action, key, "retry")


@router.put('/first-comment-template')
def first_comment_template(body: FirstCommentIn, user=Depends(require_admin)):
    result = call(douyin.save_first_comment_template, body.model_dump(exclude_unset=True))
    audit.log('platform_comment_template', user=user['username'], target='douyin')
    return result


@router.post('/uploads/{key}/first-comment', status_code=202)
def add_first_comment(key: str, body: FirstCommentIn, user=Depends(require_editor)):
    result = call(douyin.add_first_comment, key, body.model_dump(exclude_unset=True))
    audit.log('platform_first_comment_add', user=user['username'], target='douyin', result=key)
    return result


@router.post('/uploads/{key}/first-comment/{action}')
def first_comment_action(key: str, action: str, user=Depends(require_editor)):
    result = call(douyin.first_comment_action, key, action)
    audit.log('platform_first_comment_action', user=user['username'], target='douyin', result=key + ':' + action)
    return result

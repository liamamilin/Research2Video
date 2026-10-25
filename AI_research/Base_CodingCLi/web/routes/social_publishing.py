"""Shared role/CSRF boundaries for independently scoped platform adapters."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from core.social.adapter import AdapterError
from core.social.registry import PLATFORMS, DEFERRED_PLATFORMS, active_platforms, definition
from web import audit
from web.deps import require_admin, require_editor, require_viewer
from web.models import ApiError
from web.services import social_publishing as service

router=APIRouter(prefix='/api/platforms',tags=['platforms'])


def call(platform, function, *args):
    if platform not in PLATFORMS:
        raise HTTPException(404,detail=ApiError.make('unknown_platform','这个平台尚未接入。'))
    if platform in DEFERRED_PLATFORMS:
        raise HTTPException(409,detail=ApiError.make('platform_deferred','这个平台已暂缓使用，入口已隐藏。'))
    try:
        return function(platform,*args)
    except (AdapterError,service.platform_manager.Error,ValueError) as exc:
        raise HTTPException(409,detail=ApiError.make('platform_unavailable',str(exc))) from exc


class Input(BaseModel):
    model_config=ConfigDict(extra='forbid')


class UploadIn(Input):
    render_id:str=Field(min_length=1,max_length=64)
    account_uid:str=Field(min_length=1,max_length=300)
    title:str=Field(min_length=1,max_length=500)
    description:str=Field(default='',max_length=10000)
    tags:list[str]=Field(default_factory=list,max_length=30)
    first_comment:dict | None=None


class FirstCommentIn(Input):
    enabled:bool=Field(default=False,strict=True)
    text:str=Field(default='',max_length=250)
    pin:bool=Field(default=False,strict=True)


class ReconcileIn(Input):
    video:str=Field(default='',max_length=1000)


class ConfirmAbsentIn(Input):
    confirmed_absent:bool=Field(strict=True)
    account_uid:str=Field(min_length=1,max_length=300)
    expected_updated_at:float=Field(gt=0,allow_inf_nan=False)


@router.get('/catalog')
def catalog(user=Depends(require_viewer)):
    return {'platforms':[{'id':'bilibili','name':'哔哩哔哩（B站）'},{'id':'douyin','name':'抖音'}]+[definition(key) for key in active_platforms()]}


@router.get('/social/{platform}')
def overview(platform:str,user=Depends(require_viewer)):
    return call(platform,service.overview)


@router.post('/social/{platform}/login',status_code=202)
def login(platform:str,user=Depends(require_admin)):
    result=call(platform,service.start_login)
    audit.log('platform_login',user=user['username'],target=platform,result='started')
    return result


@router.post('/social/{platform}/login/renew',status_code=202)
def renew_login(platform:str,user=Depends(require_admin)):
    result=call(platform,service.start_login,True)
    audit.log('platform_login_renew',user=user['username'],target=platform,result='started')
    return result


@router.get('/social/{platform}/login/{key}')
def login_status(platform:str,key:str,user=Depends(require_admin)):
    return call(platform,service.login_status,key)


@router.post('/social/{platform}/comments/login',status_code=202)
def comment_login(platform:str,user=Depends(require_admin)):
    result=call(platform,service.start_comment_login)
    audit.log('platform_comment_login',user=user['username'],target=platform,result='started')
    return result


@router.post('/social/{platform}/comments/login/renew',status_code=202)
def renew_comment_login(platform:str,user=Depends(require_admin)):
    return call(platform,service.start_comment_login,True)


@router.post('/social/{platform}/verify',status_code=202)
def verify(platform:str,user=Depends(require_admin)):
    return call(platform,service.verify)


@router.delete('/social/{platform}/account')
def disconnect(platform:str,user=Depends(require_admin)):
    result=call(platform,service.disconnect)
    audit.log('platform_disconnect',user=user['username'],target=platform)
    return result


@router.get('/social/{platform}/preview/{render_id}')
def preview(platform:str,render_id:str,user=Depends(require_editor)):
    return call(platform,service.preview,render_id)


@router.post('/social/{platform}/uploads',status_code=202)
def upload(platform:str,body:UploadIn,user=Depends(require_editor)):
    result=call(platform,service.enqueue,body.render_id,body.model_dump(exclude={'render_id'},exclude_none=True))
    audit.log('platform_enqueue',user=user['username'],target=platform,result=result['id'])
    return result


@router.put('/social/{platform}/first-comment-template')
def comment_template(platform:str,body:FirstCommentIn,user=Depends(require_admin)):
    return call(platform,service.save_first_comment_template,body.model_dump())


@router.post('/social/{platform}/uploads/{key}/first-comment',status_code=202)
def add_comment(platform:str,key:str,body:FirstCommentIn,user=Depends(require_editor)):
    result=call(platform,service.add_first_comment,key,body.model_dump())
    audit.log('platform_first_comment',user=user['username'],target=platform,result=key)
    return result


@router.post('/social/{platform}/uploads/{key}/first-comment/{action_name}')
def comment_action(platform:str,key:str,action_name:str,user=Depends(require_editor)):
    return call(platform,service.first_comment_action,key,action_name)


@router.get('/social/{platform}/uploads/{key}')
def task(platform:str,key:str,user=Depends(require_viewer)):
    return service.public_job(platform,call(platform,service.upload_task,key))


@router.post('/social/{platform}/uploads/{key}/reconcile',status_code=202)
def reconcile(platform:str,key:str,body:ReconcileIn,user=Depends(require_editor)):
    result=call(platform,service.reconcile,key,body.video)
    audit.log('platform_reconcile',user=user['username'],target=platform,result=key)
    return result


@router.post('/social/{platform}/uploads/{key}/confirm-absent')
def confirm_absent(platform:str,key:str,body:ConfirmAbsentIn,user=Depends(require_editor)):
    if body.confirmed_absent is not True:
        raise HTTPException(422,detail=ApiError.make('confirmation_required','请明确确认已在创作中心核对，没有这条作品。'))
    result=call(platform,service.confirm_absent,key,body.account_uid,body.expected_updated_at,user['username'])
    audit.log('platform_confirm_absent',user=user['username'],target=platform,result=key)
    return result


@router.post('/social/{platform}/uploads/{key}/{action}')
def action(platform:str,key:str,action:str,user=Depends(require_editor)):
    return call(platform,service.action,key,action)

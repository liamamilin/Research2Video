"""Authenticated streaming application harness and recovery endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Request, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from core.config import load_system_config
from core.llm import LLMClient, LLMConfig
from web import audit
from web.deps import require_viewer, require_editor
from web.services import jobs_assistant
from web.services.assistant_store import AssistantStore
from web.settings import get_settings
from web.services.assistant_workspace import Context
from web.services.local_video import LocalVideoRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/assistant", tags=["assistant"])


@router.post('/local-video')
def local_video(payload: LocalVideoRequest, request: Request, user=Depends(require_editor)):
    from web.services.local_video import execute
    try:
        result = execute(payload, request, user)
    except (ValueError, OSError, TimeoutError) as exc:
        raise HTTPException(409, str(exc)) from exc
    audit.log('local_video_' + payload.action, user=user['username'], target=result['render_id'],
              result=result.get('message') or '')
    return result


@router.get('/capabilities')
def capabilities(request: Request, user=Depends(require_viewer)):
    from web.services.assistant_tools.operations import capabilities as query
    return query(request, user)


@router.get('/overview')
def operations_overview(matrix: str = Query(default='', max_length=80),
                        platform: str = Query(default='', max_length=40),
                        view: Literal['attention', 'working', 'publish'] = 'attention',
                        offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=50),
                        user=Depends(require_viewer)):
    from web.services.assistant_overview import overview
    return overview(user, matrix=matrix, platform=platform, view=view, offset=offset, limit=limit)


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=24000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=8000)
    history: list[Message] = Field(default_factory=list, max_length=20)
    conversation_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex, pattern=r'^[a-zA-Z0-9_-]{8,80}$')
    context: Context | None = None


_active: dict[str, threading.Event] = {}
_active_lock = threading.Lock()


@router.get('/conversation')
def latest_conversation(user=Depends(require_viewer)):
    return AssistantStore().conversation(user)


@router.get('/conversations')
def list_conversations(user=Depends(require_viewer)):
    return AssistantStore().conversations(user)


@router.get('/workspace')
async def assistant_workspace(request: Request, conversation_id: str, context: str | None = None,
                              user=Depends(require_viewer)):
    from web.services.assistant_workspace import workspace
    try:
        parsed = json.loads(context) if context is not None else None
        selected = Context.model_validate(parsed).model_dump() if parsed is not None else None
        return await workspace(request, user, conversation_id, selected, explicit=context is not None)
    except ValueError as exc:
        raise HTTPException(422, '上下文或会话不可用：' + str(exc)) from exc


@router.get('/tasks')
async def assistant_tasks(request: Request, limit: int = Query(default=20, ge=1, le=100), user=Depends(require_viewer)):
    """Live research, video and publication work across this account's conversations."""
    from web.services.assistant_workspace import task_center
    return await task_center(request, user, limit)


@router.get('/conversations/{conversation_id}')
def read_conversation(conversation_id: str, user=Depends(require_viewer)):
    try:
        return AssistantStore().conversation(user, conversation_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post('/conversations')
def new_conversation(user=Depends(require_viewer)):
    return {'id': AssistantStore().new_conversation(user)}


class ContextChange(BaseModel):
    context: Context | None = None


@router.put('/conversations/{conversation_id}/context')
def change_context(conversation_id: str, payload: ContextChange, user=Depends(require_viewer)):
    try:
        AssistantStore().set_context(user, conversation_id, payload.context.model_dump() if payload.context else None)
        return {'ok': True}
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post('/requests/{request_id}/stop')
def stop_response(request_id: str, user=Depends(require_viewer)):
    try:
        item = AssistantStore().request(request_id, user)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    with _active_lock:
        token = _active.get(request_id)
        if token:
            token.set()
    return {'status': 'stop_requested' if token else item['status'],
            'message': '只停止助手响应；已启动的后台任务按业务状态继续。'}


@router.get('/inputs/{input_id}')
def input_status(input_id: str, user=Depends(require_viewer)):
    try:
        item = AssistantStore().input(input_id, user)
        return {'status': item['status'], 'result': json.loads(item['result']) if item['result'] else None}
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post('/inputs/{input_id}')
def submit_input(input_id: str, payload: dict, request: Request, user=Depends(require_viewer)):
    """Secrets bypass the LLM, chat persistence and ordinary operation records."""
    from fastapi import Response
    from web.deps import require_admin
    from web.routes import config, users
    from web.auth import routes as auth
    from web.services.assistant_safety import redact
    storage = AssistantStore()
    try:
        card = storage.input(input_id, user)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    kind, params = card['kind'], card['parameters']
    if kind in ('set_secret', 'set_headers', 'create_user', 'reset_password'):
        require_admin(request, user=user)
    if kind not in ('set_secret', 'set_headers', 'create_user', 'reset_password', 'change_password', 'create_token'):
        raise HTTPException(422, '此卡片不接受输入')
    required = {'set_secret': {'value'}, 'set_headers': {'value'}, 'create_user': {'password'}, 'reset_password': {'password'},
                'change_password': {'current_password', 'new_password'}, 'create_token': set()}[kind]
    if set(payload) != required or any(not isinstance(v, str) or len(v) > 12000 for v in payload.values()):
        raise HTTPException(422, '输入字段不符合此卡片的要求')
    if not storage.claim_input(input_id, user):
        raise HTTPException(409, '此卡片已提交，请查询状态；不要重复操作')
    try:
        if kind == 'set_secret':
            raw = config.update_secrets({'values': {params['env']: payload['value']}}, request, user=user)
            result = {'ok': raw['ok']}
        elif kind == 'set_headers':
            import re
            from web.services import assistant_configuration
            try:
                headers = json.loads(payload['value'])
            except ValueError as exc:
                raise HTTPException(422, '请求头必须是 JSON 对象') from exc
            if not isinstance(headers, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                    or not re.fullmatch(r'[A-Za-z0-9!#$%&\x27*+.^_`|~-]+', k) or '\n' in v or '\r' in v
                    for k, v in headers.items()):
                raise HTTPException(422, '请求头必须是合法名称到单行字符串的 JSON 对象')
            result = assistant_configuration.apply({}, params['expected_mtime'], request, user, private_headers=headers)
            if not result.get('saved'):
                raise HTTPException(422, result.get('message', '候选请求头验证未通过，未保存'))
        elif kind == 'create_user':
            result = users.create_user({'username': params['username'], 'role': params['role'], **payload}, request, user=user).model_dump(mode='json')
        elif kind == 'reset_password':
            result = users.update_user(params['user_id'], payload, request, user=user).model_dump(mode='json')
        elif kind == 'change_password':
            result = auth.change_password(payload, request, Response())
        else:
            result = auth.create_token({'name': params['token_name'], 'expires_days': params['expires_days']}, request)
        # Only the immediate HTTP caller receives the new token. Not in the
        # model context, request results, audit payload, or protected form DB.
        stored = {k: v for k, v in result.items() if k != 'token'}
        storage.finish_input(input_id, redact(stored))
        audit.log('assistant_protected_input', user=user['username'], target=kind, result='success')
        return result
    except HTTPException as exc:
        status = 'pending' if exc.status_code in (400, 401, 403, 404, 409, 422) else 'failed'
        storage.finish_input(input_id, {'error': '校验未通过，尚未完成操作'}, status)
        if exc.status_code == 401 and kind == 'change_password':
            raise HTTPException(400, exc.detail) from exc
        raise
    except Exception:
        storage.finish_input(input_id, {'error': '操作未完成，请核验实际状态后重新发起'}, 'failed')
        raise


@router.get('/inputs/{input_id}/verify')
def verify_input(input_id: str, request: Request, user=Depends(require_viewer)):
    """Read actual business metadata without replaying an uncertain secret action."""
    from web.deps import require_admin
    from web.auth import db, routes as auth
    from web.routes import config
    try:
        card = AssistantStore().input(input_id, user)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    kind, params = card['kind'], card['parameters']
    result = {'status': card['status'], 'kind': kind,
              'message': '仅核验当前业务状态；不会再次提交原操作。'}
    if kind == 'create_token':
        existing = set(params.get('existing_token_ids', []))
        result['tokens'] = [{k: token[k] for k in ('id', 'name', 'created_at', 'revoked')}
                            for token in auth.list_tokens(request)['tokens']
                            if token['name'] == params['token_name'][:60] and token['id'] not in existing]
        result['message'] = '以下为同名新 Token 的元数据，请核对创建时间。原值不能再次取出；需要时先撤销，再明确创建新的。'
    elif kind == 'change_password':
        result['message'] = '密码原值不能读取核验；可用新密码重新登录。需要重新修改时，请在账号设置中操作。'
    else:
        require_admin(request, user=user)
        if kind == 'set_secret':
            result['configured'] = any(item['env'] == params['env'] and item['configured'] for item in config.get_secrets(user=user)['secrets'])
            result['message'] = '只显示该密钥是否已配置，不读取原值，也不能据此断言本次提交已成功。'
        elif kind == 'set_headers':
            from web.services.assistant_configuration import recovery
            result['header_names'] = list((config._load_system().get('ai') or {}).get('extra_headers') or {})
            result.update(recovery(user))
            result['message'] = '仅核验当前请求头名称，不返回私密值；可在设置中测试连接或恢复上一笔配置。'
        elif kind in ('create_user', 'reset_password'):
            target = db.get_user_by_username(params['username']) if kind == 'create_user' else db.get_user_by_id(params['user_id'])
            result['user'] = {k: target[k] for k in ('id', 'username', 'role', 'disabled')} if target else None
            result['message'] = '核对当前用户状态；密码原值不能读取，是否已更改需通过登录或重新设置确认。'
        else:
            raise HTTPException(422, '此卡片不支持业务核验')
    return result


@router.post('/rollback/{rollback_id}')
def rollback_configuration(rollback_id: str, request: Request, user=Depends(require_viewer)):
    from web.services.assistant_configuration import rollback
    try:
        return rollback(rollback_id, request, user)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get('/configuration/recovery')
def configuration_recovery(request: Request, user=Depends(require_viewer)):
    from web.deps import require_admin
    from web.services.assistant_configuration import recovery
    require_admin(request, user=user)
    return recovery(user)


@router.get('/background/{task_id}')
def background_status(task_id: str, user=Depends(require_viewer)):
    from web.services import assistant_background
    try:
        return assistant_background.read(task_id, user)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get('/maintenance/{maintenance_id}')
def maintenance_status(maintenance_id: str, user=Depends(require_viewer)):
    from web.services.assistant_maintenance import receipt
    if user['role'] != 'admin':
        raise HTTPException(403, '需要管理员权限')
    try:
        return receipt(maintenance_id, user)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


class ServiceOperation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['restart', 'shutdown', 'rebind_gateway']
    active_runs: Literal['refuse', 'wait', 'cancel'] = 'refuse'


@router.get('/service')
def service_status(user=Depends(require_viewer)):
    if user['role'] != 'admin':
        raise HTTPException(403, '需要管理员权限')
    from web.services.assistant_maintenance import status
    return status()


@router.post('/service')
def service_operation(payload: ServiceOperation, request: Request, user=Depends(require_viewer)):
    from web.deps import require_admin
    from web.services.assistant_maintenance import start
    require_admin(request, user=user)
    try:
        return start(payload.action, payload.active_runs, request, user)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


def _llm(cancel_token=None):
    return LLMClient(LLMConfig.from_system(load_system_config(get_settings().paths.config_dir)),
                     cancel_token=cancel_token)


@router.post("")
def chat(payload: ChatRequest, request: Request, user=Depends(require_viewer)):
    """Manage Jobs using the current user's permissions and configured model."""
    return _respond(payload, request, user)


def _respond(payload, request, user, on_event=None, cancel_token=None):
    storage = AssistantStore()
    try:
        conversation = storage.conversation(user, payload.conversation_id)
        started, previous = storage.begin(user, payload.request_id, conversation['id'], payload.message)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    metadata = {'conversation_id': conversation['id'], 'request_id': payload.request_id}
    if not started:
        from web.services.assistant_delivery import canonical_links
        previous['answer'] = canonical_links(previous.get('answer') or '', conversation['refs'])
        return {**previous, **metadata}
    if 'context' in payload.model_fields_set:
        conversation['context'] = payload.context.model_dump() if payload.context else None
        storage.set_context(user, conversation['id'], conversation['context'])
    if conversation.get('context'):
        from web.services.assistant_workspace import href
        current = conversation['context']
        context_ref = {'type': current['kind'], 'id': current['id'], 'href': href(current['kind'], current['id'])}
        conversation['refs'] = [r for r in conversation['refs'] if (r['type'], r['id']) != (current['kind'], current['id'])] + [context_ref]
        storage.references(conversation['id'], conversation['refs'])
    token = cancel_token if cancel_token is not None else threading.Event()
    with _active_lock:
        _active[payload.request_id] = token
    partial = {'answer': '', 'actions': [], 'error': None}
    last_saved = 0.0

    def persist_event(event):
        nonlocal last_saved
        if event['type'] == 'reset':
            partial['answer'] = ''
        elif event['type'] == 'delta':
            partial['answer'] += event['text']
        elif event['type'] == 'action':
            partial['actions'].append(event['action'])
        if event['type'] == 'action' or time.monotonic() - last_saved > 0.5:
            storage.save(payload.request_id, partial)
            last_saved = time.monotonic()
        if on_event:
            on_event(event)

    llm = None
    try:
        llm = _llm(cancel_token=token)
        if hasattr(llm, 'set_session_id'):
            llm.set_session_id('assistant_' + conversation['id'])
        history = [m.model_dump() for m in payload.history]
        if payload.conversation_id:
            history = []
            recent_records = conversation['requests'][-10:]
            for index, record in enumerate(recent_records):
                history.append({'role': 'user', 'content': record['message']})
                result = record['result']
                actions = json.loads(json.dumps(result.get('actions', [])))
                for action in actions:
                    card = action.get('input_card')
                    if card:
                        try:
                            submitted = storage.input(card['id'], user)
                            action['input_status'] = submitted['status']
                            action['input_result'] = json.loads(submitted['result']) if submitted['result'] else None
                        except ValueError:
                            action['input_status'] = 'unavailable'
                from web.services.assistant_context import dumps, history_actions
                actions = history_actions(actions, brief=index < len(recent_records) - 3)
                history.append({'role': 'assistant', 'content': (result.get('answer') or '回复未完成')[:24000] +
                                '\n实际操作回执：' + dumps(actions) +
                                ('\n尚未完成的任务进度（继续前先核验）：' + dumps(result['continuation']) if result.get('continuation') else '')})
        result = jobs_assistant.run(
            payload.message, history, llm, request, user,
            on_event=persist_event if on_event else None, cancel_token=token,
            store=storage, request_id=payload.request_id, conversation_id=conversation['id'], refs=conversation['refs'])
    except Exception as exc:
        logger.warning("Assistant initialization failed: %s", type(exc).__name__)
        result = {"answer": "助手模型不可用，请检查设置中的模型配置。", "actions": [],
                  "error": "助手模型不可用，请检查设置中的模型配置。"}
    finally:
        if llm is not None:
            try:
                from core import state as state_mod

                state_mod.use_state_dir(get_settings().paths.state_dir)
                state_mod.StateManager.record_usage("assistant", user["username"], llm.usage or None)
            except Exception as exc:
                logger.warning("Assistant usage not recorded: %s", type(exc).__name__)
            try:
                llm.close()
            except Exception as exc:
                logger.warning('Assistant client cleanup failed: %s', type(exc).__name__)
        with _active_lock:
            _active.pop(payload.request_id, None)
    if llm is not None:
        result['usage'] = dict(llm.usage or {})
        metrics = getattr(llm, 'context_metrics', [])
        if metrics:
            result['context_usage'] = {'calls': len(metrics),
                'message_chars': sum(item['message_chars'] for item in metrics),
                'tool_schema_chars': sum(item['tool_schema_chars'] for item in metrics),
                'context_retired': sum(item.get('context_retired', 0) for item in metrics),
                'context_saved_chars': sum(item.get('context_saved_chars', 0) for item in metrics),
                'tool_counts': [item.get('tool_count', 0) for item in metrics]}
    result.update(metadata)
    result['status'] = 'interrupted' if token.is_set() else 'completed'
    storage.save(payload.request_id, result, result['status'])
    audit.log("assistant_chat", user=user["username"],
              result="warning" if result["error"] else "success",
              details={"tools": len(result["actions"])},
              ip=request.client.host if request.client else None)
    return result


@router.post("/stream")
async def stream_chat(payload: ChatRequest, request: Request, user=Depends(require_viewer)):
    """Forward model deltas and completed operations as POST SSE events."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    cancelled = threading.Event()
    disconnected = threading.Event()
    received = {'answer': '', 'actions': []}

    def emit(event):
        if not disconnected.is_set():
            if event['type'] == 'reset':
                received['answer'] = ''
            elif event['type'] == 'delta':
                received['answer'] += event['text']
            elif event['type'] == 'action':
                received['actions'].append(event['action'])
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:  # The request's event loop has already closed.
                cancelled.set()

    def worker():
        try:
            result = _respond(payload, request, user, on_event=emit, cancel_token=cancelled)
        except Exception as exc:
            result = {**received, 'error': str(exc.detail) if isinstance(exc, HTTPException) else '请求未完成，请先核验已执行结果'}
        emit({"type": "done", "result": result})

    async def events():
        threading.Thread(target=worker, daemon=True, name="assistant-stream").start()
        try:
            yield ": connected\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=10)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                if event["type"] == "done":
                    break
        finally:
            disconnected.set()
            cancelled.set()

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no",
    })

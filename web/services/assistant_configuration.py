"""Safe config projection, candidate verification and version-bound rollback."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import time
from web.routes import config
from web.services.assistant_safety import redact, validate_patch, safe_url
from web.services.assistant_store import AssistantStore
from web.services import yaml_io
from web.settings import get_settings


def view(user):
    result = config.get_system_config(user=user)
    data = result['parsed']
    sections = ('ai', 'search', 'qa', 'research', 'budget', 'notifications', 'extraction', 'defaults', 'logging', 'cleanup')
    projected = redact({key: data[key] for key in sections if key in data})
    for name in ('ai', 'extraction'):
        section = projected.get(name) or {}
        for key in ('base_url', 'embedding_base_url'):
            if key in section:
                section[key] = safe_url(str(section[key]))
    return {'data': projected, 'expected_mtime': result['mtime'], 'expected_version': result['version'],
            'scope': 'ai 同时用于后续助手对话和研究任务', 'href': '/settings'}


def candidate_patch(patch: dict, provider_id: str = '') -> dict:
    if patch:
        validate_patch(patch)
    if not provider_id:
        return patch
    from core.providers import get_provider
    preset = get_provider(provider_id)
    if not preset:
        raise ValueError('未知 provider_id，请先查询预设列表')
    current = config._load_system().get('ai') or {}
    old_headers = current.get('extra_headers') or {}
    headers = {key: None for key in old_headers}
    headers.update(preset.extra_headers)
    chosen = {'base_url': preset.base_url, 'api_key_env': preset.api_key_env, 'extra_headers': headers}
    if preset.models:
        chosen['model'] = current.get('model') if current.get('model') in preset.models else preset.models[0]
    return {**patch, 'ai': {**chosen, **patch.get('ai', {})}}


def _with_headers(patch, headers):
    if headers is None:
        return patch
    current = (config._load_system().get('ai') or {}).get('extra_headers') or {}
    return {**patch, 'ai': {**patch.get('ai', {}),
                           'extra_headers': {**{key: None for key in current}, **headers}}}


def test_candidate(patch: dict, user, provider_id: str = '', *, private_headers=None) -> dict:
    cfg = config._merged_system(_with_headers(candidate_patch(patch, provider_id), private_headers))
    from core.llm import LLMClient, LLMConfig
    llm_config = LLMConfig.from_system(cfg)
    llm_config.request_timeout = 60
    llm_config.timeout = 180
    llm_config.max_retries = 0
    llm_config.max_tokens = 512
    client = LLMClient(llm_config)
    started = time.monotonic()
    try:
        plain = client.chat([{'role': 'user', 'content': 'Reply only: ok'}], stream=False, max_tokens=32)
        if not plain.content.strip():
            raise ValueError('普通文本调用没有返回内容')
        tools = [{'type': 'function', 'function': {'name': 'harness_probe',
                  'description': 'Connectivity probe; must be called once.',
                  'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False}}}]
        messages = [{'role': 'user', 'content': 'Call harness_probe exactly once, then say ok after its result.'}]
        call = client.chat(messages, tools=tools, stream=True, on_delta=lambda _: None, max_tokens=512)
        if len(call.tool_calls) != 1 or call.tool_calls[0].name != 'harness_probe' or call.tool_calls[0].arguments != {}:
            raise ValueError('流式工具调用未通过验证')
        messages.extend([call.assistant_message, {'role': 'tool', 'tool_call_id': call.tool_calls[0].id, 'content': '{"ok":true}'}])
        deltas = []
        reply = client.chat(messages, tools=tools, stream=True, on_delta=deltas.append, max_tokens=64)
        if reply.tool_calls or not reply.content.strip() or not ''.join(deltas).strip():
            raise ValueError('工具执行后的流式文本未通过验证')
        return {'ok': True, 'ordinary': True, 'stream_tools': True, 'stream_text': True,
                'model': llm_config.model, 'latency_ms': int((time.monotonic()-started)*1000)}
    except Exception as exc:
        error = str(exc)
        for value in (private_headers or {}).values():
            if value:
                error = error.replace(value, '[已隐藏]')
        return {'ok': False, 'error': redact(error)[:300], 'model': llm_config.model}
    finally:
        try:
            from core.state import StateManager
            StateManager.record_usage('assistant-test', user['username'], client.usage or None)
        finally:
            client.close()


def apply(patch: dict, expected_mtime: float, request, user, force_save: bool = False, provider_id: str = '', *, private_headers=None, expected_version: str = '') -> dict:
    expanded = _with_headers(candidate_patch(patch, provider_id), private_headers)
    if not expanded:
        raise ValueError('未提供需要保存的配置变更')
    before = config.get_system_config(user=user)
    if before['mtime'] != expected_mtime or (expected_version and before['version'] != expected_version):
        raise ValueError('配置已被修改，请重新读取后再保存')
    from core.system_schema import validate_system_change, validate_system_patch
    validate_system_patch(expanded, before['parsed'])
    path = Path(get_settings().paths.config_dir) / 'system.yaml'
    validate_system_change(before['parsed'], yaml_io.parse_yaml(config._merge_patch(str(path), expanded)))
    tested = None
    if 'ai' in expanded and set(expanded['ai']) & {'model', 'base_url', 'api_key_env', 'stream', 'extra_headers'}:
        tested = (test_candidate(patch, user, provider_id, private_headers=private_headers) if private_headers is not None
                  else test_candidate(patch, user, provider_id))
        if not tested['ok'] and not force_save:
            return {'ok': False, 'saved': False, 'test': tested, 'message': '候选配置未通过，保留当前配置'}
    with config.system_config_lock():
        before = config._get_system_config_locked()
        if before['mtime'] != expected_mtime or (expected_version and before['version'] != expected_version):
            raise ValueError('配置已被修改，请重新读取后再保存')
        content = config._merge_patch(str(path), expanded)
        validate_system_change(before['parsed'], yaml_io.parse_yaml(content))
        # All recovery prerequisites precede publication. A crash after the
        # replace still leaves a usable, version-bound recovery record.
        import uuid
        rollback_id = uuid.uuid4().hex
        directory = Path(get_settings().paths.state_dir) / 'assistant-config-backups'
        directory.mkdir(parents=True, exist_ok=True)
        backup = directory / (rollback_id + '.yaml')
        yaml_io.atomic_write(str(backup), before['content'])
        backup.chmod(0o600)
        storage = AssistantStore()
        rollback_id = storage.prepare(user, 'config_rollback', {
            'before_version': before['version'], 'after_version': config.content_version(content),
            'backup_name': backup.name})
        result = config._update_system_config_locked({'content': content, 'expected_version': before['version'],
                                                       'expected_mtime': expected_mtime}, request, user)
    return {**result, 'saved': True, 'test': tested, 'rollback_id': rollback_id,
            'message': '后续请求使用新配置；当前已启动任务保留原配置', 'href': '/settings'}


def rollback(key, request, user):
    from web.deps import require_admin
    require_admin(request, user=user)
    storage = AssistantStore()
    with config.system_config_lock():
        card = storage.input(key, user)
        if card['kind'] != 'config_rollback':
            raise ValueError('回退标识无效')
        if card['status'] != 'pending':
            raise ValueError('回退已执行或结果待核验，请先查看设置')
        params = card['parameters']
        directory = Path(get_settings().paths.state_dir) / 'assistant-config-backups'
        path = directory / params.get('backup_name', key + '.yaml')
        if path.parent != directory or path.suffix != '.yaml':
            raise ValueError('回退文件标识无效')
        content = path.read_text(encoding='utf-8')
        current = config._get_system_config_locked()
        if params.get('before_version') == current['version']:
            result = {'ok': True, 'mtime': current['mtime'], 'version': current['version']}
        else:
            guard = ({'expected_version': params['after_version']} if params.get('after_version')
                     else {'expected_mtime': params['expected_mtime']})
            result = config._update_system_config_locked({'content': content, **guard}, request, user, trusted_restore=True)
        storage.finish_input(key, {'ok': True})
    return {**result, 'message': '已恢复变更前的配置'}


def recovery(user):
    """Find an applicable saved rollback even if its chat response was lost."""
    current = config.get_system_config(user=user)
    storage = AssistantStore()
    from web.services.assistant_store import identity
    with storage.db() as db:
        rows = db.execute("SELECT id,parameters,created FROM protected_inputs WHERE user_id=? AND kind='config_rollback' AND status='pending' ORDER BY created DESC",
                          (identity(user),)).fetchall()
    for row in rows:
        params = json.loads(row['parameters'])
        matches = (params.get('after_version') == current['version'] or
                   (not params.get('after_version') and params.get('expected_mtime') == current['mtime']))
        if matches:
            backup = Path(get_settings().paths.state_dir) / 'assistant-config-backups' / params.get('backup_name', row['id'] + '.yaml')
            if backup.is_file():
                return {'rollback_id': row['id'], 'created': row['created']}
    return {'rollback_id': None}

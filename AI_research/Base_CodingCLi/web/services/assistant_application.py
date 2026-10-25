"""Discover application actions and execute through the same authenticated HTTP boundary as the UI."""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter
from urllib.parse import quote
from typing import Any, Iterator

import httpx
from fastapi import HTTPException, Request
from fastapi.routing import APIRoute

from web import deps
from web.services.assistant_safety import redact, secret_key

DOMAINS = {
    'matrices': ('矩阵与继承设置', '/api/matrices'),
    'recipes': ('配方与公共提示词', '/api/video/recipes'),
    'production': ('任务的视频生成配置', '/api/production'),
    'video': ('选题、文稿、重跑与成片', '/api/video'),
    'jobs': ('研究任务与素材来源', '/api/jobs'),
    'sources': ('信息源', '/api/sources'),
    'platforms': ('平台发布、账号状态与评论', '/api/platforms'),
}
RANK = {'viewer': 0, 'editor': 1, 'admin': 2}


def domain(path: str) -> str | None:
    return next((key for key, (_, prefix) in DOMAINS.items() if path == prefix or path.startswith(prefix + '/')), None)


def allowed(route: Any) -> bool:
    if not isinstance(route, APIRoute) or not domain(route.path):
        return False
    # Binary/stream transports and credentials have dedicated UI/protected-input flows.
    return not any(part in route.path.split('/') for part in ('stream', 'files', 'reveal', 'credentials', 'login'))


def role_of(route: APIRoute) -> str:
    def walk(dependant):
        roles = [role for role, call in (('viewer', deps.require_viewer), ('editor', deps.require_editor), ('admin', deps.require_admin))
                 if dependant.call is call]
        for child in dependant.dependencies:
            roles.extend(walk(child))
        return roles
    roles = walk(route.dependant)
    return max(roles, key=RANK.get) if roles else 'admin'


def routes(request: Request, user: dict) -> Iterator[tuple[str, APIRoute, str]]:
    def expanded(items):
        for item in items:
            if isinstance(item, APIRoute):
                yield item
            elif hasattr(item, 'effective_route_contexts'):
                for context in item.effective_route_contexts():
                    route = context.original_route
                    if isinstance(route, APIRoute) and context.path == route.path:
                        yield route
    for route in expanded(request.app.routes):
        if allowed(route) and RANK[user['role']] >= RANK[role_of(route)]:
            for method in sorted(route.methods & {'GET', 'POST', 'PUT', 'PATCH', 'DELETE'}):
                yield f'{method} {route.path_format}', route, method


def describe(request: Request, user: dict, *, domain_key: str = '', search: str = '', action_id: str = '', offset: int = 0) -> dict:
    found = []
    for key, route, method in routes(request, user):
        area = domain(route.path)
        label = route.summary or route.name.replace('_', ' ')
        if action_id and key != action_id:
            continue
        if domain_key and area != domain_key:
            continue
        if search and search.lower() not in f'{key} {label} {DOMAINS[area][0]} {route.description}'.lower():
            continue
        found.append({'action_id': key, 'domain': area, 'name': label, 'description': route.description[:1200],
                      'write': method != 'GET', 'role': role_of(route)})
    result = {'domains': {k: v[0] for k, v in DOMAINS.items()}, 'total': len(found),
              'actions': found[offset:offset + 24], 'next_offset': offset + 24 if offset + 24 < len(found) else None}
    if action_id:
        if not found:
            raise ValueError('操作不存在或当前账号无权使用')
        method, path = action_id.split(' ', 1)
        schema = request.app.openapi()
        operation = schema['paths'][path][method.lower()]
        result['parameters'] = operation.get('parameters', [])
        result['requestBody'] = operation.get('requestBody')
        # Return only referenced schemas, not the whole application's specification.
        definitions = schema.get('components', {}).get('schemas', {})
        selected = {}
        def collect(value):
            if isinstance(value, dict):
                ref = value.get('$ref', '').removeprefix('#/components/schemas/')
                if ref in definitions and ref not in selected:
                    selected[ref] = definitions[ref]
                    collect(definitions[ref])
                for item in value.values():
                    collect(item)
            elif isinstance(value, list):
                for item in value:
                    collect(item)
        collect([result['parameters'], result['requestBody']])
        result['schemas'] = selected
    return result


def path_for(route: APIRoute, values: dict) -> str:
    names = set(route.param_convertors)
    if set(values) != names:
        raise ValueError('path 必须包含且只包含操作声明的路径参数')
    path = route.path_format
    for name, value in values.items():
        if not isinstance(value, str) or not value or any(c in value for c in ('?', '#', '%', '\\')) or any(s in ('.', '..') for s in value.split('/')):
            raise ValueError('路径参数不合法')
        is_path = type(route.param_convertors[name]).__name__ == 'PathConvertor'
        if '/' in value and not is_path:
            raise ValueError('此路径参数不能包含斜杠')
        path = path.replace('{' + name + '}', quote(value, safe='/' if is_path else ''))
    return path


def check_credentials(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if secret_key(key) or key.lower() in ('cookie', 'cookies', 'extra_headers'):
                raise ValueError('凭据请使用受保护输入卡片或平台登录页面')
            check_credentials(item)
    elif isinstance(value, list):
        for item in value:
            check_credentials(item)


async def native(request: Request, method: str, path: str, *, query: dict | None = None, body: Any = None) -> tuple[int, Any]:
    # Only same-process requests. Authentication and CSRF never enter model arguments/results.
    headers = {k: request.headers[k] for k in ('cookie', 'authorization', 'x-csrf-token') if k in request.headers}
    async def authenticated_app(scope, receive, send):
        deps.inherit_background(request.scope, scope)
        await request.app(scope, receive, send)
    transport = httpx.ASGITransport(app=authenticated_app, client=(request.client.host if request.client else '127.0.0.1', 0))
    async with httpx.AsyncClient(transport=transport, base_url='http://assistant.local', headers=headers, timeout=30) as client:
        response = await client.request(method, path, params=query, json=body)
    if 'application/json' not in response.headers.get('content-type', ''):
        raise ValueError('此操作的结果需在原页面查看')
    data = redact(response.json())
    if response.is_error:
        raise HTTPException(response.status_code, data.get('detail', data))
    return response.status_code, data


def targets(route: APIRoute, values: dict, data: Any) -> list[dict]:
    result = []
    def add(kind, key, href, title=''):
        if key:
            result.append({'type': kind, 'id': key, 'href': href, 'title': title or key})
    row = data if isinstance(data, dict) else {}
    path = route.path_format
    if path == '/api/video/autopilot/runs' and row.get('id'):
        add('batch', row['id'], '/?automation=1', '视频生产批次')
    sid = values.get('script_id') or row.get('script_id') or (row.get('id') if path == '/api/video/scripts' else None)
    add('video', sid, '/video/' + str(sid), row.get('title') or (row.get('version_shown') or {}).get('title', ''))
    if path.startswith('/api/matrices'):
        key = values.get('key') or row.get('key')
        add('matrix', key, '/matrices/' + quote(str(key or ''), safe=''), row.get('name', ''))
    if path.startswith('/api/video/recipes/parts/') and values.get('name'):
        key = values['kind'] + '/' + values['name']
        add('part', key, '/video-recipes/parts/' + key)
    elif path.startswith('/api/video/recipes'):
        key = values.get('key') or row.get('key')
        add('recipe', key, '/video-recipes/' + quote(str(key or ''), safe=''), row.get('label', ''))
    if path.startswith('/api/jobs/') and values.get('name'):
        add('job', values['name'], '/jobs/' + quote(values['name'], safe=''))
    if path.startswith('/api/platforms'):
        platform = values.get('platform') or ('douyin' if '/douyin' in path else 'bilibili')
        add('platform', platform, '/platforms?platform=' + platform)
        if row.get('id') and ('uploads' in path):
            add('upload', platform + ':' + str(row['id']), '/platforms?platform=' + platform)
    return result


def configuration_diff(before: dict, body: dict) -> list[dict]:
    """Describe saved configuration fields, without sending whole YAML files to cards."""
    replacement = isinstance(body.get('content'), str)
    if replacement:
        raw_before, raw_body = before, body
        from core.yaml_cache import load_yaml
        import yaml
        try:
            before, body = load_yaml(before.get('content') or '') or {}, load_yaml(body['content']) or {}
        except (ValueError, yaml.YAMLError):
            return [{'field': 'content', 'before': raw_before.get('content'), 'after': raw_body.get('content')}]
    fields = []

    def compare(old, new, prefix=''):
        if isinstance(new, dict):
            for key, value in new.items():
                if key.startswith('expected_'):
                    continue
                compare(old.get(key) if isinstance(old, dict) else None, value, f'{prefix}.{key}' if prefix else key)
            # YAML replacement can also remove fields.
            if replacement and isinstance(old, dict):
                fields.extend({'field': f'{prefix}.{key}' if prefix else key, 'before': old[key], 'after': None}
                              for key in sorted(old.keys() - new.keys()))
        elif old != new:
            fields.append({'field': prefix, 'before': old, 'after': new})
    compare(before, body)
    return fields[:24]


def changed_fields(before: dict, body: dict) -> list[str]:
    return [item['field'] for item in configuration_diff(before, body)]


def configuration_change(area: str, path: str, values: dict, before: dict, changes: list[dict]) -> dict:
    shared = area == 'recipes' and '/parts/' in path
    level, label, scope = {
        'recipes': ('shared', '共用部件', '所有引用此部件的配方') if shared else ('recipe', '文稿配方', '所有使用此配方的后续生成'),
        'matrices': ('matrix', '矩阵共用设置', '本矩阵未单独覆盖该字段的任务'),
        'production': ('task', '任务视频设置', '该研究任务的后续视频'),
        'jobs': ('job', '研究任务', '该任务后续生成的研究报告'),
    }[area]
    render_only = area == 'matrices' or area == 'production' and path.endswith('/settings') or values.get('kind') in ('output', 'packaging') or bool(changes) and all(
        c['field'].startswith(('output.', 'packaging.', 'render.')) for c in changes)
    next_step = '重新生成视频' if render_only else '重新生成文稿，再生成视频'
    if area == 'jobs' or values.get('kind') == 'research' or any(c['field'].startswith('research.') for c in changes):
        next_step = '重新调研后写稿；只重写会沿用旧报告'
    if area == 'production' and changes and all(c['field'] == 'auto' for c in changes):
        next_step = '用于后续自动生产；需要更新已有内容时再重跑'
    # Keep receipts small even for long prompt fields. The editor retains complete YAML.
    def preview(value):
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        if len(text) > 1600:
            return text[:1600] + '…（完整内容见配置页）'
        return value
    return {'level': level, 'label': label, 'scope': scope, 'target': values, 'used_by': before.get('used_by') or [],
            'changes': [{**c, 'before': preview(c['before']), 'after': preview(c['after'])} for c in changes],
            'next': next_step, 'existing_outputs': '已有文稿和视频保留，保存配置不会自动改写历史产物。'}


def execute(a: Any, request: Request, user: dict, *, write: bool = False) -> dict:
    selected = next(((r, m) for key, r, m in routes(request, user) if key == a.action_id), None)
    if selected is None:
        raise ValueError('操作不存在或当前账号无权使用')
    route, method = selected
    if (method != 'GET') != write:
        raise ValueError('读取操作使用 read_application，修改操作使用 operate_application')
    check_credentials([a.path, a.query, a.body])
    area = domain(route.path)
    fields = []
    change = None
    if method in ('PUT', 'PATCH') and area in ('recipes', 'production', 'matrices', 'jobs') and isinstance(a.body, dict):
        try:
            _, before = asyncio.run(native(request, 'GET', path_for(route, a.path)))
        except (HTTPException, ValueError):
            before = {}
        before = before if isinstance(before, dict) else {}
        compared = a.body
        if area == 'production' and route.path_format.endswith('/settings'):
            # Inheritance is an edit operation, not a persisted configuration
            # field. Show the removed task override and its prior value.
            compared = {'render': {**a.body.get('render', {}), **{field: None for field in a.body.get('inherit', [])}}}
        diffs = configuration_diff(before, compared)
        fields = [c['field'] for c in diffs]
        change = configuration_change(area, route.path_format, a.path, before, diffs)
    status, data = asyncio.run(native(request, method, path_for(route, a.path), query=a.query, body=a.body))
    refs = targets(route, a.path, data)
    if isinstance(data, dict) and route.path_format.startswith('/api/video/scripts') and 'version_shown' in data:
        from web.services.assistant_video_state import model_detail
        data = model_detail(data)
    ok = not isinstance(data, dict) or data.get('ok') is not False
    info = ({'returned_count': len(data), 'status_counts': dict(Counter(row['status'] for row in data
             if isinstance(row, dict) and isinstance(row.get('status'), str)))} if isinstance(data, list) else {})
    serialized = json.dumps(data, ensure_ascii=False)
    if not write and (len(serialized) > 30000 or a.result_offset):
        offset = a.result_offset
        data = {'format': 'json_fragment', 'content': serialized[offset:offset + 30000],
                'next_offset': offset + 30000 if offset + 30000 < len(serialized) else None,
                'total_chars': len(serialized), 'sha256': hashlib.sha256(serialized.encode()).hexdigest()}
    elif write and len(serialized) > 30000 and isinstance(data, dict):
        data = {k: v for k, v in data.items() if k in ('id', 'key', 'name', 'title', 'status', 'current_version',
                'approved_version', 'mtime', 'sha256', 'error', 'ok', 'message')}
        data['detail'] = '已保存；完整内容通过对应读取操作查看，不重复执行本次修改。'
    noun = '共享提示词/部件' if '/recipes/parts' in route.path else DOMAINS[area][0]
    title = {'GET': '读取', 'POST': '提交', 'PUT': '保存', 'PATCH': '更新', 'DELETE': '删除'}[method] + noun
    impact = ''
    if method in ('PUT', 'PATCH'):
        impact = {'matrices': '影响本矩阵后续生成；任务单独覆盖的设置保持。',
                  'recipes': '影响使用该配方或共享部件的后续生成；已有产物需重新生成。',
                  'production': '影响该任务后续视频生成。', 'jobs': '影响该任务后续研究。'}.get(area, '')
    return {'action_id': a.action_id, 'ok': ok, 'submitted': status == 202, 'impact': impact, 'changed_fields': fields,
            'configuration_change': change,
            'message': (title + ('：已提交，实际进度见工作区' if status == 202 else '：操作完成')) if ok else '业务操作未成功，请查看返回结果',
            'result': data, 'result_info': info, 'references': refs, 'href': refs[0]['href'] if refs else None}

"""Complete bounded business goals using existing APIs and durable video workers."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import HTTPException, Request

from core import matrices
from core.fileio import file_lock
from core.video import production, production_settings, store as videos, autopilot_store
from web import audit, deps
from web.services import assistant_application, assistant_management
from web.services.assistant_batch_store import BatchStore, view
from web.settings import get_settings

logger = logging.getLogger(__name__)
RANK = {'viewer': 0, 'editor': 1, 'admin': 2}
_cursors: dict[str, int] = {}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def script_report_time(script: dict) -> float:
    version = videos.get_version(script['id'], script['current_version']) if script['current_version'] else None
    timestamp = (version or {}).get('created_at') or script['created_at']
    if script['status'] in ('WRITING', 'RESEARCHING'):
        timestamp = script['updated_at']
    return datetime.strptime(timestamp, '%Y-%m-%dT%H:%M:%S%z').timestamp()


def script_target_version(script: dict) -> int:
    if script['status'] in ('WRITING', 'RESEARCHING') and not script['current_version']:
        return max([v['version'] for v in videos.list_versions(script['id'])] or [0]) + 1
    return max(1, script['current_version'])


def required(kind: str) -> str:
    return 'admin' if kind == 'schedules' else 'editor'


def check_role(user: dict, kind: str) -> None:
    if RANK[user['role']] < RANK[required(kind)]:
        raise HTTPException(403, '当前账号没有执行此批次的权限')


def selected(rows: list[dict], scope: dict, *, owner_jobs: set[str] | None = None) -> list[dict]:
    ids = set(scope.get('ids') or [])
    job_names = set(scope.get('jobs') or [])
    if not ids and not job_names and not scope.get('matrices') and not scope.get('all'):
        raise ValueError('请指定 ids、jobs、matrices 或明确 all=true；空范围不会扩大为全部')
    unknown = ids - {r['key'] for r in rows}
    if unknown:
        options = [{'id': r['key'], 'job': r.get('job')} for r in rows if r.get('job') in unknown]
        raise ValueError('找不到指定对象：' + '、'.join(sorted(unknown)) + ('。任务对应的生产配置：' + json.dumps(options, ensure_ascii=False) + '；也可使用 scope.jobs 选择任务。' if options else ''))
    if job_names - {r.get('job') for r in rows}:
        raise ValueError('这些任务没有可选对象：' + '、'.join(sorted(job_names - {r.get('job') for r in rows})))
    return [r for r in rows if (not ids or r['key'] in ids) and
            (not job_names or r.get('job') in job_names) and
            (owner_jobs is None or r.get('job') in owner_jobs)]


def prepare(spec: dict, request: Request, user: dict) -> dict:
    """Freeze the whole target list once. Subsequent arrivals never join implicitly."""
    kind, scope = spec['kind'], spec['scope']
    check_role(user, kind)
    paths = get_settings().paths
    every = matrices.load_all(paths.config_dir)
    chosen = scope.get('matrices') or []
    if set(chosen) - set(every):
        raise ValueError('矩阵不存在：' + '、'.join(sorted(set(chosen) - set(every))))
    jobs = {j for k in chosen for j in every[k].jobs} if chosen else None
    items = []
    if kind == 'matrix_render':
        if scope.get('jobs'):
            raise ValueError('矩阵设置按矩阵选择，请使用 scope.matrices 或矩阵 ids')
        if not spec.get('render'):
            raise ValueError('请提供需要调整的 render 字段')
        patch = production_settings.validate(spec['render'])
        scope = {**scope, 'ids': scope.get('ids') or chosen}
        rows = [{'key': k, 'name': m.name} for k, m in every.items() if not m.archived or k in scope.get('ids', [])]
        for row in selected(rows, scope):
            current = assistant_management.matrix_detail(row['key'], user)
            items.append({'target': row['key'], 'title': row['name'], 'payload': {
                'revision': current['revision'], 'render': patch, 'before': current['render']}})
    elif kind == 'schedules':
        from web.services.assistant_tools.work import query, ScheduleQuery
        rows = [{**r, 'key': r['id'], 'job': r.get('job_name')} for r in query(ScheduleQuery(), request, user)['jobs']]
        for row in selected(rows, scope, owner_jobs=jobs):
            items.append({'target': row['key'], 'title': row.get('name') or row.get('title') or row['key'],
                          'payload': {'enabled': spec['enabled'], 'before': bool(row.get('enabled')),
                                      'version': row.get('version'), 'schedule': row.get('schedule')}})
    elif kind == 'publish':
        from web.services.assistant_tools.management import PublishingQuery
        platform = spec.get('platform')
        if not platform:
            raise ValueError('批量发布需要指定平台')
        profile = assistant_management.account(platform)
        if not profile.get('connected') or not spec.get('account_uid') or profile.get('uid') != spec['account_uid']:
            raise ValueError('请先查询并核对已登录的平台账号')
        rows, offset = [], 0
        while True:
            data = assistant_management.publishing_query(PublishingQuery(action='candidates', platform=platform, offset=offset, limit=50), request, user)
            rows.extend({**r, 'key': r.get('render_id') or r['id']} for r in data['items'])
            offset = data.get('next_offset')
            if offset is None:
                break
        if jobs is not None:
            from core.video import outputs
            scripts = [s for r in rows if (s := videos.get_script(r['script_id']))]
            owners, _ = outputs.script_owners(paths.config_dir, paths.jobs_dir, scripts)
            rows = [r for r in rows if owners.get(r['script_id'], {}).get('matrix') in chosen]
        for row in selected(rows, scope):
            items.append({'target': row['key'], 'title': row['title'], 'payload': {
                'script_id': row['script_id'], 'version': row['version'], 'account_uid': profile['uid'], 'platform': platform}})
    else:
        configs = production.load_all(paths.config_dir, paths.jobs_dir)
        rows = [{'key': c.key, 'job': c.job, 'config': c} for c in configs.values()]
        selected_configs = selected(rows, scope, owner_jobs=jobs)
        for row in selected_configs:
            config = row['config']
            if not config.usable:
                raise ValueError(config.key + '：' + '；'.join(config.errors))
        if spec.get('refresh', True):
            from core.config import load_job
            for job in dict.fromkeys(r['job'] for r in selected_configs):
                data = load_job(paths.jobs_dir, job) or {}
                if not data.get('enabled', True):
                    raise ValueError('研究任务已停用：' + job)
                path = Path(paths.jobs_dir) / (job + '.yaml')
                items.append({'target': 'research:' + job, 'title': data.get('name') or job,
                              'payload': {'type': 'research', 'job': job, 'revision': digest(path)}})
        for row in selected_configs:
            config = row['config']
            items.append({'target': config.key, 'title': config.name or config.label or config.key, 'payload': {
                'type': 'production', 'job': config.job, 'revision': digest(Path(production.directory(paths.config_dir)) / (config.key + '.yaml')),
                'dependency': 'research:' + config.job if spec.get('refresh', True) else '', 'count': spec.get('count', 1)}})
    title = {'matrix_render': '批量调整矩阵设置', 'schedules': '批量启停周期', 'publish': '批量发布到 ' + spec.get('platform', ''),
             'produce': '研究并制作视频'}[kind]
    result = BatchStore().create(user, title, spec, items)
    return {'batch': view(result), 'application_batch_id': result['id'], 'href': result['href'],
            'message': f'已准备 {len(items)} 项，范围已固定，尚未执行', 'references': []}


def manage(key: str, action: str, user: dict) -> dict:
    storage = BatchStore()
    batch = storage.get(key, user)
    check_role(user, batch['spec']['kind'])
    with file_lock(str(storage.path) + '.batch-' + key):
        batch = storage.get(key, user)
        if action == 'start' and batch['status'] == 'prepared':
            storage.set_status(key, 'running' if batch['items'] else 'completed')
        elif action == 'cancel':
            storage.set_status(key, 'cancelled')
            # Already submitted work belongs to its native executor and remains visible.
        elif action == 'retry' and batch['status'] in ('failed', 'waiting'):
            for item in batch['items']:
                if item['state'] not in ('failed', 'blocked'):
                    continue
                saved = item['result']
                if saved.get('upload_id'):
                    # Inspect the original upload. A new submission is never a retry.
                    storage.item(item['id'], state='active', result={**saved, 'retry_requested': True})
                elif saved.get('scripts'):
                    for workflow in saved.get('workflow_ids', []):
                        run = autopilot_store.get_run(workflow['id'])
                        for entry in (run or {}).get('items', []):
                            script = videos.get_script(entry['script_id']) if entry.get('script_id') else None
                            if entry.get('script_id') in saved['scripts'] and entry['state'] in ('HELD', 'FAILED') and script and not script.get('archived'):
                                autopilot_store.update_item(entry['id'], state='WORKING', retries={}, note='用户从操作批次继续制作')
                                autopilot_store.update_run(workflow['id'], status='RUNNING')
                    storage.item(item['id'], state='active', result=saved)
                else:
                    # An explicit retry may rerun a failed research; the old receipt stays recorded.
                    previous = {k: v for k, v in saved.items() if k != 'previous_attempts'}
                    history = saved.get('previous_attempts', []) + ([previous] if previous else [])
                    storage.item(item['id'], state='pending', stage='', result={'previous_attempts': history} if history else {})
            storage.set_status(key, 'running')
    batch = storage.get(key, user)
    audit.log('assistant_batch_' + action, user=user['username'], target=key, details={'kind': batch['spec']['kind'], 'count': len(batch['items'])})
    return {'batch': view(batch), 'application_batch_id': key, 'submitted': batch['active'], 'status': 'started' if batch['active'] else batch['status'],
            'message': '批次已交给后台，任务中心持续显示逐项结果' if batch['active'] else '批次状态已记录；已提交的研究、视频或投稿保持原状',
            'href': batch['href'], 'references': []}


def background_request(app, user_id: int) -> Request:
    scope = {'type': 'http', 'method': 'GET', 'path': '/', 'root_path': '', 'query_string': b'',
             'headers': [], 'scheme': 'http', 'server': ('assistant.local', 80), 'client': ('127.0.0.1', 0), 'app': app}
    deps.delegate_background(scope, user_id)
    return Request(scope)


async def native(app, user_id: int, method: str, path: str, *, body=None, query=None):
    async def delegated(scope, receive, send):
        deps.delegate_background(scope, user_id)
        await app(scope, receive, send)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=delegated), base_url='http://assistant.local', timeout=30) as client:
        response = await client.request(method, path, json=body, params=query)
    data = response.json()
    if response.is_error:
        raise HTTPException(response.status_code, data)
    from web.services.assistant_safety import redact
    return redact(data)


def _error(exc: Exception) -> str:
    from web.services.assistant_safety import redact
    return str(redact(exc.detail if isinstance(exc, HTTPException) else str(exc)))[:1500]


async def advance(batch: dict, item: dict, app, user: dict, storage: BatchStore, *, observe_only: bool = False) -> None:
    """A short dispatch or read-only observation; waiting on work never blocks other items."""
    kind, payload, result = batch['spec']['kind'], item['payload'], item['result']
    uid, paths = int(batch['user_id']), get_settings().paths
    request = background_request(app, uid)

    async def dispatch(stage, method, path, body=None):
        storage.item(item['id'], state='dispatching', stage=stage, result=result)
        return await native(app, uid, method, path, body=body)

    if item['state'] == 'dispatching':
        storage.item(item['id'], state='unknown', error='上次提交结果未记录，请先核对原生任务；不会自动重复提交')
        return
    if kind == 'matrix_render':
        if assistant_management.matrix_revision(item['target']) != payload['revision']:
            raise HTTPException(409, '矩阵已变化，请重新准备这项变更')
        from web.services.assistant_tools.management import MatrixChange
        storage.item(item['id'], state='dispatching', stage='保存矩阵')
        data = await asyncio.to_thread(assistant_management.matrix_change, MatrixChange(action='update', key=item['target'],
                    expected_revision=payload['revision'], fields={'render': payload['render']}), request, user)
        storage.item(item['id'], state='completed', result={'href': data['href'], 'before': payload['before'], 'render': payload['render']})
    elif kind == 'schedules':
        current = await native(app, uid, 'GET', '/api/scheduler/' + quote(item['target'], safe=''))
        if current.get('version') != payload['version'] or current.get('schedule') != payload['schedule']:
            raise HTTPException(409, '周期已变化，请重新准备这项变更')
        data = await dispatch('启用周期' if payload['enabled'] else '暂停周期', 'PUT', '/api/scheduler/' + quote(item['target'], safe='') + '/toggle', {'enabled': payload['enabled']})
        storage.item(item['id'], state='completed', result={'href': '/scheduler?plan=' + item['target'], 'enabled': payload['enabled']})
    elif kind == 'publish':
        from web.services.publication_state import describe, resume_action
        platform = payload['platform']
        base = assistant_management.platform_path(platform)
        if item['state'] in ('active', 'blocked') and result.get('upload_id'):
            data = await native(app, uid, 'GET', base + '/uploads/' + quote(result['upload_id'], safe=''))
            status = data.get('status', '')
            if not observe_only and result.get('retry_requested') and (operation := resume_action(platform, data)):
                result = {k: v for k, v in result.items() if k != 'retry_requested'}
                started = time.time()
                reply = await dispatch('继续原投稿任务', 'POST', base + '/uploads/' + quote(result['upload_id'], safe='') + '/' + operation,
                                       {'bvid': ''} if operation == 'reconcile' and platform == 'bilibili' else {'video': ''} if operation == 'reconcile' else None)
                # A reconciliation receipt identifies the check, never a replacement upload.
                if reply.get('kind') == 'RECONCILE':
                    result['resume_check_started_at'] = started
                storage.item(item['id'], state='active', stage='正在核对原投稿' if operation == 'reconcile' else '原投稿重试已提交', result=result)
                return
            progress = describe(platform, result['upload_id'], data)
            checking = result.get('resume_check_started_at', 0) > (data.get('verification') or {}).get('checked_at', 0) and status in ('SUBMITTED', 'UNKNOWN')
            state = 'completed' if status == 'PUBLISHED' else 'failed' if status in ('FAILED', 'CANCELLED') else 'blocked' if not progress['active'] and not checking else 'active'
            result = {k: v for k, v in result.items() if k != 'retry_requested'}
            if not checking:
                result.pop('resume_check_started_at', None)
            storage.item(item['id'], state=state, stage='正在核对原投稿' if checking else progress['label'],
                         result={**result, 'publication': progress, 'recovery': progress['recovery'],
                                 'href': assistant_management.publication_href(platform, result['upload_id'], data)}, error=data.get('error') or '')
            return
        profile = assistant_management.account(platform)
        if not profile.get('connected') or profile.get('uid') != payload['account_uid']:
            raise HTTPException(403, '平台账号未连接或已变化，请在平台页登录后继续')
        script = videos.get_script(payload['script_id'])
        if not script or script.get('archived') or script['current_version'] != payload['version'] or script.get('approved_version') != payload['version']:
            raise HTTPException(409, '文稿版本或定稿状态已变化，请重新选择成片')
        preview = await native(app, uid, 'GET', base + '/preview/' + quote(item['target'], safe=''))
        draft = ((preview.get('draft') or {}).get('payload') or {})
        fields = {k: draft.get(k, preview.get(k)) for k in ('title', 'description', 'tags', 'tid', 'first_comment') if k in draft or k in preview}
        data = await dispatch('提交投稿', 'POST', base + '/uploads', {**fields, 'render_id': item['target'], 'account_uid': payload['account_uid']})
        key = data.get('id') or data.get('task_id')
        if not key:
            raise RuntimeError('投稿已返回但缺少任务 ID，请核对平台队列')
        storage.item(item['id'], state='active', result={'upload_id': key, 'platform': platform, 'version': payload['version'], 'href': assistant_management.publication_href(platform, key, data)})
    elif payload['type'] == 'research':
        if item['state'] == 'pending':
            if digest(Path(paths.jobs_dir) / (payload['job'] + '.yaml')) != payload['revision']:
                raise HTTPException(409, '研究任务已变化，请重新准备本次制作')
            data = await dispatch('启动研究', 'POST', '/api/jobs/' + quote(payload['job'], safe='/') + '/run')
            storage.item(item['id'], state='active', stage='研究中', result={**result, 'run_id': data['task_id'], 'href': '/jobs/' + quote(payload['job'], safe='')})
            return
        from web.services.assistant_runs import run_view
        data = await run_view({'source': 'manual', 'job_name': payload['job'], 'run_id': result['run_id']}, request, user)
        if data['active']:
            return
        if data['status'] != 'success' or not data.get('report'):
            storage.item(item['id'], state='failed', error=data.get('error') or data['label'], result=result)
            return
        report = data['report']
        from web.services import video_workflow
        from web.routes.reports import resolve_output_file
        from core.video import reports
        path = Path(resolve_output_file(report['path'], ('.md', '.mdx')))
        workspace_report = reports.relative(str(path), video_workflow.workspace_dir())
        storage.item(item['id'], state='completed', stage='报告完成', result={**result, 'report': report,
            'workspace_report': workspace_report, 'report_sha256': digest(path), 'report_at': path.stat().st_mtime})
    else:
        await advance_production(batch, item, app, user, storage, observe_only=observe_only)


async def advance_production(batch, item, app, user, storage, *, observe_only=False):
    payload, result = item['payload'], item['result']
    paths = get_settings().paths
    if item['state'] == 'pending':
        dependency = next((i for i in batch['items'] if i['target'] == payload['dependency']), None)
        if dependency and dependency['state'] != 'completed':
            if dependency['state'] in ('failed', 'blocked', 'unknown'):
                storage.item(item['id'], state='blocked', error='上游研究未完成，请先处理该次研究')
            return
        config = production.load_all(paths.config_dir, paths.jobs_dir).get(item['target'])
        if not config or not config.usable or digest(Path(production.directory(paths.config_dir)) / (item['target'] + '.yaml')) != payload['revision']:
            raise HTTPException(409, '生产配置已变化，请重新准备本次制作')
        pending = await native(app, int(batch['user_id']), 'GET', '/api/production/' + quote(item['target'], safe='') + '/pending')
        report = (dependency or {}).get('result', {}).get('report')
        workspace_report = (dependency or {}).get('result', {}).get('workspace_report')
        if dependency:
            from web.routes.reports import resolve_output_file
            if digest(Path(resolve_output_file(report['path'], ('.md', '.mdx')))) != dependency['result']['report_sha256']:
                raise HTTPException(409, '本次报告文件已被其他运行更新，请重新核对，不使用另一轮素材')
        if config.split == 'whole':
            candidates = pending.get('reports', [])
            source = next((r for r in candidates if r['path'] == workspace_report), None) if report else next(iter(candidates), None)
            if not source:
                raise HTTPException(409, '本次报告不在该生产配置的报告列表中')
            if source['written']:
                matches = [s for row in source.get('scripts', []) if (s := videos.get_script(row['id']))
                           and not s.get('archived') and s.get('production') == item['target']]
                owned = list(matches)
                if dependency:
                    # A recurring job may overwrite today's path. An older script
                    # is not proof that this newly completed report was produced.
                    report_at = dependency['result'].get('report_at', 0)
                    matches = [s for s in matches if script_report_time(s) + 1 >= report_at]
                if not matches:
                    if not dependency or not owned:
                        storage.item(item['id'], state='blocked', stage='已有文稿需核对', result={'href': '/outputs'},
                                     error='未找到属于本配置且对应这次报告的可继续文稿；不会把旧报告的成片记作本次完成')
                        return
                    # A daily filename can contain a new research result. Reuse
                    # the native rewrite so old scripts and renders stay intact.
                    body = {'report_path': source['path'], 'rewrite_existing': True,
                            'report_sha256': dependency['result']['report_sha256']}
                else:
                    scripts = [s['id'] for s in matches]
                    versions = {s['id']: script_target_version(s) for s in matches}
                    storage.item(item['id'], state='active', stage='继续已有文稿与视频', result={'scripts': scripts, 'script_versions': versions,
                        'workflow_ids': [], 'failures': [], 'report': report, 'reused': True, 'href': '/video/' + scripts[0]})
                    return
            else:
                body = {'report_path': source['path']}
        else:
            if report and pending.get('report_path') != workspace_report:
                raise HTTPException(409, '选题来源已切换到另一份报告，请核对本次报告后继续')
            topics = [t['id'] for t in pending.get('items', []) if not t.get('written') and not t.get('dismissed')][:payload['count']]
            if not topics:
                storage.item(item['id'], state='skipped', stage='没有未制作选题', result={'href': '/topics'})
                return
            body = {'topic_ids': topics}
        result = {**result, 'report': report, 'href': '/outputs'}
        storage.item(item['id'], state='dispatching', stage='开始写稿', result=result)
        made = await native(app, int(batch['user_id']), 'POST', '/api/production/' + quote(item['target'], safe='') + '/write', body=body)
        scripts = [r['id'] for r in made if r.get('id')]
        failures = [r['failed'] for r in made if r.get('failed')]
        if not scripts:
            raise RuntimeError('写稿提交返回中没有文稿 ID，请核对文稿列表，不自动重复创建')
        versions = {r['id']: r.get('target_version') or max(1, r.get('current_version') or 0) for r in made if r.get('id')}
        storage.item(item['id'], state='active', stage='文稿与视频制作中', result={'scripts': scripts, 'script_versions': versions, 'workflow_ids': [], 'failures': failures,
                     'report': report, 'href': '/video/' + scripts[0] if scripts else '/outputs'})
        return
    workflows = list(result.get('workflow_ids', []))
    for sid in result.get('scripts', []):
        script = videos.get_script(sid)
        if not script or script.get('archived'):
            storage.item(item['id'], state='blocked', result=result, error='文稿已删除或归档，停止后续制作')
            return
        if script['current_version'] > result.get('script_versions', {}).get(sid, script['current_version']):
            storage.item(item['id'], state='blocked', result=result, error='文稿已有后续版本，本批次保留原版本结果，请到文稿页选择要继续制作的版本')
            return
        # adopt_script is idempotent while running; record each receipt before continuing.
        if not any(w['script_id'] == sid for w in workflows):
            if observe_only:
                storage.item(item['id'], state='blocked', result=result, error='已停止后续派发；文稿写作已提交，尚未继续出片')
                return
            run = autopilot_store.adopt_script(script, started_by=user['username'])
            workflows.append({'script_id': sid, 'id': run['id']})
            result = {**result, 'workflow_ids': workflows}
            storage.item(item['id'], state='active', result=result)
    runs = [autopilot_store.get_run(w['id']) for w in workflows]
    if any(not r for r in runs):
        storage.item(item['id'], state='unknown', result=result, error='原视频 workflow 不可读取，请核对已有成片')
        return
    owned = {sid for sid in result.get('scripts', [])}
    entries = list({i['id']: i for r in runs for i in r['items'] if i.get('script_id') in owned}.values())
    if any(i['state'] in ('PENDING', 'WORKING') for i in entries):
        return
    if {i.get('script_id') for i in entries} != owned:
        storage.item(item['id'], state='unknown', result=result, error='原 workflow 中缺少本批次文稿，请核对已有制作记录')
        return
    render_ids = [i['render_id'] for i in entries if i.get('render_id')]
    render_refs = [{'id': i['render_id'], 'script_id': i['script_id'], 'version': result.get('script_versions', {}).get(i['script_id'])}
                   for i in entries if i.get('render_id')]
    errors = [i.get('note') or i['state'] for i in entries if i['state'] != 'DONE']
    state = 'failed' if errors or result.get('failures') else 'completed'
    storage.item(item['id'], state=state, stage='待发布' if state == 'completed' else '制作需要处理',
                 result={**result, 'render_ids': render_ids, 'renders': render_refs}, error='；'.join(errors))


async def tick(app) -> int:
    storage, dispatched = BatchStore(), 0
    for batch in storage.list(active=True):
        try:
            with file_lock(str(storage.path) + '.batch-' + batch['id'], timeout=0):
                batch = storage.get(batch['id'])
                observe_only = batch['status'] != 'running'
                request = background_request(app, int(batch['user_id']))
                try:
                    user = deps.current_user(request)
                    check_role(user, batch['spec']['kind'])
                except HTTPException:
                    storage.set_status(batch['id'], 'waiting')
                    for item in batch['items']:
                        if item['state'] in ('pending', 'active'):
                            storage.item(item['id'], state='blocked', error='账号权限或状态已变化，后续操作已停止')
                    continue
                cursor = _cursors.get(batch['id'], 0) % max(1, len(batch['items']))
                ordered = batch['items'][cursor:] + batch['items'][:cursor]
                for index, item in enumerate(ordered):
                    observing_publication = observe_only and batch['spec']['kind'] == 'publish' and item['state'] == 'blocked' and item['result'].get('upload_id')
                    if item['state'] not in ('pending', 'active', 'dispatching') and not observing_publication:
                        continue
                    if observe_only and item['state'] != 'active' and not observing_publication:
                        continue
                    if item['result'].get('not_before', 0) > time.time():
                        continue
                    try:
                        user = deps.current_user(request)
                        check_role(user, batch['spec']['kind'])
                        await advance(batch, item, app, user, storage, observe_only=observe_only)
                    except Exception as exc:
                        # A transport/5xx failure after dispatch has an ambiguous outcome.
                        saved = next(i for i in storage.get(batch['id'])['items'] if i['id'] == item['id'])
                        if isinstance(exc, HTTPException) and exc.status_code == 429:
                            storage.item(item['id'], state='active' if item['state'] == 'active' else 'pending', stage='等待接口限流恢复', result={**saved['result'], 'not_before': time.time() + 60})
                            continue
                        ambiguous = saved['state'] == 'dispatching' and (not isinstance(exc, HTTPException) or exc.status_code >= 500)
                        state = 'unknown' if ambiguous else 'blocked' if isinstance(exc, HTTPException) and exc.status_code in (401, 403) else 'failed'
                        storage.item(item['id'], state=state, error=_error(exc))
                    dispatched += 1
                    _cursors[batch['id']] = (cursor + index + 1) % max(1, len(batch['items']))
                    if index >= 19:
                        break
                storage.settle(batch['id'])
        except TimeoutError:
            continue
    if dispatched:
        from core import activity
        activity.use_state_dir(get_settings().paths.state_dir)
        activity.mark_activity()
    return dispatched

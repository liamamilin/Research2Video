"""Small live projections of conversation objects; no model calls or workflow writes."""
from __future__ import annotations

import asyncio
from urllib.parse import quote

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from web.services.assistant_application import native
from web.services.assistant_store import AssistantStore


class Context(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['video', 'job', 'report', 'matrix', 'recipe', 'part', 'platform']
    id: str = Field(min_length=1, max_length=500)


def href(kind: str, key: str) -> str:
    encoded = quote(key, safe='')
    return {'video': '/video/' + encoded, 'job': '/jobs/' + encoded,
            'report': '/reports/view?path=' + encoded, 'matrix': '/matrices/' + encoded,
            'recipe': '/video-recipes/' + encoded, 'part': '/video-recipes/parts/' + quote(key, safe='/'),
            'platform': '/platforms?platform=' + encoded}.get(kind, '')


async def object_view(context: dict, request: Request, load_video=None) -> dict:
    kind, key = context['kind'], context['id']
    item = {'kind': kind, 'id': key, 'title': key, 'href': href(kind, key), 'status': '已选中', 'links': [], 'active': False}
    encoded = quote(key, safe='')
    try:
        if kind == 'video':
            data = await load_video(key) if load_video else (await native(request, 'GET', '/api/video/scripts/' + encoded))[1]
            renders = data.get('renders') or []
            latest = next((r for r in renders if r.get('status') == 'COMPLETED'), None)
            current = data.get('version_shown') or {}
            item.update(title=current.get('title') or (data.get('topic') or {}).get('title') or key,
                        status=data['status'], version=data.get('current_version'), video_version=latest.get('version') if latest else None,
                        active=data['status'] in ('RESEARCHING', 'WRITING') or bool(data.get('stage')),
                        error=data.get('error'), links=(data.get('configuration') or {}).get('links', []))
            item['local_video'] = bool((data.get('topic') or {}).get('local_video'))
            if item['local_video']:
                item.update(status='本地视频', links=[])
            item['renders'] = [{k: r.get(k) for k in ('id', 'version', 'status', 'active_stage', 'error_message')} for r in renders[:3]]
            item['active'] |= any(r.get('status') in ('QUEUED', 'RUNNING', 'SUBMITTING', 'DOWNLOADING') for r in renders[:3])
        elif kind == 'job':
            _, data = await native(request, 'GET', '/api/jobs/' + encoded)
            item.update(title=data.get('title') or data.get('name') or key, status='研究中' if data.get('is_running') else '研究任务',
                        active=bool(data.get('is_running')))
        elif kind == 'batch':
            from core.video import autopilot_store
            data = autopilot_store.get_run(key)
            if not data:
                raise ValueError('视频生产批次不存在')
            item.update(title='视频生产批次', href='/?automation=1', status=data['status'],
                        active=data['status'] in ('RUNNING', 'QUEUED'),
                        detail='调研、文稿与出片由既有 workflow 推进；待处理项可打开看板继续。')
            item['tasks'] = [{'id': i['id'], 'title': i.get('title') or i.get('note') or '视频',
                              'status': i['state']} for i in data.get('items', [])]
        elif kind == 'application_batch':
            from web.deps import current_user
            from web.services.assistant_batch_store import BatchStore
            data = BatchStore().get(key, current_user(request))
            item.update(title=data['title'], href=data['href'], status=data['status'], active=data['active'],
                        detail='固定范围，逐项接续；结果未知的提交不会自动重放。')
            item['tasks'] = [{'id': i['id'], 'title': i['title'], 'status': i['state'], 'error': i['error']} for i in data['items']]
        elif kind == 'matrix':
            _, data = await native(request, 'GET', '/api/matrices/' + encoded, query={'reports': False})
            item.update(title=data.get('name') or key, status='矩阵', detail='共用设置由未单独覆盖的任务继承')
            item['members'] = [{'job': m['job'], 'name': m['name'], 'enabled': m.get('enabled', False),
                                'schedule': m.get('schedule'), 'href': href('job', m['job'])} for m in data.get('members', [])]
            item['render'] = data.get('render', {})
            item['detail'] = f"{len(item['members'])} 个研究任务 · {sum(bool((m['schedule'] or {}).get('enabled')) for m in item['members'])} 个周期已启用；任务覆盖保留"
        elif kind in ('recipe', 'part'):
            path = '/api/video/recipes/' + encoded if kind == 'recipe' else '/api/video/recipes/parts/' + quote(key, safe='/')
            _, data = await native(request, 'GET', path)
            item.update(title=data.get('label') or key, status='配方' if kind == 'recipe' else '共享提示词/部件',
                        detail='保存后用于后续生成；已有文稿和视频需要重新生成')
            if data.get('used_by'):
                item['detail'] += ' · 引用：' + '、'.join(data['used_by'])
        elif kind == 'platform':
            if key not in ('bilibili', 'douyin', 'kuaishou', 'xiaohongshu', 'weixin_channels'):
                raise ValueError('未知平台')
            candidates = ['/api/platforms/' + key, '/api/platforms/social/' + key]
            if key == 'bilibili':
                candidates.append('/api/platforms')
            available = request.app.openapi()['paths']
            path = next((p for p in candidates if p in available and 'get' in available[p]), None)
            if not path and '/api/platforms/social/{platform}' in available:
                path = '/api/platforms/social/' + key
            if not path:
                raise ValueError('此平台尚未提供概览入口')
            _, data = await native(request, 'GET', path)
            uploads = data.get('uploads') or data.get('jobs') or []
            if isinstance(uploads, dict):
                uploads = uploads.get('items', [])
            item.update(status='平台管理', detail='登录与验证码在平台页面完成')
            item['tasks'] = [{k: u.get(k) for k in ('id', 'title', 'status', 'error')} for u in uploads[:5]]
            item['active'] = any(str(u.get('status', '')).upper() in ('UPLOADING', 'RUNNING', 'QUEUED', 'PUBLISHING', 'PROCESSING') for u in uploads[:5])
    except (HTTPException, ValueError) as exc:
        item.update(status='暂不可读取', error=str(exc.detail if isinstance(exc, HTTPException) else exc))
    return item


async def workspace(request: Request, user: dict, conversation_id: str, context: dict | None = None, explicit: bool = False) -> dict:
    def read():
        storage = AssistantStore()
        conversation = storage.conversation(user, conversation_id)
        return conversation, storage.operations(user, conversation['id'])
    conversation, operations = await asyncio.to_thread(read)
    return await project(request, user, conversation, operations, context, explicit=explicit)


async def task_center(request: Request, user: dict, limit: int = 20) -> dict:
    data = await asyncio.to_thread(lambda: AssistantStore().recent_work(user, limit))
    result = await project(request, user, {'id': None, 'requests': data['records'], 'refs': [], 'context': None},
                           data['operations'], card_limit=limit)
    return {'tasks': result['tasks'], 'active': result['active'], 'total': data['total'], 'limit': limit}


async def project(request: Request, user: dict, conversation: dict, operations: list[dict],
                  context: dict | None = None, *, explicit: bool = False, card_limit: int = 8) -> dict:
    selected = context if explicit else conversation.get('context')
    refs = list(reversed(conversation.get('refs') or []))
    objects, seen = [], set()
    for ref in ([selected] if selected else []) + [{'kind': r['type'], 'id': r['id']} for r in refs
            if r.get('type') in ('video', 'job', 'report', 'matrix', 'recipe', 'part', 'platform', 'batch')]:
        identity = (ref['kind'], ref['id'])
        if identity not in seen:
            objects.append(ref)
            seen.add(identity)
        if len(objects) == 4:
            break
    from web.services.assistant_tasks import recovered_operations
    recovered = recovered_operations(operations)
    reads = {}
    publication_reads = {}
    publication_catalogs = {}
    publication_ids = {}
    for operation in operations:
        result = operation.get('result') or {}
        targets = [(target['platform'], target['id']) for target in result.get('publication_tasks', [])]
        targets.extend(tuple(ref['id'].split(':', 1)) for ref in result.get('references', [])
                       if ref.get('type') == 'upload' and ':' in ref['id'])
        for platform, key in targets:
            publication_ids.setdefault(platform, set()).add(key)
    research_reads = {}
    manual_reads = {}

    async def load_research(target):
        from web.services.assistant_runs import run_view
        key = (target['source'], target.get('job_name', ''), target['run_id'])
        if key not in research_reads:
            snapshot = None
            if target['source'] == 'manual':
                from types import SimpleNamespace
                from web.services.assistant_tools.work import run_query
                job = target.get('job_name', '')
                if job not in manual_reads:
                    manual_reads[job] = asyncio.create_task(asyncio.to_thread(run_query,
                        SimpleNamespace(source='manual', job_name=job, run_id='', limit=20), request, user))
                snapshot = manual_reads[job]
            research_reads[key] = asyncio.create_task(run_view(target, request, user, snapshot))
        return await research_reads[key]

    async def load_publication(target):
        from web.services.assistant_management import platform_path, publication_task_view
        key = (target['platform'], target['id'])
        if key not in publication_reads:
            async def load():
                platform = target['platform']
                if len(publication_ids.get(platform, ())) < 2:
                    return await publication_task_view(target, request)
                try:
                    if platform not in publication_catalogs:
                        publication_catalogs[platform] = asyncio.create_task(native(request, 'GET', platform_path(platform)))
                    _, catalog = await publication_catalogs[platform]
                    row = next((item for item in catalog.get('uploads', []) if item['id'] == target['id']), None)
                except (HTTPException, ValueError):
                    row = None
                # Old Bilibili receipts can fall outside its bounded overview.
                # Preserve the native single-task lookup for missing entries.
                return await publication_task_view(target, request, row=row)

            publication_reads[key] = asyncio.create_task(load())
        return await publication_reads[key]

    async def load_video(key):
        if key not in reads:
            reads[key] = asyncio.create_task(native(request, 'GET', '/api/video/scripts/' + quote(key, safe='')))
        return (await reads[key])[1]

    shown = await asyncio.gather(*(object_view(ref, request, load_video) for ref in objects))
    changes = []
    from web.services import assistant_tools, jobs_assistant
    specs = assistant_tools.registry()
    labels = {**jobs_assistant.LABELS, **{k: t.label for k, t in specs.items()}}
    for op in operations:
        result = op['result'] or {}
        # Keep query traces out of the main workspace; they remain in chat receipts.
        if op['tool'] in jobs_assistant.READ_TOOLS or (op['tool'] in specs and not specs[op['tool']].write):
            continue
        changes.append({'id': op['id'], 'request_id': op['request_id'], 'tool': op['tool'], 'status': op['status'],
                        'created': op['created'], 'label': result.get('message') or labels.get(op['tool'], '应用操作'),
                        'error': result.get('error'), 'href': result.get('href'), 'submitted': result.get('submitted', False),
                        'impact': result.get('impact', ''), 'fields': result.get('changed_fields', []),
                        'configuration': result.get('configuration_change'), 'recovered_by': recovered.get(op['id'])})
    from web.services.assistant_tasks import video_task
    task_cards = []
    for record in reversed(conversation['requests']):
        actions = [op for op in reversed(operations) if op['request_id'] == record['id']]
        modifications = [change for change in reversed(changes) if change['request_id'] == record['id']]
        from web.services.assistant_batch_store import BatchStore, view as batch_view
        batches = []
        for key in dict.fromkeys(op['result'].get('application_batch_id') for op in actions if op.get('result')):
            if key:
                try:
                    batches.append(batch_view(BatchStore().get(key, user)))
                except ValueError:
                    pass
        preparations = [p for op in actions for p in (op['result'] or {}).get('publication_preparations', [])]
        targets = {(t['platform'], t['id']): t for op in actions for t in (op['result'] or {}).get('publication_tasks', [])}
        # Also track native generic API submissions, including partial success.
        for op in actions:
            for ref in (op['result'] or {}).get('references', []):
                if ref.get('type') == 'upload' and ':' in ref['id']:
                    platform, key = ref['id'].split(':', 1)
                    targets.setdefault((platform, key), {'platform': platform, 'id': key})
        from web.services.assistant_runs import targets as run_targets
        research = {(t['source'], t.get('job_name', ''), t['run_id']): t for op in actions
                    for t in ((op['result'] or {}).get('research_runs') or
                              run_targets(op['tool'], op['arguments'], op['result'] or {}))}
        if not modifications and not preparations and not targets and not research and record['status'] != 'running':
            continue
        publications = await asyncio.gather(*(load_publication(t) for t in targets.values()))
        runs = await asyncio.gather(*(load_research(t) for t in research.values()))
        failures = [f for op in actions for f in (op['result'] or {}).get('failures', [])]
        grouped = {}
        for op in actions:
            task = (op['result'] or {}).get('task') or {}
            # Platform tasks also carry script_id/version. Only workflow receipts
            # describe a generation step and its target version.
            sid = task.get('script_id') if task.get('step') and task.get('target_version') is not None else None
            if sid:
                grouped.setdefault(sid, []).append(op)
        videos = []
        for sid, work in grouped.items():
            # A later regeneration owns its new attempts. Older cards stay tied
            # to the versions and attempts created by their original request.
            later_ops = [op for op in operations if op['request_id'] != record['id'] and op['status'] == 'completed'
                     and op['created'] > work[-1]['created']
                     and (op['result'].get('task') or {}).get('script_id') == sid
                     and (op['result'].get('task') or {}).get('step') in ('write', 'edit', 'research', 'render')]
            later = [op['created'] for op in later_ops]
            target = next((op['result']['task']['target_version'] for op in work if op['result']['task']['step'] in (
                'write', 'edit', 'research')), work[-1]['result']['task']['target_version'])
            superseded = any(op['result']['task']['step'] in ('write', 'edit', 'research')
                             and op['result']['task']['before_version'] < target for op in later_ops)
            try:
                videos.append(video_task(work, await load_video(sid), until=min(later) if later else None, superseded=superseded))
            except (HTTPException, ValueError) as exc:
                videos.append({'script_id': sid, 'status': 'unavailable', 'label': '暂不可读取', 'active': False,
                               'href': href('video', sid), 'error': str(exc.detail if isinstance(exc, HTTPException) else exc)})
        active = record['status'] == 'running' or any(b['active'] for b in batches) or any(v.get('active') for v in videos) or any(p['active'] for p in publications) or any(r['active'] for r in runs)
        status = 'running' if active else 'failed' if failures or any(c['status'] == 'failed' and not c['recovered_by'] for c in modifications) or any(
            v['status'] in ('failed', 'unavailable') for v in videos) or any(p['state'] == 'failed' for p in publications) or any(r['state'] == 'failed' for r in runs) else 'waiting' if record['status'] == 'interrupted' or any(c['status'] == 'started' for c in modifications) or any(
            v['status'] != 'completed' for v in videos) or any(p['state'] != 'completed' for p in publications) or any(r['state'] != 'completed' for r in runs) else 'prepared' if preparations and not publications else 'completed' if videos or publications or runs or not any(
            c['submitted'] for c in modifications) else 'submitted'
        task_cards.append({'id': record['id'], 'message': record['message'], 'created': record['created'],
                           'status': status, 'active': active, 'changes': modifications, 'videos': videos,
                           'preparations': preparations, 'publications': publications, 'failures': failures, 'runs': runs,
                           'conversation_id': record['conversation_id']})
        task_cards[-1]['batches'] = batches
        if not active and batches:
            states = {b['status'] for b in batches}
            task_cards[-1]['status'] = ('failed' if 'failed' in states else 'waiting' if states & {'waiting', 'prepared', 'cancelled'}
                                        else 'completed' if status == 'submitted' and states == {'completed'} else status)
        if len(task_cards) == card_limit:
            break
    return {'conversation_id': conversation['id'], 'context': selected, 'objects': shown,
            'operations': changes[:8], 'tasks': task_cards,
            'active': any(item.get('active') for item in shown) or any(t['active'] for t in task_cards)}

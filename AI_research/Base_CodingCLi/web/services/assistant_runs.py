"""Track exact research executions without starting work or calling a model."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from urllib.parse import quote

from fastapi import HTTPException


def capture(name: str, args: dict) -> dict | None:
    """Capture cancellation's original run before another execution can replace it."""
    job = args.get('job_name') if name == 'cancel_job' else None
    if name == 'operate_application' and args.get('action_id') == 'POST /api/jobs/{name}/cancel':
        job = (args.get('path') or {}).get('name')
    if not job:
        return None
    from web.routes.jobs import _registry
    task = _registry.get(job)
    return {'source': 'manual', 'job_name': job, 'run_id': task.task_id} if task else None


def targets(name: str, args: dict, result: dict, captured: dict | None = None) -> list[dict]:
    data = result.get('result') if isinstance(result.get('result'), dict) else result
    job = result.get('job_name') or args.get('job_name')
    if name == 'run_job' and data.get('task_id'):
        return [{'source': 'manual', 'job_name': job, 'run_id': data['task_id']}]
    if name == 'query_run':
        row = data.get('run') or {}
        key = row.get('run_id') or row.get('id')
        if key:
            return [{'source': args['source'], 'job_name': job or row.get('job_name', ''), 'run_id': key}]
    if name == 'cancel_run' and args.get('run_id'):
        return [{'source': args['source'], 'job_name': job or (data.get('run') or {}).get('job_name', ''), 'run_id': args['run_id']}]
    if name == 'operate_application' and args.get('action_id') == 'POST /api/jobs/{name}/run' and data.get('task_id'):
        return [{'source': 'manual', 'job_name': (args.get('path') or {}).get('name', ''), 'run_id': data['task_id']}]
    return [captured] if captured else []


async def run_view(target: dict, request, user: dict, snapshot=None) -> dict:
    from web.services.assistant_tools.work import run_query
    source, key, job = target['source'], target['run_id'], target.get('job_name', '')
    item = {**target, 'status': 'unavailable', 'state': 'waiting', 'active': False,
            'href': '/jobs/' + quote(job, safe='') if source == 'manual' else '/scheduler',
            'label': '本次运行暂不可核验', 'report': None}
    try:
        data = await snapshot if snapshot is not None else await asyncio.to_thread(
            run_query, SimpleNamespace(source=source, run_id=key, job_name=job, limit=20), request, user)
        if snapshot is not None:
            from web.services.assistant_artifacts import report_reference
            selected = next((r for r in data.get('runs', []) if r['run_id'] == key), None)
            data = {**data, 'run': selected, 'report': report_reference((selected or {}).get('output_path')),
                    'events': data.get('events', []) if (data.get('run') or {}).get('run_id') == key else []}
        row = data.get('run') or {}
        current = data.get('current') or {}
        current_active = current.get('id') == key and bool(current.get('active', current.get('status') == 'running'))
        if not row and current_active:
            row = {'run_id': key, 'status': current['status']}
        if not row:
            item['error'] = '没有找到本次运行记录；不以其他运行代替。'
            return item
        status = row.get('status', 'unknown')
        if source == 'manual' and current_active:
            status = current['status']
        elif source == 'manual' and status == 'unknown':
            status = 'interrupted'
        running = status in ('running', 'queued', 'starting', 'retry')
        # A saved "running" event alone cannot establish that its process survived.
        if source == 'manual' and running:
            running = current.get('id') == key and current.get('active', current.get('status') == 'running')
            if not running:
                status = 'interrupted'
        cancelling = source == 'manual' and (data.get('current') or {}).get('id') == key and bool(
            (data.get('current') or {}).get('active')) and (data.get('current') or {}).get('status') == 'cancelled'
        if cancelling:
            status, running = 'cancelling', True
        if source == 'manual':
            events = [e.get('event') or {} for e in data.get('events', [])]
            error = next((e.get('message') or e.get('error') for e in reversed(events) if e.get('type') == 'error'), None)
            progress = next((e.get('message') or e.get('stage') for e in reversed(events) if e.get('type') == 'progress'), None)
        else:
            error, progress = row.get('error'), row.get('phase')
            item['href'] = data['href']
        label = {'running': '研究中', 'queued': '研究排队中', 'starting': '正在启动研究', 'retry': '等待研究重试', 'success': '研究完成', 'skipped': '本次研究已跳过',
                 'failed': '研究失败', 'cancelled': '本次研究已取消', 'cancelling': '等待本次研究取消完成', 'interrupted': '执行已中断，需核对结果'}.get(status, '本次运行待核对')
        item.update(status=status, state='running' if running else 'completed' if status in ('success', 'cancelled', 'skipped') else
                    'failed' if status == 'failed' else 'waiting', active=running, label=label,
                    title=data.get('title') or job or row.get('job_name') or '研究任务', job_name=job or row.get('job_name', ''),
                    error=error, progress=progress, report=data.get('report'), started_at=row.get('started_at'),
                    cancel_requested=cancelling or bool(row.get('cancel_requested')))
        if status == 'interrupted':
            item['error'] = '本次执行进程已不在；可读取日志和已有报告，不会自动重跑。'
        if status == 'success' and not item['report']:
            item['label'] = '研究已结束，报告文件暂不可用'
    except (HTTPException, ValueError) as exc:
        item['error'] = str(exc.detail if isinstance(exc, HTTPException) else exc)
    return item


class ResearchHandoffs:
    def __init__(self, refs=()):
        self.pending = {(r.get('source'), r.get('job_name', ''), r['id']): r for r in refs if r.get('type') == 'run'}
        self.observed = set()
        self.latest = {key[:2]: key[2] for key in self.pending}

    def probe(self, name, args):
        if name != 'query_run':
            return None
        source, job, key = args.get('source'), args.get('job_name', ''), args.get('run_id')
        key = key or self.latest.get((source, job))
        return next((t for t in self.pending if t[0] == source and (not job or t[1] == job) and (not key or t[2] == key)), None)

    def waiting(self, name, args):
        key = self.probe(name, args)
        return key is not None and key in self.observed

    def record(self, name, args, result):
        for row in result.get('research_runs', []):
            target = (row['source'], row.get('job_name', ''), row['run_id'])
            self.pending[target] = row
            self.latest[target[:2]] = target[2]
        key = self.probe(name, args)
        if key is not None:
            row = result.get('run') or {}
            if row.get('status') in ('running', 'queued', 'starting', 'retry') or bool((result.get('current') or {}).get('active')):
                self.observed.add(key)
            else:
                self.pending.pop(key, None)

    def response(self, actions):
        return {'answer': '研究任务已交给后台执行，报告还未确认完成。工作区和任务中心会跟踪本次运行；可以继续处理其他工作。',
                'actions': actions, 'error': None}

"""Pin assistant requests to workflow versions; project progress without model calls."""
from __future__ import annotations

from datetime import datetime
import json
from urllib.parse import quote

from core.video import store
from web.services.assistant_video_state import duration, review

ACTIVE = {'QUEUED', 'RUNNING', 'SUBMITTING', 'DOWNLOADING'}


def capture(name: str, arguments: dict) -> dict | None:
    """Save the baseline before dispatch, including renders that predate this request."""
    sid, step = arguments.get('script_id'), ''
    if name == 'continue_video':
        step = 'workflow'
    elif name == 'create_video':
        step = 'write'
    elif name == 'manage_video':
        step = {'restyle': 'write', 'approve': 'approve', 'render': 'render', 'recheck': 'recheck'}.get(arguments.get('action'), '')
    elif name == 'operate_application':
        action = arguments.get('action_id', '')
        sid = (arguments.get('path') or {}).get('script_id')
        if action == 'POST /api/video/scripts/{script_id}/steps/{step}/rerun':
            step = (arguments.get('path') or {}).get('step', '')
        else:
            step = {'POST /api/video/scripts': 'write', 'PUT /api/video/scripts/{script_id}': 'edit',
                    'POST /api/video/scripts/{script_id}/restyle': 'write',
                    'POST /api/video/scripts/{script_id}/approve': 'approve',
                    'POST /api/video/scripts/{script_id}/render': 'render',
                    'POST /api/video/scripts/{script_id}/recheck': 'recheck'}.get(action, '')
    if not step:
        return None
    script = store.get_script(sid) if sid else None
    version = (script or {}).get('current_version', 0)
    target = version + 1 if step in ('write', 'edit', 'research') or (
        step == 'workflow' and (script or {}).get('status') in ('WRITING', 'RESEARCHING')) else max(1, version)
    renders = store.list_renders(sid) if script else []
    old = next((r for r in renders if r['version'] == version and r['status'] == 'COMPLETED'), None)
    return {'script_id': sid, 'step': step, 'before_version': version, 'target_version': target,
            'before_render_id': old['id'] if old else None,
            'existing_render_ids': [r['id'] for r in renders],
            'report_path': (script or {}).get('report_path')}


def attach(task: dict | None, result: dict) -> None:
    if task is None:
        return
    data = result.get('result') if isinstance(result.get('result'), dict) else result
    sid = task.get('script_id') or data.get('script_id') or data.get('id')
    task = {**task, 'script_id': sid}
    if task['step'] == 'render':
        task['render_id'] = data.get('render_id') or (data.get('id') if 'script_id' in data else None)
    if task['step'] == 'recheck':
        task['recheck_id'] = data.get('recheck') or data.get('id')
    if result.get('batch_id'):
        task['batch_id'] = result['batch_id']
    result['task'] = task


def recovered_operations(operations: list[dict]) -> dict[str, str]:
    """A later save can resolve a failed identical config save, without erasing its receipt."""
    pending, recovered = {}, {}
    for op in sorted(operations, key=lambda item: item['created']):
        args = op['arguments']
        action = args.get('action_id', '')
        if op['tool'] != 'operate_application' or not action.startswith(('PUT ', 'PATCH ')) or not any(
            action.startswith(method + prefix) for method in ('PUT ', 'PATCH ')
            for prefix in ('/api/video/recipes/', '/api/matrices/', '/api/production/', '/api/jobs/')):
            continue
        if not isinstance(args.get('body'), dict):
            continue
        body = dict(args['body'])
        body = {k: v for k, v in body.items() if not k.startswith('expected_')}
        if isinstance(body.get('content'), str):
            from core.yaml_cache import load_yaml
            import yaml
            try:
                body['content'] = load_yaml(body['content'])
            except (ValueError, yaml.YAMLError):
                pass
        try:
            signature = json.dumps([op['request_id'], action, args.get('path'), body], ensure_ascii=False, sort_keys=True, default=str)
        except TypeError:
            continue
        if op['status'] == 'failed':
            pending.setdefault(signature, []).append(op['id'])
        elif op['status'] == 'completed':
            for failed in pending.pop(signature, []):
                recovered[failed] = op['id']
    return recovered


def _epoch(value: str) -> float:
    try:
        return datetime.fromisoformat(value).timestamp()
    except (TypeError, ValueError):
        return 0


def video_task(operations: list[dict], data: dict, *, until: float | None = None, superseded: bool = False) -> dict:
    """Observe only this request's version/attempt, even after a later rewrite."""
    tasks = [op['result']['task'] for op in operations]
    baseline = tasks[0]
    target = next((t['target_version'] for t in tasks if t['step'] in ('write', 'edit', 'research')), tasks[-1]['target_version'])
    sid = baseline['script_id']
    version = data.get('version_shown') or {}
    if version.get('version') != target:
        version = store.get_version(sid, target) or {}
    if superseded:
        version = {}
    steps = {t['step'] for t in tasks}
    render_requested = bool(steps & {'render', 'workflow'})
    render_id = next((t['render_id'] for t in reversed(tasks) if t.get('render_id')), None)
    candidates = [r for r in data.get('renders', []) if r.get('version') == target]
    if render_id:
        candidates = [r for r in candidates if r['id'] == render_id]
    elif render_requested:
        old = set(baseline['existing_render_ids'])
        candidates = [r for r in candidates if r['id'] not in old and (until is None or _epoch(r.get('created_at')) < until)]
        # Continuing an already finished workflow may legitimately reuse its video.
        if not candidates and steps == {'workflow'}:
            candidates = [r for r in data.get('renders', []) if r['id'] in old and r.get('version') == target]
    render = candidates[0] if candidates and render_requested else None
    current = data.get('current_version', 0)
    error = None
    retry = None
    active = False
    failed_op = next((op for op in reversed(operations) if op['status'] == 'failed'), None)
    if failed_op:
        status, error = 'failed', failed_op['result'].get('error') or failed_op['result'].get('message') or '操作未成功'
        retry = failed_op['result']['task']['step']
    elif any(op['status'] == 'started' for op in operations):
        status, active = 'submitting', True
    elif render and render['status'] in ('FAILED', 'CANCELLED'):
        status, error, retry = 'failed', render.get('error_message') or '视频制作未完成', 'render'
    elif render and render['status'] == 'COMPLETED':
        status = 'completed'
    elif render and render['status'] in ACTIVE:
        status, active = 'rendering', True
    elif current <= target and (data.get('error') or data.get('status') == 'FAILED'):
        status, error = 'failed', data.get('error') or '生成未完成'
        retry = 'write' if not version else 'workflow' if render_requested else 'audit'
    elif not version:
        status, active = ('writing', True) if data.get('status') in ('WRITING', 'RESEARCHING') else ('waiting', False)
    elif render_requested:
        status, active = ('waiting', False) if data.get('status') == 'PLAN_READY' else ('render_pending', current == target)
    elif data.get('stage') and current == target:
        status, active = 'reviewing', True
    else:
        status = 'completed'
    if steps == {'recheck'}:
        key = tasks[-1].get('recheck_id')
        recheck = next((r for r in data.get('rechecks', []) if r['id'] == key), None)
        if recheck and recheck['status'] in ACTIVE:
            status, active = 'reviewing', True
        elif recheck and recheck['status'] == 'FAILED':
            status, error, retry = 'failed', recheck.get('error') or '发布前复核失败', 'recheck'
    batch_id = next((t['batch_id'] for t in reversed(tasks) if t.get('batch_id')), None)
    if batch_id and status not in ('completed', 'failed'):
        from core.video import autopilot_store
        batch = autopilot_store.get_run(batch_id)
        item = next((i for i in (batch or {}).get('items', []) if i.get('script_id') == sid), None)
        if item and item['state'] in ('HELD', 'FAILED', 'CANCELLED'):
            status, active, error, retry = 'waiting', False, item.get('note') or '视频 workflow 等待处理', 'workflow'
    if data.get('archived') and active:
        status, active, error, retry = 'waiting', False, '文稿已归档，先到详情页恢复后继续。', None
    if superseded:
        status, active, error, retry, render = 'superseded', False, '本次未产出新版本，后续请求已重新发起生成。', None, None
    report = (version.get('meta') or {}).get('report_path') or baseline.get('report_path') or data.get('report_path')
    cell = (version.get('meta') or {}).get('cell') or {}
    assessment = review(version, running=bool(current == target and data.get('stage')))
    latest_video = next((r for r in data.get('renders', []) if r.get('status') == 'COMPLETED'), None)
    label = {'submitting': '正在提交', 'writing': '正在生成文稿', 'reviewing': '正在审校', 'waiting': '等待处理',
             'render_pending': '文稿已生成，等待视频制作', 'rendering': '视频制作中', 'failed': '需要处理',
             'completed': '本次生成已完成', 'superseded': '已由后续请求接手'}.get(status, status)
    if status == 'rendering' and render:
        label = {'QUEUED': '视频排队中', 'SUBMITTING': '正在提交视频', 'DOWNLOADING': '成片回传中'}.get(render['status'], label)
    comparison = None
    if version and baseline['before_version']:
        comparison = f"/video/{sid}?tab=compare&compare={baseline['before_version']}&version={target}"
        if baseline.get('before_render_id'):
            comparison += '&before_render=' + baseline['before_render_id']
        if render:
            comparison += '&render=' + render['id']
    return {'script_id': sid, 'title': version.get('title') or (data.get('topic') or {}).get('title') or sid,
            'status': status, 'label': label, 'active': active, 'error': error, 'retry_step': retry,
            'href': '/video/' + sid, 'target_version': target, 'before_version': baseline['before_version'],
            'current_version': current, 'script_ready': bool(version), 'render_requested': render_requested,
            'render': {k: render.get(k) for k in ('id', 'version', 'status', 'active_stage', 'error_message')} if render else None,
            'previous_video_version': latest_video.get('version') if latest_video else None,
            'report': {'path': report, 'href': '/reports/view?path=' + quote(report, safe='')} if report else None,
            'recipe': cell.get('recipe') or data.get('column_key'), 'recipe_sha256': cell.get('resolved_sha256'),
            'review': assessment, 'duration': duration(version),
            'compare_href': comparison, 'script_href': '/video/' + sid + '?tab=draft&version=' + str(target),
            'video_href': '/video/' + sid + '?tab=video&version=' + str(target) + ('&render=' + render['id'] if render else '')}

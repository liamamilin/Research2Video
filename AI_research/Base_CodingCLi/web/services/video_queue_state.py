"""Read-only queue phases derived from current scripts and current-version renders."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta

from core.video import store


LABELS = {
    'queued_script': '等待研究/写稿', 'researching': '研究中', 'writing': '写稿中',
    'processing': '文稿处理中', 'waiting_plan': '等待方案确认', 'waiting_approval': '等待定稿',
    'queued_render': '等待出片', 'preparing_render': '准备出片', 'rendering': '渲染中',
    'downloading': '成片回传中', 'done': '成片完成', 'held': '待处理',
    'failed': '失败', 'cancelled': '已取消', 'unknown': '等待状态核实',
}
EXECUTING = frozenset(('researching', 'writing', 'processing', 'preparing_render', 'rendering', 'downloading'))
QUEUED = frozenset(('queued_script', 'queued_render'))
WAITING = frozenset(('waiting_plan', 'waiting_approval'))


def phase(item: dict, script: dict | None, renders: list[dict]) -> dict:
    terminal = {'DONE': 'done', 'HELD': 'held', 'FAILED': 'failed', 'CANCELLED': 'cancelled'}
    if item['state'] in terminal:
        key = terminal[item['state']]
    elif not item.get('script_id'):
        key = 'queued_script'
    elif script is None:
        key = 'unknown'
    elif script['status'] == 'FAILED':
        key = 'failed'
    elif script.get('stage') == '排队':
        key = 'queued_script'
    elif script['status'] in ('RESEARCHING', 'WRITING', 'PLAN_READY'):
        key = {'RESEARCHING': 'researching', 'WRITING': 'writing', 'PLAN_READY': 'waiting_plan'}[script['status']]
    elif script.get('stage'):
        key = 'processing'
    else:
        current = [r for r in renders if r['version'] == script['current_version']]
        active = next((r for r in current if r['status'] in store.ACTIVE_RENDER_STATUSES), None)
        if active:
            key = {'QUEUED': 'queued_render', 'SUBMITTING': 'preparing_render',
                   'RUNNING': 'rendering', 'DOWNLOADING': 'downloading'}.get(active['status'], 'unknown')
        elif any(r['status'] == 'COMPLETED' for r in current):
            key = 'done'
        elif current and current[0]['status'] in ('FAILED', 'CANCELLED'):
            key = 'held'
        else:
            key = 'queued_render' if script['status'] == 'APPROVED' else 'waiting_approval'
    return {'stage': key, 'label': LABELS[key], 'executing': key in EXECUTING, 'queued': key in QUEUED}


def snapshot(recent_runs: list[dict], *, now: datetime | None = None) -> dict:
    """Aggregate every active batch; never infer queue size from the latest ten."""
    now = now or datetime.now().astimezone()
    recent_ids = [r['id'] for r in recent_runs]
    scripts, renders = {}, defaultdict(list)
    with store.connect() as db:
        active = [dict(row) for row in db.execute(
            "SELECT id,stage,created_at FROM autopilot_runs WHERE status='RUNNING'")]
        active_ids = {row['id'] for row in active}
        ids = list(dict.fromkeys([*recent_ids, *sorted(active_ids)]))
        items = []
        for start in range(0, len(ids), 500):
            group = ids[start:start + 500]
            marks = ','.join('?' for _ in group)
            items.extend(dict(row) for row in db.execute(
                'SELECT id,run_id,state,script_id,render_id FROM autopilot_items '
                f'WHERE run_id IN ({marks})', group))
        script_ids = sorted({r['script_id'] for r in items if r['script_id']})
        for start in range(0, len(script_ids), 500):
            group = script_ids[start:start + 500]
            marks = ','.join('?' for _ in group)
            scripts.update((r['id'], dict(r)) for r in db.execute(
                f'SELECT id,status,stage,current_version FROM scripts WHERE id IN ({marks})', group))
            for row in db.execute('SELECT id,script_id,version,status FROM renders '
                                  f'WHERE script_id IN ({marks}) ORDER BY created_at DESC,id DESC', group):
                renders[row['script_id']].append(dict(row))
        today = now.date().isoformat()
        tomorrow = (now.date() + timedelta(days=1)).isoformat()
        completed = db.execute("SELECT count(*) FROM renders WHERE status='COMPLETED' "
                               'AND completed_at>=? AND completed_at<?', (today, tomorrow)).fetchone()[0]
    progress = {item['id']: phase(item, scripts.get(item['script_id']), renders.get(item['script_id'], []))
                for item in items}
    counts = Counter(progress[item['id']]['stage'] for item in items if item['run_id'] in active_ids)
    # Batch creation is durable. Do not label it as time spent in a particular
    # stage, since transitions and retries may have happened since creation.
    oldest = min((r['created_at'] for r in active), default=None)
    return {'items': progress, 'summary': {
        'checked_at': now.isoformat(timespec='seconds'), 'active_batches': len(active),
        'preparing_batches': sum(r['stage'] == 'TOPICS' for r in active),
        'executing_items': sum(counts[k] for k in EXECUTING),
        'queued_items': sum(counts[k] for k in QUEUED),
        'waiting_items': sum(counts[k] for k in WAITING),
        'attention_items': sum(counts[k] for k in ('held', 'failed', 'unknown')),
        'phase_counts': dict(counts), 'oldest_batch_created_at': oldest,
        'completed_renders_today': completed, 'day': today,
        'basis': '批次与当前业务记录；在途批次数不等于同时执行的任务数',
    }}

"""Small model-facing views; native results and durable receipts stay complete."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), default=str)


def compact_schema(value):
    """Remove generated display titles, preserving properties actually named title."""
    if isinstance(value, list):
        return [compact_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {key: deepcopy(item) if key in ('default', 'enum', 'const', 'examples') else compact_schema(item)
            for key, item in value.items()
            if not (key == 'title' and isinstance(item, str))}


def _pick(value: dict, keys: tuple[str, ...]) -> dict:
    return {key: value[key] for key in keys if key in value}


def _task(row: dict, operation_id: str, index: int) -> dict:
    result = dict(row)
    result['runs'] = [_pick(item, ('source', 'job_name', 'run_id', 'status', 'state', 'active',
        'href', 'report', 'error', 'progress', 'started_at', 'cancel_requested')) for item in row.get('runs', [])]
    result['videos'] = [_pick(item, ('script_id', 'title', 'status', 'active', 'error', 'retry_step',
        'href', 'target_version', 'current_version', 'script_ready', 'render_requested', 'render',
        'previous_video_version')) for item in row.get('videos', [])]
    # Account identity, per-item failures and recovery decisions must survive a brief view.
    result['publications'] = [_pick(item, ('platform', 'id', 'account_uid', 'account_name', 'href',
        'active', 'state', 'status', 'title', 'script_id', 'version', 'render_id', 'phase', 'error',
        'url', 'bvid', 'work_id', 'needs_attention', 'verification', 'recovery'))
        for item in row.get('publications', [])]
    nested = {}
    for field in ('runs', 'videos', 'publications', 'failures'):
        values = result.get(field, [])
        if len(values) <= 5:
            continue
        statuses = {}
        for value in values:
            status = str(value.get('status') or value.get('state') or 'unknown')
            statuses[status] = statuses.get(status, 0) + 1
        result[field] = values[:5]
        nested[field] = {'total_items': len(values), 'shown_items': 5, 'complete': False,
            'status_counts': statuses, 'active_count': sum(bool(value.get('active')) for value in values),
            'next_read': {'operation_id': operation_id, 'result_path': f'/items/{index}/{field}',
                          'offset': 5, 'limit': 20}}
    if nested:
        result['_model_view'] = nested
    return result


def model_result(tool: str, arguments: dict, result: dict, operation_id: str | None,
                 *, index_offset: int = 0) -> dict:
    """Project only known list tools. Detail reads, writes and small results are untouched."""
    if not operation_id or not isinstance(result, dict) or len(dumps(result)) < 8000:
        return result
    field, limit = None, 20
    if tool == 'list_jobs':
        field, limit = 'items', 40
    elif tool == 'query_video' and arguments.get('action', 'board') == 'board':
        field = 'items'
    elif tool == 'query_assistant_tasks':
        field = 'items'
    elif tool == 'query_schedules' and arguments.get('action', 'list') == 'list':
        field, limit = 'jobs', 40
    if field is None or not isinstance(result.get(field), list):
        return result
    rows = result[field]
    if any(not isinstance(row, dict) for row in rows):
        return result
    if tool == 'query_assistant_tasks' and any(
            not isinstance(row.get(key, []), list) or
            any(not isinstance(item, dict) for item in row.get(key, []))
            for row in rows for key in ('runs', 'videos', 'publications', 'failures')):
        return result
    view = deepcopy(result)
    shown = []
    for index, row in enumerate(rows[:limit]):
        if tool == 'list_jobs':
            row = {key: value for key, value in row.items() if key not in ('keywords', 'output_template', 'file_mtime')}
            if isinstance(row.get('state'), dict):
                row['state'] = {key: value for key, value in row['state'].items() if key != 'last_usage'}
        elif tool == 'query_video':
            row = _pick(row, ('script_id', 'title', 'column_key', 'column_label', 'created_at',
                'updated_at', 'status', 'stage', 'version', 'has_video', 'has_previous_video',
                'matrix', 'matrix_name', 'job', 'action', 'note', 'error', 'href'))
        elif tool == 'query_assistant_tasks':
            row = _task(row, operation_id, index + index_offset)
        elif tool == 'query_schedules':
            row = _pick(row, ('id', 'job_name', 'title', 'job_label', 'schedule', 'timezone',
                'enabled', 'missed_policy', 'max_retries', 'version', 'backend', 'next_runs',
                'next_run_at', 'pending_due_at', 'last_run', 'editable'))
            if isinstance(row.get('last_run'), dict):
                row['last_run'] = {key: value for key, value in row['last_run'].items() if key != 'usage'}
        shown.append(row)
    view[field] = shown
    view['_model_view'] = {
        'operation_id': operation_id, 'result_path': '/' + field,
        'fields': 'brief; original receipt retains all fields', 'fields_complete': False, 'total_items': len(rows),
        'shown_items': len(shown), 'complete': len(shown) == len(rows),
        'detail_read': {'operation_id': operation_id, 'result_path': '/' + field, 'offset': 0, 'limit': limit},
    }
    if len(shown) < len(rows):
        view['_model_view']['next_read'] = {
            'operation_id': operation_id, 'result_path': '/' + field, 'offset': len(shown),
            'limit': limit, 'view': 'brief'}
    return view


def receipt_page(receipt: dict, path: str, offset: int, limit: int, max_chars: int,
                 *, view: str = 'full') -> dict:
    """Read a JSON Pointer inside an owned receipt without rerunning its operation."""
    value = receipt.get('result')
    if path and not path.startswith('/'):
        raise ValueError('result_path 使用 JSON Pointer，例如 /items 或 /items/0')
    try:
        for part in path.split('/')[1:] if path else []:
            key = part.replace('~1', '/').replace('~0', '~')
            if isinstance(value, list):
                if not key.isdecimal():
                    raise ValueError('数组路径需要非负索引')
                value = value[int(key)]
            elif isinstance(value, dict):
                value = value[key]
            else:
                raise ValueError('路径不能继续展开')
    except (KeyError, IndexError) as exc:
        raise ValueError('操作回执中不存在此路径') from exc
    serialized = dumps(value)
    response = {'operation_id': receipt['id'], 'source_tool': receipt['tool'],
                'source_status': receipt['status'], 'result_path': path,
                'sha256': hashlib.sha256(serialized.encode()).hexdigest(), 'offset': offset}
    if isinstance(value, list):
        if offset > len(value):
            raise ValueError('offset 超出回执数组范围')
        end = min(len(value), offset + limit)
        items = value[offset:end]
        # Subsequent list pages can use the same briefs instead of restoring a
        # complete UI snapshot to the prompt. Explicit detail reads remain full.
        if view == 'brief' and path in ('/items', '/jobs'):
            field = path[1:]
            projected = model_result(receipt['tool'], receipt.get('arguments') or {},
                {field: items}, receipt['id'], index_offset=offset)
            items = projected[field]
            end = offset + len(items)
            response['fields_complete'] = '_model_view' not in projected
        response.update(items=items, total=len(value), end_offset=end,
                        next_offset=end if end < len(value) else None, complete=end >= len(value))
    else:
        if offset > len(serialized):
            raise ValueError('offset 超出回执内容范围')
        end = min(len(serialized), offset + max_chars)
        response.update(format='json_fragment', content=serialized[offset:end], total_chars=len(serialized),
                        end_offset=end, next_offset=end if end < len(serialized) else None,
                        complete=end >= len(serialized))
    return response


def history_actions(actions: list[dict], *, brief: bool) -> list[dict]:
    """Older receipts retain their outcome and target identity, rather than duplicate snapshots."""
    if not brief:
        return actions
    result = []
    for action in actions:
        if not action.get('operation_id') or action.get('input_card') or action.get('ok') is not True:
            result.append(action)
            continue
        row = dict(action)
        if 'detail' in row:
            row.pop('detail')
            row['detail_in_receipt'] = True
        refs = []
        seen = set()
        for ref in row.get('references', []):
            key = (ref.get('type'), ref.get('id'), ref.get('source'))
            if key in seen:
                continue
            seen.add(key)
            refs.append({key: value for key, value in ref.items() if key != 'title'})
        if 'references' in row:
            row['references'] = refs
        result.append(row)
    return result

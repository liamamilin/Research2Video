"""Business meanings shared by assistant queries and live task cards."""
from __future__ import annotations


def review(version: dict, *, running: bool = False) -> dict:
    checks = version.get('checks') or (version.get('meta') or {}).get('checks') or {}
    summary = checks.get('summary') or {}
    if running:
        state, label = 'running', '审校中'
    elif checks.get('model_checks_stale'):
        state, label = 'stale', '修改后尚未重新审校'
    elif checks.get('mode') == 'on_request' and not checks.get('model_ran'):
        state, label = 'not_requested', '按需审校，尚未运行'
    elif not checks.get('model_ran'):
        state, label = 'not_run', '尚未审校'
    elif not checks.get('audits_complete'):
        state, label = 'incomplete', '审校尚未全部完成'
    else:
        state, label = 'completed', '审校完成'
    verdict = None
    if state == 'completed':
        verdict = 'findings' if summary.get('serious') or summary.get('must') else 'no_blocking_findings'
    return {'state': state, 'label': label, 'verdict': verdict,
            'summary': summary if state == 'completed' else None,
            'note': '审校只描述已执行的检查；未运行或未完成不代表文稿质量不合格。'}


def duration(version: dict) -> dict:
    length = version.get('length_check') or {}
    return {'estimated_seconds': length.get('estimated_seconds'), 'target_minutes': length.get('target_minutes'),
            'binding': False, 'kind': 'reference', 'note': '配方时长是参考值，偏离不代表生成失败或质量不合格。'}


def model_detail(data: dict) -> dict:
    """Retain the manuscript while removing misleading legacy verdict fields."""
    result = dict(data)
    shown = dict(data.get('version_shown') or {})
    if not shown:
        return result
    assessment = review(shown, running=bool(data.get('stage')))
    checks = dict(shown.get('checks') or {})
    checks['summary'] = assessment['summary']
    shown['checks'] = checks
    meta = dict(shown.get('meta') or {})
    if isinstance(meta.get('checks'), dict):
        meta['checks'] = {**meta['checks'], 'summary': assessment['summary']}
    shown.update(meta=meta, length_check=duration(shown), review=assessment)
    result.update(version_shown=shown, review=assessment, duration=shown['length_check'])
    # List/history lengths have the same soft meaning as the selected version.
    if isinstance(data.get('versions'), list):
        result['versions'] = [{**v, 'length_check': duration(v)} for v in data['versions']]
    return result

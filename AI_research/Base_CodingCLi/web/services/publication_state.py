"""Explain native publication receipts and how to continue the same task."""
from urllib.parse import urlencode

from core.social.verification import STATE_LABELS


def task_href(platform: str, key: str, row: dict) -> str:
    return '/platforms?' + urlencode({'tab': 'uploads', 'platform': platform, 'task': key,
                                     'from_script': row.get('script_id') or '', 'version': row.get('version') or ''})


def resume_action(platform: str, row: dict) -> str | None:
    """Select an existing native action; never create another upload."""
    status = row.get('status')
    if status in ('FAILED', 'CANCELLED') and (row.get('can_retry') is True or platform == 'bilibili'):
        return 'retry'
    if status in ('SUBMITTED', 'UNKNOWN', 'NEEDS_RECONCILIATION'):
        return 'reconcile'
    return None


def describe(platform: str, key: str, row: dict) -> dict:
    status = row.get('status', '')
    verification = row.get('verification') or {}
    native_state = verification.get('state', '')
    href = task_href(platform, key, row)
    labels = {'PUBLISHED': '已公开', 'SUBMITTED': '已投稿，等待公开确认', 'UNKNOWN': '投稿结果待核对',
              'NEEDS_RECONCILIATION': '投稿结果待核对', 'FAILED': '投稿未完成', 'CANCELLED': '已取消',
              'QUEUED': '等待上传', 'RUNNING': '正在处理', 'UPLOADING': '正在上传',
              'PUBLISHING': '正在发布', 'VERIFYING': '正在核对', 'PROCESSING': '平台处理中'}
    label = STATE_LABELS.get(native_state, labels.get(status, status)) if status in ('SUBMITTED', 'UNKNOWN') else labels.get(status, status)
    recovery = None
    if verification.get('exhausted') and status in ('SUBMITTED', 'UNKNOWN'):
        recovery = {'kind': 'check', 'label': '继续核对原投稿', 'href': href,
                    'message': verification.get('reason') or '自动回查未解决，原投稿与回执已保留。', 'resumable': True}
    elif native_state == 'unavailable' and status in ('SUBMITTED', 'UNKNOWN'):
        recovery = {'kind': 'connection', 'label': '检查平台连接', 'href': href,
                    'message': '回查暂不可用。请检查原账号登录和平台验证，处理后继续核对原投稿。', 'resumable': True}
    elif native_state in ('private', 'rejected') and status in ('SUBMITTED', 'UNKNOWN', 'FAILED'):
        recovery = {'kind': 'platform', 'label': '处理平台作品', 'href': href,
                    'message': '作品尚未公开，请在创作中心查看可见范围或审核意见；调整后核对原作品。',
                    'resumable': status in ('SUBMITTED', 'UNKNOWN')}
    elif status in ('FAILED', 'CANCELLED') and resume_action(platform, row) == 'retry':
        recovery = {'kind': 'retry', 'label': '重试原投稿任务', 'href': href,
                    'message': '这次任务已确认没有提交，可沿用原版本和发布包重试。', 'resumable': True}
    elif status in ('UNKNOWN', 'NEEDS_RECONCILIATION') or status == 'FAILED':
        recovery = {'kind': 'check', 'label': '核对原投稿', 'href': href,
                    'message': '请核对已有回执和平台作品；当前没有确认可重新投稿。',
                    'resumable': status in ('UNKNOWN', 'NEEDS_RECONCILIATION')}
    attention = bool(recovery) and (verification.get('exhausted') or native_state in ('unavailable', 'private', 'rejected')
                                  or status in ('FAILED', 'UNKNOWN', 'NEEDS_RECONCILIATION') and not verification.get('automatic'))
    active = not attention and (status in ('QUEUED', 'RUNNING', 'UPLOADING', 'PUBLISHING', 'VERIFYING', 'PROCESSING', 'RECONCILING')
                               or bool(verification.get('running', verification.get('automatic')))
                               or platform == 'bilibili' and status == 'SUBMITTED')
    state = 'completed' if status in ('PUBLISHED', 'CANCELLED') else 'failed' if status == 'FAILED' else 'running' if active else 'waiting'
    return {'label': label, 'state': state, 'active': active, 'needs_attention': attention,
            'verification': verification, 'recovery': recovery, 'url': row.get('url', '')}

"""Read-only verification contracts shared by the social publishers.

No code in this module submits, retries or edits a platform work. Discovery
requires an exact upload fingerprint, or a unique title/time/duration match.
"""
from __future__ import annotations

import hashlib
import math
import unicodedata
from urllib.parse import urlsplit



CHECK_DELAYS = (15, 30, 60, 120, 300, 600, 1800, 3600)
EVIDENCE = {'kuaishou': 'kuaishou_creator_public', 'xiaohongshu': 'xiaohongshu_creator_public'}
STATE_LABELS = {
    'published': '已公开', 'reviewing': '平台审核中', 'processing': '平台处理中',
    'scheduled': '等待定时发布', 'private': '仅自己可见', 'rejected': '平台审核未通过',
    'missing': '暂未找到匹配作品', 'ambiguous': '找到多个候选，需核对作品链接',
    'unknown': '平台状态暂未确认', 'unavailable': '暂时无法回查，请核对平台登录',
}


def delay(attempts: int) -> int:
    return CHECK_DELAYS[min(max(attempts - 1, 0), len(CHECK_DELAYS) - 1)]


def fingerprint(value: object) -> str:
    """Persist an opaque upload correlation as a hash, never an asset URL/token."""
    if not isinstance(value, str) or not value or len(value) > 4096:
        return ''
    return hashlib.sha256(value.encode()).hexdigest()


def asset_fingerprints(value: object) -> list[str]:
    """Match an opaque cover key or its unsigned URL path, without retaining URLs."""
    if not isinstance(value, str) or not value or len(value) > 4096:
        return []
    try:
        parts = urlsplit(value)
    except ValueError:
        return []
    if parts.scheme:
        return [fingerprint('asset-path:' + parts.path)] if parts.scheme == 'https' and len(parts.path) >= 24 else []
    return [fingerprint(value)]


def submission_correlations(body: object) -> list[str]:
    """Extract only cover identities from the final request; ignore all auth keys."""
    values = set()
    def visit(value, depth=0):
        if not isinstance(value, dict) or depth > 3:
            return
        for key in ('coverKey','unPublishCoverKey','cover','coverUrl','cover_url'):
            item = value.get(key)
            values.update(asset_fingerprints(item))
            if isinstance(item, dict):
                visit(item, depth + 1)
        if isinstance(value.get('data'), dict):
            visit(value['data'], depth + 1)
    visit(body)
    return sorted(values)


def text(value: object) -> str:
    return ' '.join(unicodedata.normalize('NFKC', value).split()) if isinstance(value, str) else ''


def seconds(value: object) -> float | None:
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        return None
    return float(value)


def timestamp(value: object) -> float | None:
    number = seconds(value)
    if number and number > 10**12:
        number /= 1000
    return number if number and 946684800 <= number < 4102444800 else None


def select_work(rows: list[dict], lookup: dict) -> tuple[dict | None, str]:
    """Rows must already be validated as belonging to the exact signed-in UID."""
    target = lookup.get('work_id', '')
    if target:
        matches = [row for row in rows if row.get('work_id') == target]
    else:
        started = timestamp(lookup.get('submitted_at'))
        duration = seconds(lookup.get('duration_seconds'))
        def near_submission(row):
            when = timestamp(row.get('published_at'))
            actual = seconds(row.get('duration_seconds'))
            return (started is not None and duration is not None and when is not None and actual is not None
                    and started - 120 <= when <= started + 900 and abs(actual - duration) <= 2)
        correlations = set(lookup.get('correlations') or []) - {''}
        matches = [row for row in rows if near_submission(row) and correlations.intersection(row.get('correlations') or [])]
        if not matches:
            title = text(lookup.get('title'))
            # A title by itself, nearest timestamp, or rounded numeric work ID
            # cannot associate an external post with one of our documents.
            matches = [row for row in rows if title and text(row.get('title')) == title and near_submission(row)]
    unique = {row['work_id']: row for row in matches if row.get('work_id')}
    if any(len({row.get('state') for row in matches if row.get('work_id') == key}) > 1 for key in unique):
        return None, 'ambiguous'
    if len(unique) == 1:
        return next(iter(unique.values())), ''
    return None, 'ambiguous' if len(unique) > 1 else 'missing'


def receipt(platform: str, uid: str, row: dict | None, state: str = 'missing') -> dict:
    state = row.get('state', 'unknown') if row else state
    if state not in STATE_LABELS:
        state = 'unknown'
    work_id = row.get('work_id', '') if row else ''
    domain_path = {'kuaishou': ('www.kuaishou.com', 'short-video'),
                   'xiaohongshu': ('www.xiaohongshu.com', 'explore')}[platform]
    return {'uid': uid, 'work_id': work_id, 'owned': bool(row), 'public': state == 'published',
            'platform_state': state, 'check_reason': STATE_LABELS[state],
            'evidence': EVIDENCE[platform] if state == 'published' else '',
            'published_at': row.get('published_at') if row else None,
            'url': f'https://{domain_path[0]}/{domain_path[1]}/{work_id}' if work_id else ''}


def readonly_request(method: str, url: str, body: dict | None = None) -> bool:
    """The verification browser may issue known lookups, never an unknown POST."""
    try:
        parts = urlsplit(url)
        if parts.scheme != 'https' or parts.port not in (None, 443) or parts.username or parts.password:
            return False
    except (ValueError, TypeError):
        return False
    if method in ('GET', 'HEAD', 'OPTIONS'):
        return True
    if method != 'POST':
        return False
    # First-party read/bootstrap operations required by the signed creator UI.
    # These retrieve anti-abuse configuration; they do not create/edit a note.
    if parts.hostname == 'as.xiaohongshu.com':
        return parts.path in ('/api/sec/v1/scripting', '/api/sec/v1/sbtsource', '/api/sec/v1/shield/webprofile')
    if parts.hostname == 'edith.xiaohongshu.com':
        return parts.path == '/api/redcaptcha/v2/getconfig'
    if parts.hostname == 'cp.kuaishou.com':
        return parts.path in (
            '/rest/cp/works/v2/video/pc/photo/list',
            '/rest/cp/creator/pc/home/infoV2',
            '/rest/v2/creator/pc/authority/account/current',
            '/rest/cp/works/v2/common/pc/fe/kconf',
            '/rest/v2/creator/pc/frontend/kswitch/config',
            '/rest/cp/works/v2/common/pc/current/user',
            '/rest/cp/creator/analysis/pc/home/author/overview',
            '/rest/cp/works/v2/video/pc/home/photo/list',
        )
    return (parts.hostname == 'www.kuaishou.com' and parts.path == '/graphql'
            and isinstance(body, dict) and body.get('operationName') == 'userInfoQuery'
            and body.get('query', '').lstrip().startswith('query '))


async def protect_context(context) -> None:
    async def guard(route):
        request = route.request
        try:
            body = request.post_data_json if request.method == 'POST' else None
        except Exception:
            body = None
        if readonly_request(request.method, request.url, body):
            await route.continue_()
        else:
            await route.abort()
    await context.route('**/*', guard)

"""Read-only verification contracts shared by the social publishers.

No code in this module submits, retries or edits a platform work. Discovery
requires an exact upload fingerprint, or a unique title/time/duration match.
"""
from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from urllib.parse import urlsplit



CHECK_DELAYS = (15, 30, 60, 120, 300, 600, 1800, 3600)
MAX_UNRESOLVED_CHECKS = 8
MAX_TOTAL_CHECKS = 24
MAX_COMMENT_CHECKS = 12
EVIDENCE = {'kuaishou': 'kuaishou_creator_public', 'xiaohongshu': 'xiaohongshu_creator_public'}
STATE_LABELS = {
    'published': '已公开', 'reviewing': '平台审核中', 'processing': '平台处理中',
    'scheduled': '等待定时发布', 'private': '非公开可见', 'rejected': '平台审核未通过',
    'missing': '暂未找到匹配作品', 'ambiguous': '找到多个候选，需核对作品链接',
    'unknown': '平台状态暂未确认', 'unavailable': '暂时无法回查，请核对平台登录',
    'metadata_missing': '本地视频时长暂不可读取，无法关联原投稿',
}


def lookup_revision(lookup: dict) -> str:
    return hashlib.sha256(json.dumps(lookup, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def check_progress(previous: dict, state: str, checked_at: float, revision: str) -> dict:
    """Bound unresolved checks for unchanged input; retain the cumulative history."""
    cycle = int(previous.get('attempts_for_input', 0)) + 1 if previous.get('input_revision') == revision else 1
    attempts = int(previous.get('attempts', 0)) + 1
    # A changed input restarts the per-input count, but total automatic checks stay bounded.
    exhausted = (cycle >= MAX_UNRESOLVED_CHECKS or attempts >= MAX_TOTAL_CHECKS) and state in (
        'missing', 'ambiguous', 'unknown', 'unavailable', 'metadata_missing')
    return {'attempts': attempts, 'attempts_for_input': cycle,
            'input_revision': revision, 'checked_at': checked_at, 'state': state,
            'exhausted': exhausted, 'next_check_at': None if exhausted else checked_at + delay(cycle),
            'reason': STATE_LABELS.get(state, STATE_LABELS['unknown']) +
                      ('；多次回查未解决，已保留原投稿，等待处理后继续核对' if exhausted else '')}


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
        if parts.scheme != 'https' or len(parts.path) < 24:
            return []
        values = {fingerprint('asset-path:' + parts.path)}
        # The submit response may use an opaque object key while the creator
        # list returns that same key under a CDN/bucket prefix. Retain exact
        # long-key hashes; never retain or compare signed URL parameters.
        segments = parts.path.strip('/').split('/')
        for offset in range(len(segments)):
            suffix = '/'.join(segments[offset:])
            if len(suffix) >= 24:
                values.update((fingerprint(suffix), fingerprint('/' + suffix)))
        return sorted(values)
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


METADATA_WINDOW = (-5, 240)
METADATA_DURATION_TOLERANCE = 1.5


def _metadata_in_window(row: dict, submitted_at: object, duration: object) -> bool:
    when, actual = timestamp(row.get('published_at')), seconds(row.get('duration_seconds'))
    started, expected = timestamp(submitted_at), seconds(duration)
    return (None not in (when, actual, started, expected)
            and started + METADATA_WINDOW[0] <= when <= started + METADATA_WINDOW[1]
            and abs(actual - expected) <= METADATA_DURATION_TOLERANCE)


def _caption_compatible(row: dict, title: str) -> bool:
    """A blank or truncated platform caption is compatible; a different caption never is."""
    shown = text(row.get('title'))
    return not shown or (len(shown) >= 6 and title.startswith(shown)) or shown == title


def metadata_work(rows: list[dict], lookup: dict) -> tuple[dict | None, str]:
    """Last-resort match for an accepted upload whose caption and cover cannot be read back.

    Needs one own work published within minutes of the final request, a near-identical
    duration and a compatible caption. A work already bound to another upload, or one
    that another pending upload also fits, is never chosen.
    """
    title, claimed = text(lookup.get('title')), set(lookup.get('claimed') or ())
    fits = [row for row in rows if row.get('work_id') and row['work_id'] not in claimed
            and _metadata_in_window(row, lookup.get('submitted_at'), lookup.get('duration_seconds'))
            and _caption_compatible(row, title)]
    unique = {row['work_id']: row for row in fits}
    if len(unique) != 1:
        return None, 'ambiguous' if unique else 'missing'
    row = next(iter(unique.values()))
    if any(_metadata_in_window(row, peer.get('submitted_at'), peer.get('duration_seconds'))
           for peer in lookup.get('peers') or ()):
        return None, 'ambiguous'
    return {**row, 'match_basis': 'time_duration'}, ''


def select_work(rows: list[dict], lookup: dict, *, metadata_match: bool = False) -> tuple[dict | None, str]:
    """Rows must already be validated as belonging to the exact signed-in UID."""
    selected, reason = _select_work(rows, lookup)
    if selected is None and reason == 'missing' and metadata_match and not lookup.get('work_id'):
        return metadata_work(rows, lookup)
    return selected, reason


def _select_work(rows: list[dict], lookup: dict) -> tuple[dict | None, str]:
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


def lookup_diagnostics(rows: list[dict], lookup: dict) -> dict:
    """Keep only work identifiers and matching metadata, never signed response data."""
    title = text(lookup.get('title'))
    candidates = [row for row in rows if title and text(row.get('title')) == title]
    started, duration = timestamp(lookup.get('submitted_at')), seconds(lookup.get('duration_seconds'))
    metadata_candidates = [row for row in rows if started is not None and duration is not None
                           and timestamp(row.get('published_at')) is not None
                           and seconds(row.get('duration_seconds')) is not None
                           and started - 120 <= timestamp(row['published_at']) <= started + 900
                           and abs(seconds(row['duration_seconds']) - duration) <= 2]
    return {'records_seen': len(rows), 'title_matches': len(candidates),
            'duration_available': seconds(lookup.get('duration_seconds')) is not None,
            'metadata_candidates': [{key: row.get(key) for key in ('work_id', 'title', 'correlations', 'published_at', 'duration_seconds', 'state')}
                                    for row in metadata_candidates[:3]],
            'candidates': [{key: row.get(key) for key in ('work_id', 'published_at', 'duration_seconds', 'state')}
                           for row in candidates[:3]]}


def receipt(platform: str, uid: str, row: dict | None, state: str = 'missing') -> dict:
    state = row.get('state', 'unknown') if row else state
    if state not in STATE_LABELS:
        state = 'unknown'
    work_id = row.get('work_id', '') if row else ''
    domain_path = {'kuaishou': ('www.kuaishou.com', 'short-video'),
                   'xiaohongshu': ('www.xiaohongshu.com', 'explore')}[platform]
    details = {key: value for key, value in (row or {}).get('status_details', {}).items()
               if key in ('tab_status', 'permission_code', 'high_self', 'schedule_post_time')
               and (value is None or type(value) in (int, bool))}
    basis = {'match_basis': row['match_basis']} if row and row.get('match_basis') else {}
    return {'uid': uid, 'work_id': work_id, 'owned': bool(row), 'public': state == 'published', **basis,
            'platform_state': state, 'check_reason': STATE_LABELS[state],
            'evidence': EVIDENCE[platform] if state == 'published' else '',
            'published_at': row.get('published_at') if row else None,
            **({'status_details': details} if details else {}),
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

"""A scoped operating snapshot assembled from the same native business views."""
import time
from urllib.parse import quote

from fastapi import HTTPException

from core.config import load_job
from web.services import assistant_management as management, source_catalog
from web.settings import get_settings


def inspect(a, request, user):
    from web.services.assistant_tools.management import PublishingQuery
    matrix = management.matrix_detail(a.key, user)
    paths = get_settings().paths
    names = [member['job'] for member in matrix['members']]
    sources = source_catalog.catalog(paths.config_dir, paths.jobs_dir, paths.state_dir, job_names=names)
    members = []
    for member in matrix['members']:
        job = load_job(paths.jobs_dir, member['job']) or {}
        configs = [c for c in matrix['production'] if c['job'] == member['job']]
        members.append({**member, 'sources': job.get('sources') or [], 'search': job.get('search', True),
                        'production_keys': [c['key'] for c in configs],
                        'href': '/jobs/' + quote(member['job'], safe='')})
    catalog = management.publishing_query(PublishingQuery(action='catalog'), request, user)['platforms']
    unknown = set(a.platforms) - {p['id'] for p in catalog}
    if unknown:
        raise ValueError('这些平台未接入：' + '、'.join(sorted(unknown)))
    platforms = []
    for platform in catalog:
        if a.platforms and platform['id'] not in a.platforms:
            continue
        row = {k: platform.get(k) for k in ('id', 'name', 'account', 'href')}
        if platform['account'].get('connected'):
            try:
                overview = management.publishing_query(PublishingQuery(action='overview', platform=platform['id'], limit=10), request, user)
                candidates = management.publishing_query(PublishingQuery(action='candidates', platform=platform['id'], matrix=a.key, limit=a.limit), request, user)
                row.update(recent_tasks=overview['tasks'], recent_tasks_scope='当前账号最近至多 10 条，不代表全部队列',
                           candidates=candidates['items'], candidate_count=candidates['total'], next_offset=candidates['next_offset'])
            except (HTTPException, ValueError, OSError) as exc:
                row['error'] = str(exc.detail if isinstance(exc, HTTPException) else exc)
        platforms.append(row)
    return {'at': time.time(), 'matrix': {k: matrix[k] for k in ('key', 'name', 'revision', 'render', 'href')},
            'members': members, 'production': matrix['production'],
            'sources': [s for s in sources['sources'] if s['used_by']], 'source_problems': sources['problems'],
            'platforms': platforms, 'references': matrix['references'], 'href': matrix['href'],
            'scope': '仅检查这个矩阵及当前平台账号；平台近期任务供排障，不表示都属于本矩阵。未修改或启动任何工作。'}

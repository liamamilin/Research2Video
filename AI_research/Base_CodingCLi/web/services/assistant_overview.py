"""Local, read-only operations projection shared by the assistant and its overview UI."""
from __future__ import annotations

from datetime import datetime
import logging
from urllib.parse import quote, urlencode

from fastapi import HTTPException

from core import matrices
from core.config import list_jobs, load_job
from core.state import StateManager
from core.social.registry import active_platforms, PLATFORMS
from core.video import outputs, progress, store
from web.runner.registry import TaskRegistry
from web.services import douyin, platform_manager, social_publishing, managed_scheduler
from web.services.publication_state import describe, task_href
from web.settings import get_settings

logger = logging.getLogger(__name__)
UNGROUPED = '_ungrouped'
VIEWS = ('attention', 'working', 'publish')
PLATFORM_NAMES = {'bilibili': '哔哩哔哩', 'douyin': '抖音',
                  **{key: PLATFORMS[key][0] for key in active_platforms()}}


def _platform_account(platform: str) -> dict:
    value = (platform_manager.account() if platform == 'bilibili' else douyin.account()
             if platform == 'douyin' else social_publishing.account(platform))
    return {key: value[key] for key in ('connected', 'uid', 'name') if key in value}


def _candidates(platform: str) -> list[dict]:
    return (platform_manager.candidates() if platform == 'bilibili' else douyin.candidates()
            if platform == 'douyin' else social_publishing.candidates(platform))


def _uploads(platform: str, owner: str) -> list[dict]:
    if platform == 'bilibili':
        # Unlike the platform UI's most recent 200, counts cover the whole outbox.
        with platform_manager.store().connect() as db:
            return [{key: row[key] for key in ('id', 'script_id', 'version', 'render_id', 'status', 'error')}
                    for row in db.execute('SELECT * FROM uploads WHERE uid=? ORDER BY created_at DESC', (owner,))]
    if platform == 'douyin':
        db = douyin.store()
        checks = douyin.verification_checks(db.list(uid=owner, kind='RECONCILE'))
        return [douyin.public_job(row, comments={}, checks=checks) for row in db.list(uid=owner, kind='UPLOAD')]
    return [social_publishing.public_job(platform, row, comments={})
            for row in social_publishing.store(platform).list(uid=owner, kind='UPLOAD')]


def _at(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        if not value:
            return 0
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
        except ValueError:
            return datetime.strptime(value, '%Y-%m-%dT%H:%M:%S%z').timestamp()
    except (ValueError, TypeError):
        return 0


def overview(user: dict, *, matrix: str = '', platform: str = '', view: str = 'attention',
             offset: int = 0, limit: int = 20) -> dict:
    """Count the complete local scope before paging; never start/sync/retry any work."""
    if view not in VIEWS or offset < 0 or not 1 <= limit <= 50:
        raise HTTPException(422, '总览筛选或分页参数无效')
    if platform and platform not in PLATFORM_NAMES:
        raise HTTPException(422, '这个平台未开放')
    now = datetime.now().astimezone()
    paths = get_settings().paths
    warnings, availability = [], {}

    def read(area, label, fn, fallback):
        try:
            result = fn()
            availability[area] = True
            return result
        except Exception:
            logger.exception('Operations overview could not read %s', area)
            availability[area] = False
            warnings.append(f'{label}暂时无法读取；相关统计不完整，请刷新或进入原页面查看。')
            return fallback

    every = read('matrices', '矩阵', lambda: matrices.load_all(paths.config_dir), {})
    if matrix and matrix != UNGROUPED and matrix not in {k for k, m in every.items() if not m.archived}:
        if availability['matrices']:
            raise HTTPException(404, '矩阵不存在或已归档')
    groups = {k: {'key': k, 'name': m.name, 'href': '/matrices/' + quote(k, safe=''),
                  'jobs': 0, 'attention': 0, 'working': 0, 'publish': 0} for k, m in every.items() if not m.archived}
    groups[UNGROUPED] = {'key': UNGROUPED, 'name': '未归属', 'href': '/jobs',
                         'jobs': 0, 'attention': 0, 'working': 0, 'publish': 0}
    job_matrix = {name: key for key, m in every.items() for name in m.jobs}
    rows: dict[str, list[dict]] = {key: [] for key in VIEWS}

    def add(bucket, key, *, id, kind, title, label, href, detail='', prompt='', **fields):
        if key not in groups:
            return  # Archived matrix, including scripts whose parent is archived.
        row = {'id': id, 'kind': kind, 'title': title, 'label': label, 'detail': detail,
               'matrix': key, 'matrix_name': groups[key]['name'], 'href': href, 'prompt': prompt, **fields}
        rows[bucket].append(row)
        groups[key][bucket] += 1

    profiles = []
    for key, name in PLATFORM_NAMES.items():
        profile = read('account_' + key, name + '账号', lambda key=key: _platform_account(key), {'connected': False})
        profiles.append({'id': key, 'name': name, 'account': profile})
    platform = platform or next((p['id'] for p in profiles if p['account'].get('connected')), 'bilibili')
    selected = next(p for p in profiles if p['id'] == platform)
    profile = selected['account']

    schedules = {'available': user['role'] == 'admin', 'enabled': None, 'paused': None, 'next': None,
                 'href': '/scheduler', 'note': '仅管理员可查看周期安排'}
    latest, plans, scheduled_active = {}, [], []
    if user['role'] == 'admin':
        def schedule_snapshot():
            db = managed_scheduler.store()
            items = db.list()
            with db.connect() as conn:
                active = [dict(r) for r in conn.execute("SELECT * FROM schedule_runs WHERE status IN ('queued','starting','running','retry') ORDER BY created_at DESC")]
            return items, db.latest_runs([p['id'] for p in items]), active
        plans, latest, scheduled_active = read('schedules', '周期安排', schedule_snapshot, ([], {}, []))
        scoped_plans = [p for p in plans if job_matrix.get(p['job_name'], UNGROUPED) in groups
                 and (not matrix or job_matrix.get(p['job_name'], UNGROUPED) == matrix)]
        schedules.update(available=availability['schedules'], enabled=sum(bool(p['enabled']) for p in scoped_plans),
                         paused=sum(not p['enabled'] for p in scoped_plans), note='当前范围内的托管周期；兼容旧计划请进入周期页查看')
        if not availability['schedules']:
            schedules.update(enabled=None, paused=None, note='周期安排暂时无法读取')
        future = [p for p in scoped_plans if p['enabled'] and p['next_run_at'] and p['next_run_at'] > now.timestamp()]
        if future:
            p = min(future, key=lambda p: p['next_run_at'])
            schedules['next'] = {'at': datetime.fromtimestamp(p['next_run_at'], now.tzinfo).isoformat(),
                                 'job': p['job_name'], 'timezone': p['timezone'],
                                 'href': '/scheduler?plan=' + quote(p['id'], safe='')}
    running = read('research_running', '运行中研究', lambda: TaskRegistry().all_running(), {})
    jobs = read('jobs', '研究任务', lambda: {name: load_job(paths.jobs_dir, name) or {} for name in
                list_jobs(paths.jobs_dir) if not name.startswith(('video/', 'vod/'))}, {})
    states = read('research_results', '最近研究结果', lambda: {name: StateManager.get(name) or {} for name in jobs}, {})
    scoped_jobs = enabled_jobs = today_success = today_failed = 0
    for name, config in jobs.items():
        key = job_matrix.get(name, UNGROUPED)
        if key not in groups:
            continue
        groups[key]['jobs'] += 1
        scoped = not matrix or key == matrix
        scoped_jobs += bool(scoped)
        enabled_jobs += bool(scoped and config.get('enabled', True))
        href = '/jobs/' + quote(name, safe='')
        state = states.get(name, {})
        last = {'status': state.get('last_status'), 'at': _at(state.get('last_run_at')), 'error': state.get('last_error') or ''}
        own_plans = [p for p in plans if p['job_name'] == name]
        for p in own_plans:
            run = latest.get(p['id']) or {}
            if _at(run.get('finished_at')) > last['at']:
                last = {'status': run['status'], 'at': _at(run['finished_at']), 'error': run.get('error') or ''}
        if scoped and last['at'] and datetime.fromtimestamp(last['at'], now.tzinfo).date() == now.date():
            today_success += last['status'] == 'success'
            today_failed += last['status'] in ('failed', 'blocked', 'interrupted')
        active_runs = [run for run in scheduled_active if run['job_name'] == name]
        if name in running or active_runs:
            run = active_runs[0] if active_runs else None
            target = '/scheduler?plan=' + quote(run['schedule_id'], safe='') if run else href
            add('working', key, id='job:' + name, kind='research', title=config.get('name') or name,
                label='研究运行中' if not run else {'queued': '研究排队中', 'retry': '等待重试'}.get(run['status'], '研究运行中'),
                href=target, job=name, prompt=f'查看研究任务 {name} 当前这次运行的进度和结果。')
        elif config.get('enabled', True) and last['status'] in ('failed', 'blocked', 'interrupted'):
            add('attention', key, id='job:' + name, kind='research', title=config.get('name') or name,
                label='研究未完成', href=href, detail=last['error'][:400], job=name,
                prompt=f'检查研究任务 {name} 最近一次失败的原因和恢复方式，先分析。')

    def video_snapshot():
        # No narration, full review results, filesystem history, or render request bodies.
        with store.connect() as db:
            scripts = [store._decode(r, store._SCRIPT_JSON) for r in db.execute('SELECT * FROM scripts WHERE archived=0 ORDER BY created_at DESC,id DESC')]
        owners, _ = outputs.script_owners(paths.config_dir, paths.jobs_dir, scripts)
        return scripts, owners, store.current_titles(), store.render_statuses_by_script()
    scripts, owners, titles, renders = read('videos', '文稿与视频', video_snapshot, ([], {}, {}, {}))
    visible = {}
    for script in scripts:
        sid, version = script['id'], script['current_version']
        owner = owners.get(sid, {})
        key = owner.get('matrix') or UNGROUPED
        if owner.get('matrix_archived') or key not in groups:
            continue
        visible[sid] = (script, key)
        title = titles.get(sid) or script.get('topic', {}).get('title') or '未命名文稿'
        state = progress.render_state(script, renders.get(sid, []))
        render = state['render']
        fields = {'id': 'video:' + sid, 'kind': 'video', 'title': title, 'href': f'/video/{sid}?version={version}',
                  'script_id': sid, 'version': version, 'job': owner.get('job') or '',
                  'detail': f'当前文稿 v{version}' + ('；另有旧版成片' if state['has_previous_video'] else '')}
        if render and render['status'] in store.ACTIVE_RENDER_STATUSES:
            add('working', key, **{**fields, 'href': f'/video/{sid}?tab=video&version={version}&render={render["id"]}'},
                label='视频制作中', render_id=render['id'], prompt=f'查看文稿 {sid} v{version} 的视频制作进度。')
        elif script['status'] in ('RESEARCHING', 'WRITING') or script.get('stage'):
            add('working', key, **fields, label='文稿处理中', prompt=f'查看文稿 {sid} 当前生成进度。')
        elif script['status'] != 'APPROVED':
            if script.get('error'):
                fields['detail'] += ' · ' + script['error'][:300]
            add('attention', key, **fields,
                label={'DRAFT': '待定稿', 'PLAN_READY': '待确认方案', 'FAILED': '文稿失败'}.get(script['status'], '文稿待处理'),
                prompt=f'查看文稿 {sid} v{version} 和当前生成配置，分析下一步如何处理。')
        elif not state['has_video']:
            add('attention', key, **fields, label='出片失败' if render and render['status'] == 'FAILED' else '待生成视频',
                prompt=f'查看已定稿文稿 {sid} v{version} 的出片状态和生成设置，分析如何继续。')

    connected = bool(profile.get('connected') and profile.get('uid'))
    availability['publishing'] = connected
    if connected:
        candidates = read('candidates', selected['name'] + '可发布视频', lambda: _candidates(platform), [])
        for item in candidates:
            sid = item['script_id']
            if sid not in visible:
                continue
            script, key = visible[sid]
            version = item['version']
            if script['status'] != 'APPROVED' or version != script['current_version'] or version != script['approved_version']:
                continue
            href = f'/video/{sid}?tab=publish&version={version}&render={item["render_id"]}'
            add('publish', key, id='publish:' + sid, kind='publish', title=item['title'], label='可准备发布',
                href=href, script_id=sid, version=version, render_id=item['render_id'],
                detail=f'文稿与成片均为 v{version} · {selected["name"]} · {profile.get("name") or profile["uid"]}',
                prompt=f'为文稿 {sid} v{version}、成片 {item["render_id"]} 准备{selected["name"]}账号 {profile["uid"]} 的发布包，先预览。')
        uploads = read('uploads', selected['name'] + '投稿状态', lambda: _uploads(platform, profile['uid']), [])
        for item in uploads:
            sid = item.get('script_id')
            if sid not in visible:
                continue
            script, key = visible[sid]
            status = describe(platform, item['id'], item)
            if not status['needs_attention'] and not status['active']:
                continue
            add('attention' if status['needs_attention'] else 'working', key,
                id='upload:' + item['id'], kind='publication', title=titles.get(sid) or '视频投稿', label=status['label'],
                href=task_href(platform, item['id'], item), script_id=sid, version=item['version'],
                detail=f'{selected["name"]} · 投稿 v{item["version"]} / 当前文稿 v{script["current_version"]}' +
                       (' · ' + item['error'][:300] if item.get('error') else ''),
                prompt=f'检查{selected["name"]}账号 {profile["uid"]} 的原投稿任务 {item["id"]} 的状态和恢复方式，先分析。')
    else:
        availability.update(candidates=False, uploads=False)

    groups_list = [g for g in groups.values() if g['key'] != UNGROUPED or any(g[k] for k in ('jobs', *VIEWS)) or matrix == UNGROUPED]
    filtered = {bucket: [row for row in values if not matrix or row['matrix'] == matrix] for bucket, values in rows.items()}
    stats = {bucket: len(values) for bucket, values in filtered.items()}
    # None means unavailable/incomplete, never a reassuring zero after a subsystem failure.
    complete = all(availability.get(k, False) for k in ('matrices', 'jobs', 'research_results', 'research_running', 'videos')) and (
        user['role'] != 'admin' or availability.get('schedules', False))
    if not complete or not availability.get('account_' + platform) or connected and not availability.get('uploads'):
        stats.update(attention=None, working=None)
    if not availability.get('candidates') or not availability.get('videos') or not availability.get('matrices'):
        stats['publish'] = None
    selected_rows = filtered[view]
    for group in groups_list:
        if stats['attention'] is None:
            group.update(attention=None, working=None)
        if stats['publish'] is None:
            group['publish'] = None
    query = {key: value for key, value in {'overview': '1', 'matrix': matrix, 'platform': platform, 'view': view}.items() if value}
    return {'at': now.isoformat(timespec='seconds'), 'date': now.date().isoformat(), 'timezone': str(now.tzinfo),
            'matrix': matrix, 'platform': platform, 'platforms': profiles, 'matrices': groups_list,
            'summary': {**stats, 'matrix_count': sum(k != UNGROUPED and (not matrix or k == matrix) for k in groups)
                        if availability['matrices'] else None,
                        'jobs': scoped_jobs if availability['jobs'] and availability['matrices'] else None,
                        'enabled_jobs': enabled_jobs, 'today_success': today_success if complete else None,
                        'today_failed': today_failed if complete else None},
            'schedules': schedules, 'availability': availability, 'warnings': warnings,
            'scope': '研究与文稿按矩阵统计，未归属单独分组；投稿进度、可发布仅针对所选平台当前账号。未归档内容；每日结果按任务最近一次运行统计，非运行次数。',
            'items': {'view': view, 'rows': selected_rows[offset:offset + limit], 'total': len(selected_rows),
                      'offset': offset, 'next_offset': offset + limit if offset + limit < len(selected_rows) else None,
                      'complete': stats[view] is not None},
            'active': bool(filtered['working']), 'href': '/ask?' + urlencode(query),
            # A broad inventory is not a selection of every video. Keep the workspace focused;
            # exact leaf links remain on the rows until the user chooses one.
            'references': [{'type': 'overview', 'id': urlencode(query), 'title': '全局总览',
                            'href': '/ask?' + urlencode(query)}]}

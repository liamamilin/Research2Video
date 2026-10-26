"""Matrix and publishing workflows over the application's existing business APIs."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, urlencode

from fastapi import HTTPException

from core import matrices
from core.video import production, production_settings, recipes, store, topic_store
from web.services import assistant_application as application
from web.settings import get_settings


def matrix_revision(key: str) -> str:
    paths = get_settings().paths
    matrix = matrices.load_all(paths.config_dir).get(key)
    if matrix is None:
        raise ValueError('矩阵不存在，请先查询矩阵')
    return hashlib.sha256((Path(matrices.directory(paths.config_dir)) / (key + '.yaml')).read_bytes()).hexdigest()


def matrix_detail(key: str, user: dict) -> dict:
    from web.routes.matrices import get_matrix
    paths = get_settings().paths
    data = get_matrix(key, user=user)
    jobs = {m['job'] for m in data['members']}
    configs = [c for c in production.load_all(paths.config_dir, paths.jobs_dir).values() if c.job in jobs]
    every = recipes.expanded(paths.config_dir)
    sources = topic_store.sources(paths.jobs_dir)
    sources = {**{s.job: s for s in sources.values()}, **sources}
    matrix_jobs = {j for m in matrices.load_all(paths.config_dir).values() for j in m.jobs}
    for member in data['members']:
        options = []
        for recipe_key, recipe in every.items():
            if recipe.base != recipe.key or not recipe.usable:
                continue
            # Use the same validator as native production creation, without writing a candidate file.
            checked = production._check('assistant-preview', {'job': member['job'], 'recipe': recipe_key},
                                        paths.config_dir, paths.jobs_dir, every, sources, matrix_jobs)
            if checked.usable:
                options.append({'recipe': recipe_key, 'label': checked.label, 'split': checked.split_name})
        member['production_options'] = options
    data['production'] = [{**c.as_dict(), 'overrides': c.render,
                           'revision': hashlib.sha256((Path(production.directory(paths.config_dir)) / (c.key + '.yaml')).read_bytes()).hexdigest(),
                           'effective_render': production_settings.inherit(data['render'], c.render),
                           'inherited_fields': sorted(set(data['render']) - set(c.render)),
                           'href': f'/matrices/{quote(key, safe="")}/video/{quote(c.job, safe="")}'} for c in configs]
    data.update(revision=matrix_revision(key), href='/matrices/' + quote(key, safe=''),
                render_note='effective_render 合并矩阵与任务设置；未设置的字段继续使用视频引擎默认值。',
                references=[{'type': 'matrix', 'id': key, 'href': '/matrices/' + quote(key, safe=''), 'title': data['name']}])
    return data


def matrix_query(a, request, user):
    if a.key:
        return matrix_detail(a.key, user)
    from web.routes.matrices import list_matrices
    data = list_matrices(user=user)
    return {'matrices': [{k: m[k] for k in ('key', 'name', 'domain', 'render', 'archived', 'errors')} |
                         {'jobs': len(m['members']), 'scheduled': sum(bool(j.get('schedule', {}).get('enabled'))
                            for j in m['members'] if j.get('schedule'))} for m in data['matrices']],
            'ungrouped': data['ungrouped'], 'href': '/matrices'}


def matrix_change(a, request, user):
    patch = a.fields.model_dump(exclude_none=True)
    application.check_credentials(patch)
    if a.action == 'create':
        if not patch.get('name'):
            raise ValueError('创建矩阵需要 name')
        method, values, body = 'POST', {}, {'key': a.key, **patch}
    else:
        current = matrix_detail(a.key, user)
        if not a.expected_revision or a.expected_revision != current['revision']:
            raise ValueError('矩阵配置已经变化，请重新读取 revision 后再修改')
        body = {k: current[k] for k in ('name', 'domain', 'keywords', 'monthly_searches', 'render')}
        body['jobs'] = [m['job'] for m in current['members']]
        if a.action in ('add_jobs', 'remove_jobs'):
            if set(patch) != {'jobs'} or not patch['jobs']:
                raise ValueError('增减成员仅填写 fields.jobs；不会移动其他矩阵的任务')
            body['jobs'] = (list(dict.fromkeys(body['jobs'] + patch['jobs'])) if a.action == 'add_jobs'
                            else [j for j in body['jobs'] if j not in patch['jobs']])
        else:
            if not patch:
                raise ValueError('请填写需要修改的 fields')
            if 'render' in patch:
                patch['render'] = production_settings.inherit(current['render'], patch['render'])
            body.update(patch)
        body['expected_revision'] = a.expected_revision
        method, values = 'PUT', {'key': a.key}
    path = '/api/matrices' + ('/{key}' if values else '')
    result = application.execute(SimpleNamespace(action_id=method + ' ' + path, path=values, query={}, body=body, result_offset=0),
                                 request, user, write=True)
    data = matrix_detail(a.key, user)
    result.update(href=data['href'], references=data['references'], revision=data['revision'], matrix=data,
                  impact='矩阵共用设置用于后续生成，任务的单独覆盖保留；增减成员不会删除研究任务。')
    return result


def configure_production(a, request, user):
    if a.config_key:
        if a.job or a.recipe:
            raise ValueError('修改现有视频配置只填写 config_key、expected_revision、auto、render 和 inherit；新增关联填写 job 和 recipe，不填 config_key')
        action = 'PATCH /api/production/{key}/settings'
        values = {'key': a.config_key}
        body = {'expected_revision': a.expected_revision, 'render': a.render or {}, 'inherit': a.inherit}
        if a.auto is not None:
            body['auto'] = a.auto
    else:
        if not a.job or not a.recipe or a.inherit:
            raise ValueError('新增关联需要 job、recipe；可用配方见 query_matrices 的 production_options')
        paths = get_settings().paths
        matches = [c for c in production.load_all(paths.config_dir, paths.jobs_dir).values() if c.job == a.job and c.recipe == a.recipe]
        if matches:
            raise ValueError(f'此任务已关联该配方（{matches[0].key}），请读取矩阵后修改现有配置，不重复创建')
        action, values = 'POST /api/production', {}
        body = {k: getattr(a, k) for k in ('job', 'recipe', 'auto', 'render') if getattr(a, k) is not None}
        body['auto'] = a.auto if a.auto is not None else 0
    result = application.execute(SimpleNamespace(action_id=action, path=values, query={}, body=body, result_offset=0), request, user, write=True)
    data = result['result']
    matrix = matrices.of_job(data['job'], get_settings().paths.config_dir)
    if matrix:
        link = '/matrices/' + quote(matrix.key, safe='') + '/video/' + quote(data['job'], safe='')
        result.update(href=link, references=[{'type': 'matrix', 'id': matrix.key, 'href': '/matrices/' + matrix.key}])
    return result


def platform_path(platform: str) -> str:
    from core.social.registry import active_platforms
    if platform in ('bilibili', 'douyin'):
        return '/api/platforms/' + platform
    if platform in active_platforms():
        return '/api/platforms/social/' + platform
    raise ValueError('平台不存在或已暂缓使用，请先查询发布平台目录')


def save_publishing_draft(a, request, user):
    base = platform_path(a.platform)
    profile = account(a.platform)
    if not profile.get('connected') or profile.get('uid') != a.account_uid:
        raise ValueError('平台账号未连接或已变化，请核对账号后再修改发布草稿')
    preview = _native(request, 'GET', base + '/preview/' + quote(a.render_id, safe=''))
    draft = ((preview.get('draft') or {}).get('payload') or {})
    keys = ('title', 'description', 'tags', 'first_comment') + (('tid',) if a.platform == 'bilibili' else ())
    payload = {key: draft.get(key, preview.get(key)) for key in keys if key in draft or key in preview}
    payload.update(a.fields)
    result = application.execute(SimpleNamespace(action_id='PUT /api/video/distributions/{platform}/draft',
        path={'platform': a.platform}, query={}, body={'render_id': a.render_id, 'account_uid': a.account_uid, 'payload': payload},
        result_offset=0), request, user, write=True)
    ready = _native(request, 'GET', base + '/preview/' + quote(a.render_id, safe=''))
    render = store.get_render(a.render_id)
    result.update(message='发布草稿已保存，尚未投稿', preview=ready,
                  href=f"/video/{render['script_id']}?tab=publish&version={render['version']}&render={a.render_id}",
                  references=[{'type': 'platform', 'id': a.platform, 'href': platform_href(a.platform)}])
    return result


def platform_href(platform: str) -> str:
    return '/platforms?platform=' + quote(platform, safe='')


def publication_href(platform: str, key: str, row: dict) -> str:
    return '/platforms?' + urlencode({'tab': 'uploads', 'platform': platform, 'task': key,
                                      'from_script': row.get('script_id') or '', 'version': row.get('version') or ''})


def _native(request, method, path, **kwargs):
    return asyncio.run(application.native(request, method, path, **kwargs))[1]


def account(platform: str) -> dict:
    from web.services import platform_manager, douyin, social_publishing
    platform_path(platform)
    data = (platform_manager.account() if platform == 'bilibili' else douyin.account() if platform == 'douyin'
            else social_publishing.account(platform))
    return {k: data.get(k) for k in ('connected', 'uid', 'name')}


def publishing_query(a, request, user):
    if a.action == 'catalog':
        catalog = _native(request, 'GET', '/api/platforms/catalog')
        return {'platforms': [{**p, 'account': account(p['id']), 'href': platform_href(p['id'])} for p in catalog['platforms']]}
    base = platform_path(a.platform)
    refs = [{'type': 'platform', 'id': a.platform, 'href': platform_href(a.platform)}]
    if a.action == 'task':
        if not a.task_id:
            raise ValueError('查询任务需要 task_id')
        task = _native(request, 'GET', base + '/uploads/' + quote(a.task_id, safe=''))
        from web.services.publication_state import describe
        task = {**task, 'progress': describe(a.platform, a.task_id, task)}
        return {'task': task, 'href': publication_href(a.platform, a.task_id, task), 'references': refs,
                'publication_tasks': [{'platform': a.platform, 'id': a.task_id}]}
    if a.action == 'prepare':
        if not a.render_ids:
            raise ValueError('准备发布需要指定 render_ids，请先查询 candidates')
        profile = account(a.platform)
        preparations = []
        for rid in dict.fromkeys(a.render_ids):
            render = store.get_render(rid)
            if not render:
                raise ValueError('成片记录不存在：' + rid)
            data = _native(request, 'GET', base + '/preview/' + quote(rid, safe=''))
            draft = ((data.get('draft') or {}).get('payload') or {})
            fields = {k: draft.get(k, data.get(k)) for k in ('title', 'description', 'tags', 'tid', 'first_comment')
                      if k in data or k in draft}
            preparations.append({'platform': a.platform, 'render_id': rid, 'script_id': render['script_id'],
                                 'version': render['version'], 'account_uid': profile.get('uid') or '',
                                 'account_name': profile.get('name') or '', 'fields': fields,
                                 'warnings': data.get('warnings', []),
                                 'href': f"/video/{render['script_id']}?tab=publish&version={render['version']}&render={rid}"})
        return {'publication_preparations': preparations, 'references': refs, 'href': platform_href(a.platform),
                'message': '发布包已准备，尚未提交上传。'}
    if a.action == 'overview':
        data = _native(request, 'GET', base)
        # Native overviews are already public projections; return compact task rows.
        tasks = data.get('uploads') or data.get('jobs') or []
        if isinstance(tasks, dict):
            tasks = tasks.get('items', [])
        return {'platform': a.platform, 'account': account(a.platform), 'href': platform_href(a.platform),
                'references': refs, 'tasks': [{k: t.get(k) for k in ('id', 'kind', 'status', 'title', 'script_id', 'version', 'error')}
                                             for t in tasks[a.offset:a.offset + a.limit]],
                'next_offset': a.offset + a.limit if len(tasks) > a.offset + a.limit else None}
    from web.services import platform_manager, douyin, social_publishing
    rows = (platform_manager.candidates() if a.platform == 'bilibili' else douyin.candidates() if a.platform == 'douyin'
            else social_publishing.candidates(a.platform))
    if a.matrix:
        from core.video import outputs
        paths = get_settings().paths
        matrix_detail(a.matrix, user)  # Unknown matrix must not silently broaden the scope.
        scripts = [s for r in rows if (s := store.get_script(r['script_id']))]
        owners, _ = outputs.script_owners(paths.config_dir, paths.jobs_dir, scripts)
        rows = [r for r in rows if owners.get(r['script_id'], {}).get('matrix') == a.matrix]
    if a.current_only:
        rows = [r for r in rows if not r['archived'] and r['version'] == r['current_version'] == r['approved_version']]
    if a.text:
        rows = [r for r in rows if a.text.casefold() in r['title'].casefold()]
    return {'platform': a.platform, 'account': account(a.platform), 'items': rows[a.offset:a.offset + a.limit],
            'total': len(rows), 'next_offset': a.offset + a.limit if len(rows) > a.offset + a.limit else None,
            'references': refs, 'href': platform_href(a.platform),
            'note': '候选针对当前平台账号排除已投稿或已发布的版本；默认仅当前定稿，不会自动提交。'}


def publishing_change(a, request, user):
    from web.services.publication_state import resume_action
    base = platform_path(a.platform)
    profile = account(a.platform)
    if not profile.get('connected') or not a.account_uid or profile.get('uid') != a.account_uid:
        raise ValueError('平台账号未连接或已经变化，请打开平台页登录或重新核对账号：' + platform_href(a.platform))
    receipts, failures = [], []
    authentication_required = False
    tasks = []
    entries = a.items if a.action == 'submit' else [None]
    if a.action == 'submit' and not entries:
        raise ValueError('提交需要明确的 items（成片和发布字段）')
    if a.action != 'submit' and not a.task_id:
        raise ValueError('操作需要 task_id')
    for entry in entries:
        try:
            if entry:
                application.check_credentials(entry.fields)
                row = _native(request, 'POST', base + '/uploads', body={**entry.fields, 'render_id': entry.render_id, 'account_uid': a.account_uid})
                key = row['id']
            else:
                key = a.task_id
                old = _native(request, 'GET', base + '/uploads/' + quote(key, safe=''))
                if (old.get('uid') or old.get('account_uid')) and (old.get('uid') or old.get('account_uid')) != a.account_uid:
                    raise ValueError('投稿任务不属于已指定账号')
                operation = resume_action(a.platform, old) if a.action == 'resume' else a.action
                if a.action == 'resume' and not operation and old.get('status') not in ('PUBLISHED', 'QUEUED', 'RUNNING', 'UPLOADING', 'PUBLISHING', 'VERIFYING', 'PROCESSING'):
                    raise ValueError('这条原投稿需要先处理平台意见，当前不能重新提交。请打开投稿任务查看恢复说明。')
                body = ({'bvid': a.video} if a.platform == 'bilibili' else {'video': a.video}) if operation == 'reconcile' else None
                row = _native(request, 'POST', base + '/uploads/' + quote(key, safe='') + '/' + operation, body=body) if operation else old
            tasks.append({'platform': a.platform, 'id': key, 'account_uid': a.account_uid})
            receipts.append({'id': key, 'status': row.get('status'), 'render_id': entry.render_id if entry else old.get('render_id')})
        except (HTTPException, ValueError) as exc:
            if isinstance(exc, HTTPException) and exc.status_code == 401:
                # Preserve successes before stopping a batch with expired authentication.
                failures.append({'render_id': entry.render_id if entry else '', 'error': '会话认证失效，后续项目未提交'})
                authentication_required = True
                break
            failures.append({'render_id': entry.render_id if entry else '', 'error': str(exc.detail if isinstance(exc, HTTPException) else exc)})
    refs = [{'type': 'upload', 'id': a.platform + ':' + t['id'], 'href': platform_href(a.platform)} for t in tasks]
    already_published = bool(receipts) and all(row['status'] == 'PUBLISHED' for row in receipts)
    return {'ok': not failures, 'submitted': bool(tasks) and not already_published, 'status': 'completed' if already_published else 'started' if tasks else 'failed',
            'message': '原投稿已公开，已返回已有结果，没有再次投稿。' if already_published else f'已处理 {len(receipts)} 项，未完成 {len(entries) - len(receipts)} 项；投稿结果由工作区继续跟踪。',
            'receipts': receipts, 'failures': failures, 'publication_tasks': tasks, 'references': refs,
            'authentication_required': authentication_required,
            'href': platform_href(a.platform), 'account_uid': a.account_uid,
            'remaining_render_ids': [e.render_id for e in entries[len(receipts) + len(failures):] if e]}


async def publication_task_view(target: dict, request, *, row: dict | None = None) -> dict:
    from web.services.publication_state import describe
    platform, key = target['platform'], target['id']
    item = {**target, 'href': platform_href(platform), 'active': False, 'state': 'waiting'}
    try:
        if row is None:
            _, row = await application.native(request, 'GET', platform_path(platform) + '/uploads/' + quote(key, safe=''))
        status = row.get('status', '')
        owner = row.get('uid') or row.get('account_uid') or target.get('account_uid') or ''
        profile = account(platform)
        item.update(account_uid=owner, account_name=profile.get('name') if owner and profile.get('uid') == owner else owner)
        progress = describe(platform, key, row)
        item.update({k: row.get(k) for k in ('status', 'title', 'script_id', 'version', 'render_id', 'phase', 'error', 'url', 'bvid', 'work_id')})
        item.update(**progress)
        item['title'] = row.get('title') or (row.get('payload') or {}).get('title')
        item['href'] = publication_href(platform, key, row)
    except (HTTPException, ValueError) as exc:
        item.update(status='UNAVAILABLE', error=str(exc.detail if isinstance(exc, HTTPException) else exc))
    return item

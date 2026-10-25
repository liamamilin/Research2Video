"""Whole-application batches preserve scope and completed effects across replies."""
import asyncio
from pathlib import Path

import pytest

from core import matrices
from core.video import store as videos, autopilot_store
from tests.test_assistant_api import login, write_job, tool
from tests.test_assistant_workspace import chat
from tests.test_assistant_management import allow_whole_report
from tests.test_video_scripts import video_env, _approved_script  # noqa: F401
from web.services import assistant_batches as batches
from web.services.assistant_batch_store import BatchStore


def prepare(client, spec):
    response = client.post('/api/assistant/batches', headers=login(client), json=spec)
    assert response.status_code == 201, response.text
    return response.json()['batch']


def action(client, key, verb):
    response = client.post('/api/assistant/batches/' + key, headers=login(client), json={'action': verb})
    assert response.status_code == 200, response.text
    return response.json()['batch']


def tick(client):
    return asyncio.run(batches.tick(client.app))


def test_frozen_all_scope_processes_more_than_one_reply_without_replaying(client, web_env):
    root = web_env[0]
    for i in range(25):
        matrices.save(str(root / 'config'), 'm' + str(i), name='Matrix ' + str(i), domain='dev', keywords=[], members=[], render={'video_speed': 1.25, 'voice_id': 'default'})
    batch = prepare(client, {'kind': 'matrix_render', 'scope': {'all': True}, 'render': {'video_speed': 1.5}})
    assert len(batch['items']) == 25 and batch['status'] == 'prepared'
    matrices.save(str(root / 'config'), 'later', name='Later', domain='', keywords=[], members=[], render={'video_speed': 1.25})
    action(client, batch['id'], 'start')
    tick(client); tick(client)
    saved = BatchStore().get(batch['id'])
    assert saved['status'] == 'completed' and saved['counts'] == {'completed': 25}
    values = matrices.load_all(str(root / 'config'))
    assert values['later'].render['video_speed'] == 1.25
    assert all(m.render == {'video_speed': 1.5, 'voice_id': 'default'} for k, m in values.items() if k != 'later')
    mtimes = {p: p.stat().st_mtime_ns for p in (root / 'config/matrices').glob('*.yaml')}
    action(client, batch['id'], 'start'); tick(client)
    assert all(p.stat().st_mtime_ns == at for p, at in mtimes.items())


def test_scope_permissions_and_owner_are_not_http_headers(client, web_env):
    headers = login(client)
    invalid = client.post('/api/assistant/batches', headers=headers, json={'kind': 'matrix_render', 'scope': {}, 'render': {'video_speed': 1.5}})
    assert invalid.status_code == 422
    assert client.post('/api/assistant/batches', headers=headers, json={'kind': 'produce', 'scope': {'matrices': ['missing']}}).status_code == 422
    batch = prepare(client, {'kind': 'matrix_render', 'scope': {'all': True}, 'render': {'video_speed': 1.5}})
    login(client, 'viewer')
    assert client.get('/api/assistant/batches/' + batch['id']).status_code == 404
    assert client.get('/api/assistant/batches').json()['items'] == []
    assert client.post('/api/assistant/batches/' + batch['id'], headers={'x-assistant-user': '1'}, json={'action': 'start'}).status_code == 403
    client.cookies.clear()
    assert client.get('/api/assistant/batches', headers={'x-background-actor': '1'}).status_code == 401


def test_changed_target_fails_individually_unknown_dispatch_is_not_replayed(client, web_env):
    root = web_env[0]
    for key in ('one', 'two', 'three'):
        matrices.save(str(root / 'config'), key, name=key, domain='', keywords=[], members=[])
    batch = prepare(client, {'kind': 'matrix_render', 'scope': {'all': True}, 'render': {'video_speed': 1.5}})
    (root / 'config/matrices/one.yaml').write_text((root / 'config/matrices/one.yaml').read_text() + '\n# concurrent edit\n')
    storage = BatchStore(); internal = storage.get(batch['id'])
    third = next(i for i in internal['items'] if i['target'] == 'three')
    storage.item(third['id'], state='dispatching', stage='保存矩阵')
    action(client, batch['id'], 'start'); tick(client)
    saved = storage.get(batch['id'])
    states = {i['target']: i['state'] for i in saved['items']}
    assert states == {'one': 'failed', 'two': 'completed', 'three': 'unknown'}
    assert saved['status'] == 'waiting'
    action(client, batch['id'], 'retry'); tick(client)
    assert matrices.load_all(str(root / 'config'))['three'].render == {}
    assert next(i for i in storage.get(batch['id'])['items'] if i['target'] == 'two')['state'] == 'completed'


def test_actor_role_is_rechecked_after_start_and_new_browser_session_not_required(client, web_env):
    from web.auth import db
    matrices.save(str(web_env[0] / 'config'), 'one', name='one', domain='', keywords=[], members=[])
    batch = prepare(client, {'kind': 'matrix_render', 'scope': {'all': True}, 'render': {'video_speed': 1.5}})
    action(client, batch['id'], 'start')
    db.set_role(1, 'viewer')
    tick(client)
    assert BatchStore().get(batch['id'])['status'] == 'waiting'
    assert matrices.load_all(str(web_env[0] / 'config'))['one'].render == {}


def test_schedule_batch_uses_existing_toggle_and_leaves_running_work_alone(client, web_env, monkeypatch):
    from web.services import managed_scheduler as managed
    write_job(web_env)
    plan = managed.store().save('research/memory', '0 2 * * *', timezone='Asia/Shanghai')
    batch = prepare(client, {'kind': 'schedules', 'scope': {'ids': [plan['id']]}, 'enabled': False})
    action(client, batch['id'], 'start'); tick(client)
    assert not managed.store().get(plan['id'])['enabled']
    assert BatchStore().get(batch['id'])['status'] == 'completed'
    # Background batch execution does not depend on the original browser cookie.
    client.cookies.clear()
    assert tick(client) == 0


def test_production_dependency_uses_exact_report_and_shared_research_then_native_workflow(client, video_env, web_env, monkeypatch):
    from web.services import assistant_runs, video_workflow
    write_job(web_env); allow_whole_report(video_env)
    root = Path(video_env)
    matrices.save(str(root / 'config'), 'check', name='Check', domain='', keywords=[], members=[matrices.Member('research/memory')])
    configs = root / 'config/production'; configs.mkdir(exist_ok=True)
    (configs / 'one.yaml').write_text('job: research/memory\nrecipe: deep_dive\n')
    report_path = root / 'output/memory_2026-10-25.md'; report_path.write_text('# Local material\nPublic source https://example.org\n')
    monkeypatch.setattr(video_workflow, 'workspace_dir', lambda: str(root))
    phase = {'success': False}; calls = []
    original = batches.native
    async def native(app, uid, method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path.endswith('/run'):
            return {'task_id': 'exact-research-run'}
        if path.endswith('/pending'):
            return {'reports': [{'path': 'output/memory_2026-10-25.md', 'written': False}]}
        if path.endswith('/write'):
            script = _approved_script()
            return [script]
        return await original(app, uid, method, path, **kwargs)
    async def run_view(target, request, user):
        assert target['run_id'] == 'exact-research-run'
        return {'active': not phase['success'], 'status': 'success' if phase['success'] else 'running', 'label': 'research',
                'report': {'path': 'memory_2026-10-25.md', 'href': '/reports/view?path=memory_2026-10-25.md'}}
    monkeypatch.setattr(batches, 'native', native); monkeypatch.setattr(assistant_runs, 'run_view', run_view)
    batch = prepare(client, {'kind': 'produce', 'scope': {'matrices': ['check']}})
    assert len(batch['items']) == 2
    action(client, batch['id'], 'start'); tick(client); tick(client)
    assert not any(path.endswith('/write') for _, path, _ in calls)
    phase['success'] = True
    tick(client); tick(client); tick(client)
    internal = BatchStore().get(batch['id'])
    item = next(i for i in internal['items'] if i['target'] == 'one')
    assert item['result']['workflow_ids'] and item['result']['scripts']
    for ref in item['result']['workflow_ids']:
        run = autopilot_store.get_run(ref['id'])
        for entry in run['items']:
            autopilot_store.update_item(entry['id'], state='DONE', render_id='render-exact')
        autopilot_store.update_run(ref['id'], status='DONE')
    tick(client)
    completed = BatchStore().get(batch['id'])
    assert completed['status'] == 'completed'
    assert next(i for i in completed['items'] if i['target'] == 'one')['stage'] == '待发布'
    assert sum(path.endswith('/run') for _, path, _ in calls) == 1
    assert sum(path.endswith('/write') for _, path, _ in calls) == 1
    assert next(kwargs['body']['report_path'] for _, path, kwargs in calls if path.endswith('/write')) == 'output/memory_2026-10-25.md'


def test_capabilities_and_assistant_batch_handoff(client, web_env, monkeypatch):
    from core.llm import ChatResponse
    from tests.test_assistant_api import fake_llm
    matrices.save(str(web_env[0] / 'config'), 'one', name='one', domain='', keywords=[], members=[])
    batch = prepare(client, {'kind': 'matrix_render', 'scope': {'all': True}, 'render': {'video_speed': 1.5}})
    fake_llm(monkeypatch, [tool('manage_application_batch', id=batch['id'], action='start'),
        tool('query_application_batch', id=batch['id']), tool('query_application_batch', id=batch['id'])])
    result = client.post('/api/assistant', headers=login(client), json={'message': '执行此批次并跟进'}).json()
    assert len(result['actions']) == 2 and '任务中心' in result['answer']
    workspace = client.get('/api/assistant/workspace', params={'conversation_id': result['conversation_id']}).json()
    assert workspace['active'] and workspace['tasks'][0]['batches'][0]['id'] == batch['id']
    tick(client)
    workspace = client.get('/api/assistant/workspace', params={'conversation_id': result['conversation_id']}).json()
    assert not workspace['active']
    assert workspace['tasks'][0]['status'] == 'completed'
    areas = client.get('/api/assistant/capabilities').json()
    assert {'sources', 'batches', 'platforms', 'administration'} <= {a['key'] for a in areas['areas']}


def test_publish_collects_every_page_freezes_versions_and_keeps_partial_results(client, video_env, monkeypatch):
    from tests.test_assistant_management import publishing
    from web.services import platform_manager
    publishing(monkeypatch)
    rows, scripts = [], []
    for i in range(53):
        script = _approved_script()
        scripts.append(script)
        rows.append({'render_id': 'render-' + str(i), 'script_id': script['id'], 'title': 'Video ' + str(i),
                     'version': 1, 'current_version': 1, 'approved_version': 1, 'archived': False})
    monkeypatch.setattr(platform_manager, 'candidates', lambda: list(rows))
    monkeypatch.setattr(platform_manager, 'preview', lambda key: {'title': key, 'description': 'Public material', 'tags': ['test'], 'tid': 201})
    queued, statuses = [], {}
    def enqueue(key, fields):
        if key == 'render-1':
            from fastapi import HTTPException
            raise HTTPException(422, 'test known failure')
        queued.append(key); statuses[key] = 'QUEUED'
        return {'id': key, 'status': 'QUEUED', 'script_id': scripts[0]['id'], 'version': 1}
    monkeypatch.setattr(platform_manager, 'enqueue', enqueue)
    monkeypatch.setattr(platform_manager, 'upload_task', lambda key: {'id': key, 'status': statuses[key], 'script_id': scripts[0]['id'], 'version': 1})
    batch = prepare(client, {'kind': 'publish', 'scope': {'all': True}, 'platform': 'bilibili', 'account_uid': 'owner'})
    assert len(batch['items']) == 53
    rows.clear()  # Later discovery cannot replace the frozen set.
    videos.update_script(scripts[2]['id'], current_version=2)
    action(client, batch['id'], 'start')
    for _ in range(5):
        tick(client)
    assert 'render-1' not in queued and 'render-2' not in queued and len(queued) == 51
    for key in queued:
        statuses[key] = 'PUBLISHED'
    for _ in range(5):
        tick(client)
    value = BatchStore().get(batch['id'])
    assert value['counts'] == {'completed': 51, 'failed': 2} and value['status'] == 'failed'
    count = len(queued); tick(client)
    assert len(queued) == count


def test_cancel_stops_new_dispatch_but_observes_the_original_upload(client, video_env, monkeypatch):
    from tests.test_assistant_management import publishing
    platform = publishing(monkeypatch)
    script = _approved_script()
    rows = [{'render_id': 'one', 'script_id': script['id'], 'title': 'One', 'version': 1, 'current_version': 1, 'approved_version': 1, 'archived': False}]
    monkeypatch.setattr(platform, 'candidates', lambda: rows)
    monkeypatch.setattr(platform, 'preview', lambda key: {'title': 'One', 'description': '', 'tags': ['test'], 'tid': 201})
    count = []; state = {'status': 'QUEUED'}
    monkeypatch.setattr(platform, 'enqueue', lambda key, fields: count.append(key) or {'id': 'upload', **state})
    monkeypatch.setattr(platform, 'upload_task', lambda key: {'id': key, **state})
    batch = prepare(client, {'kind': 'publish', 'scope': {'all': True}, 'platform': 'bilibili', 'account_uid': 'owner'})
    action(client, batch['id'], 'start'); tick(client)
    action(client, batch['id'], 'cancel')
    state['status'] = 'PUBLISHED'; tick(client)
    saved = BatchStore().get(batch['id'])
    assert saved['status'] == 'cancelled' and saved['counts'] == {'completed': 1} and count == ['one'] and not saved['active']


def test_shared_native_workflow_only_observes_and_retries_owned_scripts(client, video_env):
    login(client)
    owned, other = _approved_script(), _approved_script()
    run = autopilot_store.create_run({}, [{'column_key': 'deep_dive', 'topic': {}}, {'column_key': 'deep_dive', 'topic': {}}])
    first, second = run['items']
    autopilot_store.update_item(first['id'], script_id=owned['id'], state='HELD', note='retry owned')
    autopilot_store.update_item(second['id'], script_id=other['id'], state='HELD', note='leave alone')
    storage = BatchStore()
    batch = storage.create({'id': 1}, 'owned only', {'kind': 'produce', 'scope': {}}, [{
        'target': 'owned', 'title': 'Owned', 'payload': {'type': 'production', 'job': 'research/owned', 'dependency': ''}}])
    storage.item(batch['items'][0]['id'], state='failed', result={'scripts': [owned['id']], 'script_versions': {owned['id']: 1},
        'workflow_ids': [{'id': run['id'], 'script_id': owned['id']}]})
    storage.set_status(batch['id'], 'failed')
    action(client, batch['id'], 'retry')
    value = autopilot_store.get_run(run['id'])
    assert value['items'][0]['state'] == 'WORKING' and value['items'][1]['state'] == 'HELD'
    autopilot_store.update_item(first['id'], state='DONE', render_id='owned-render')
    tick(client)
    done = storage.get(batch['id'])
    assert done['status'] == 'completed'  # The other item is still held in the same native workflow.
    assert done['items'][0]['result']['renders'] == [{'id': 'owned-render', 'script_id': owned['id'], 'version': 1}]


def test_worker_lists_every_active_batch_while_history_stays_bounded(client):
    storage = BatchStore(); user = {'id': 1}
    for _ in range(102):
        batch = storage.create(user, 'empty pending batch', {'kind': 'matrix_render', 'scope': {}}, [])
        storage.set_status(batch['id'], 'running')
    assert len(storage.list(active=True)) == 102
    assert len(storage.list(user)) == 100


@pytest.mark.parametrize('case,expected', [('fresh', 'active'), ('old', 'active'), ('changed', 'failed')])
def test_existing_automatic_script_continues_only_for_the_exact_fresh_report(client, video_env, web_env, monkeypatch, case, expected):
    from datetime import datetime
    from web.services import video_workflow
    write_job(web_env); allow_whole_report(video_env)
    root = Path(video_env)
    matrices.save(str(root / 'config'), 'check', name='Check', domain='', keywords=[], members=[matrices.Member('research/memory')])
    configs = root / 'config/production'; configs.mkdir(exist_ok=True)
    (configs / 'one.yaml').write_text('job: research/memory\nrecipe: deep_dive\nauto: 1\n')
    report = root / 'output/memory_2026-10-25.md'; report.write_text('# Public material\nhttps://example.org\n')
    monkeypatch.setattr(video_workflow, 'workspace_dir', lambda: str(root))
    script = _approved_script(); videos.update_script(script['id'], production='one')
    created = datetime.strptime(script['created_at'], '%Y-%m-%dT%H:%M:%S%z').timestamp()
    batch = prepare(client, {'kind': 'produce', 'scope': {'ids': ['one']}})
    storage = BatchStore()
    dependency = next(i for i in batch['items'] if i['target'].startswith('research:'))
    storage.item(dependency['id'], state='completed', result={'report': {'path': report.name},
        'workspace_report': 'output/' + report.name, 'report_sha256': batches.digest(report),
        'report_at': created + (60 if case == 'old' else -1)})
    if case == 'changed':
        report.write_text('# A later run overwrote the same daily filename\n')
    calls = []
    async def native(app, uid, method, path, **kwargs):
        calls.append((method, path))
        if method == 'POST':
            assert case == 'old' and path == '/api/production/one/write'
            assert kwargs['body']['rewrite_existing'] is True and kwargs['body']['report_sha256'] == batches.digest(report)
            return [{**script, 'current_version': 0, 'target_version': 2}]
        assert method == 'GET' and path.endswith('/pending')
        return {'reports': [{'path': 'output/' + report.name, 'written': True, 'scripts': [{'id': script['id']}]}]}
    monkeypatch.setattr(batches, 'native', native)
    action(client, batch['id'], 'start'); tick(client)
    item = next(i for i in storage.get(batch['id'])['items'] if i['target'] == 'one')
    assert item['state'] == expected
    assert calls == [('GET', '/api/production/one/pending')] + ([('POST', '/api/production/one/write')] if case == 'old' else [])
    if case == 'fresh':
        assert item['result']['reused'] and item['result']['scripts'] == [script['id']]
        assert item['result']['script_versions'] == {script['id']: 1}
    if case == 'old':
        assert item['result']['script_versions'] == {script['id']: 2}

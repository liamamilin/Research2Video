"""Existing-matrix operation preserves past work and uses current health facts."""
import hashlib
from pathlib import Path

import pytest

from core import matrices
from core.video import reports, store
from tests.test_assistant_api import login, write_job, tool
from tests.test_assistant_management import allow_whole_report
from tests.test_assistant_workspace import chat
from tests.test_video_scripts import video_env, _approved_script  # noqa: F401
from tests.test_platforms import platform_env  # noqa: F401


def test_same_daily_report_can_rewrite_its_own_script_with_an_exact_revision(client, video_env, web_env, monkeypatch):
    from web.routes import video
    from web.services import video_workflow
    write_job(web_env); allow_whole_report(video_env)
    root = Path(video_env)
    matrices.save(str(root / 'config'), 'check', name='Check', domain='', keywords=[], members=[matrices.Member('research/memory')])
    directory = root / 'config/production'; directory.mkdir(exist_ok=True)
    (directory / 'own.yaml').write_text('job: research/memory\nrecipe: deep_dive\n')
    report = root / 'output/memory_2026-10-25.md'; report.write_text('# A newly researched report\nPublic material\n')
    monkeypatch.setattr(video_workflow, 'workspace_dir', lambda: str(root))
    script = _approved_script()
    store.update_script(script['id'], production='own', column_key='deep_dive', report_path='output/' + report.name)
    reports.attach(script['id'], 'output/' + report.name, job='research/memory')
    original = store.get_version(script['id'], 1)
    calls = []
    def rerun(sid, step, user):
        calls.append((sid, step))
        store.update_script(sid, status='WRITING', current_version=0)
        return store.get_script(sid)
    monkeypatch.setattr(video, 'rerun_step', rerun)
    headers = login(client); base = {'report_path': 'output/' + report.name}
    assert client.post('/api/production/own/write', headers=headers, json=base).status_code == 409
    assert client.post('/api/production/own/write', headers=headers, json={**base, 'rewrite_existing': True}).status_code == 422
    assert client.post('/api/production/own/write', headers=headers, json={**base, 'rewrite_existing': True, 'report_sha256': '0'*64}).status_code == 409
    response = client.post('/api/production/own/write', headers=headers, json={**base, 'rewrite_existing': True,
        'report_sha256': hashlib.sha256(report.read_bytes()).hexdigest()})
    assert response.status_code == 202, response.text
    assert response.json()[0]['id'] == script['id'] and response.json()[0]['target_version'] == 2
    assert calls == [(script['id'], 'write')] and store.get_version(script['id'], 1) == original
    store.update_script(script['id'], production='other')
    response = client.post('/api/production/own/write', headers=headers, json={**base, 'rewrite_existing': True,
        'report_sha256': hashlib.sha256(report.read_bytes()).hexdigest()})
    assert response.status_code == 409 and calls == [(script['id'], 'write')]
    store.update_script(script['id'], archived=True)
    pending = client.get('/api/production/own/pending').json()['reports'][0]
    assert not pending['written'] and pending['scripts'] == []
    made = _approved_script('新的研究写成的新文稿')
    monkeypatch.setattr(video, 'create_script', lambda *args, **kwargs: made)
    response = client.post('/api/production/own/write', headers=headers, json=base)
    assert response.status_code == 202 and response.json()[0]['id'] == made['id']
    assert store.get_script(script['id'])['archived'] and store.get_version(script['id'], 1) == original


@pytest.mark.parametrize('deleted,installed,loaded,schedule_status,expected', [
    (True, False, False, 'ok', 'ok'), (True, True, False, 'ok', 'error'),
    (True, False, True, 'ok', 'error'), (False, False, False, 'ok', 'error'),
    (True, False, False, 'error', 'error')])
def test_only_explicitly_retired_agents_stop_reporting_stale_heartbeat(monkeypatch, deleted, installed, loaded, schedule_status, expected):
    from web.services import health_status, launchd_agents
    monkeypatch.setattr(launchd_agents, 'list_deleted', lambda _: [{'label': label} for label in launchd_agents.LABELS] if deleted else [])
    monkeypatch.setattr(launchd_agents, 'read_config', lambda _: {'Label': 'restored'} if installed else {})
    monkeypatch.setattr(launchd_agents, 'is_loaded', lambda _: loaded)
    result = health_status.complete_health({'checks': [{'name': 'scheduler', 'status': 'error', 'detail': 'old heartbeat'}]},
        {'name': 'schedule', 'status': schedule_status, 'detail': 'managed schedules'}, '/isolated')
    assert result['status'] == expected
    if deleted and not installed and not loaded:
        assert result['checks'][0]['active'] is False and result['checks'][0]['historical_detail'] == 'old heartbeat'
    if schedule_status == 'error':
        assert 'schedule' in result['errors']


def test_assistant_health_uses_the_authenticated_native_detailed_report(client, monkeypatch):
    from web.services import assistant_management
    calls = []
    monkeypatch.setattr(assistant_management, '_native', lambda request, method, path: calls.append((method, path)) or {'checks': [], 'schedule': {'status': 'ok'}})
    response = chat(client, monkeypatch, [tool('diagnose_application', action='health')])
    assert response['actions'][0]['ok'] and calls == [('GET', '/api/health/detailed')]


def test_batch_reuses_new_versions_of_an_older_script_and_pins_in_progress_rewrites(video_env):
    from web.services.assistant_batches import script_report_time, script_target_version
    from datetime import datetime
    script = _approved_script()
    store.update_script(script['id'], created_at='2000-01-01T00:00:00+0000')
    version = store.get_version(script['id'], 1)
    assert script_report_time(store.get_script(script['id'])) == datetime.strptime(version['created_at'], '%Y-%m-%dT%H:%M:%S%z').timestamp()
    store.update_script(script['id'], status='WRITING', current_version=0)
    assert script_target_version(store.get_script(script['id'])) == 2


def test_native_workflow_links_do_not_point_to_an_application_batch():
    from web.services.assistant_delivery import canonical_links
    refs = [{'type': 'batch', 'id': 'native', 'href': '/video/owned?tab=video'},
            {'type': 'application_batch', 'id': 'app', 'href': '/ask?tasks=1&batch=app'}]
    answer = canonical_links('[视频](/ask?tasks=1&batch=native) `/ask?tasks=1&batch=app` /ask?tasks=1&batch=unknown', refs)
    assert '[视频](/video/owned?tab=video)' in answer and '/ask?tasks=1&batch=app' in answer and 'batch=unknown' in answer


def test_save_publishing_draft_really_saves_fields_without_uploading(client, platform_env, monkeypatch):
    from tests.test_platforms import render, PAYLOAD
    from web.services import platform_manager
    row = render()
    def preview(rid):
        current = store.distribution_draft(row, 'bilibili', '123')
        return {**PAYLOAD, 'draft': current, 'account_uid': '123'}
    monkeypatch.setattr(platform_manager, 'preview', preview)
    monkeypatch.setattr(platform_manager, 'enqueue', lambda *args: pytest.fail('saving must not upload'))
    response = chat(client, monkeypatch, [tool('save_publishing_draft', platform='bilibili', account_uid='123',
        render_id=row['id'], fields={'title': '准备好的平台标题'})])
    assert response['actions'][0]['ok'], response
    saved = store.distribution_draft(row, 'bilibili', '123')['payload']
    assert saved == {**PAYLOAD, 'title': '准备好的平台标题'}
    assert store.get_version(row['script_id'], 1)['title'] == '测试视频'
    denied = chat(client, monkeypatch, [tool('save_publishing_draft', platform='bilibili', account_uid='changed',
        render_id=row['id'], fields={'title': '不能覆盖'})])
    assert not denied['actions'][0]['ok'] and store.distribution_draft(row, 'bilibili', '123')['payload'] == saved


def test_matrix_operating_snapshot_is_scoped_and_keeps_partial_platform_errors(client, video_env, web_env, monkeypatch):
    from web.services import assistant_management, source_catalog
    write_job(web_env)
    matrices.save(str(Path(video_env) / 'config'), 'check', name='Check', domain='', keywords=[], members=[matrices.Member('research/memory')])
    calls = []
    original = source_catalog.catalog
    def catalog(*args, **kwargs):
        assert kwargs['job_names'] == ['research/memory']
        return original(*args, **kwargs)
    monkeypatch.setattr(source_catalog, 'catalog', catalog)
    def publishing(a, request, user):
        calls.append((a.action, a.platform))
        if a.action == 'catalog':
            return {'platforms': [{'id': 'bilibili', 'name': 'B站', 'href': '/platforms', 'account': {'connected': True}},
                                 {'id': 'douyin', 'name': '抖音', 'href': '/platforms', 'account': {'connected': False}}]}
        if a.action == 'overview':
            return {'tasks': [{'id': 'foreign', 'status': 'FAILED'}]}
        assert a.matrix == 'check'
        return {'items': [], 'total': 0, 'next_offset': None}
    monkeypatch.setattr(assistant_management, 'publishing_query', publishing)
    response = chat(client, monkeypatch, [tool('inspect_matrix_operations', key='check')])
    assert response['actions'][0]['ok'], response
    assert calls == [('catalog', 'bilibili'), ('overview', 'bilibili'), ('candidates', 'bilibili')]
    bad = chat(client, monkeypatch, [tool('inspect_matrix_operations', key='missing')])
    assert not bad['actions'][0]['ok']

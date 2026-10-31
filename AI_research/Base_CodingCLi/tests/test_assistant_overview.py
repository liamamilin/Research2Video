"""Global operations use complete local scopes, exact versions and current accounts."""
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import matrices
from core.state import StateManager
from core.video import store
from tests.test_assistant_api import login, tool, write_job
from tests.test_assistant_workspace import chat
from tests.test_video_scripts import video_env, _approved_script  # noqa: F401
from web.services import assistant_overview as service


@pytest.fixture
def overview_env(video_env, web_env, monkeypatch):
    for key, job in [('ai', 'research/ai'), ('history', 'research/history'), ('old', 'research/old')]:
        write_job(web_env, job, key)
        matrices.save(str(Path(video_env) / 'config'), key, name=key, domain='', keywords=[], members=[matrices.Member(job)])
    matrices.save(str(Path(video_env) / 'config'), 'old', name='old', domain='', keywords=[], members=[matrices.Member('research/old')], archived=True)
    owners, available, uploads, account_calls = {}, [], [], []
    monkeypatch.setattr(service.outputs, 'script_owners', lambda *args: (owners, []))
    monkeypatch.setattr(service.TaskRegistry, 'all_running', lambda _: {})
    def account(platform):
        account_calls.append(platform)
        return {'connected': platform == 'xiaohongshu', 'uid': 'current-account', 'name': '当前账号'}
    monkeypatch.setattr(service, '_platform_account', account)
    monkeypatch.setattr(service, '_candidates', lambda platform: available if platform == 'xiaohongshu' else [])
    def outbox(platform, uid):
        assert uid == 'current-account'
        return uploads
    monkeypatch.setattr(service, '_uploads', outbox)
    return SimpleNamespace(owners=owners, candidates=available, uploads=uploads, accounts=account_calls)


def video(env, *, matrix='ai', status='DRAFT', version=1, render_status=None):
    script = _approved_script()
    for _ in range(1, version):
        store.add_version(script['id'], title='新版本', sections=[], length_check={}, edit_type='GENERATED')
    store.update_script(script['id'], status=status, approved_version=version if status == 'APPROVED' else 1)
    env.owners[script['id']] = {'matrix': matrix, 'matrix_archived': matrix == 'old'}
    render = None
    if render_status:
        render = store.create_render(script['id'], version, title='成片', source_text='', source_sha256='a'*64, request={})
        store.update_render(render['id'], status=render_status)
    return store.get_script(script['id']), render


def snapshot(**kwargs):
    return service.overview({'role': 'admin'}, **kwargs)


def test_complete_counts_pagination_matrix_and_archived_scope(overview_env):
    for _ in range(18):
        video(overview_env)
    video(overview_env, matrix='history')
    video(overview_env, matrix='old')
    script, _ = video(overview_env)
    store.update_script(script['id'], archived=True)
    data = snapshot(limit=5)
    assert data['summary']['attention'] == data['items']['total'] == 19
    assert data['summary']['matrix_count'] == 2
    assert len(data['items']['rows']) == 5 and data['items']['next_offset'] == 5
    assert {m['key']: m['attention'] for m in data['matrices'] if m['key'] != '_ungrouped'} == {'ai': 18, 'history': 1}
    selected = snapshot(matrix='history')
    assert selected['summary']['attention'] == 1 and selected['summary']['jobs'] == 1
    assert all(r['matrix'] == 'history' for r in selected['items']['rows'])
    assert snapshot(offset=19)['items']['rows'] == []


def test_publish_uses_current_approved_version_and_selected_account(overview_env):
    current, render = video(overview_env, status='APPROVED', version=2, render_status='COMPLETED')
    old, _ = video(overview_env, status='APPROVED', version=2)
    draft, _ = video(overview_env, status='DRAFT')
    archived, _ = video(overview_env, status='APPROVED', matrix='old')
    overview_env.candidates.extend({'script_id': s['id'], 'version': version, 'render_id': 'render-' + s['id'], 'title': '可用成片'}
                                   for s, version in [(current, 2), (old, 1), (draft, 1), (archived, 1)])
    data = snapshot(view='publish')
    assert data['platform'] == 'xiaohongshu' and data['summary']['publish'] == 1
    row = data['items']['rows'][0]
    assert row['version'] == 2 and row['script_id'] == current['id']
    assert 'current-account' in row['prompt'] and 'tab=publish&version=2&render=' in row['href']
    assert snapshot(platform='bilibili')['summary']['publish'] is None
    assert set(overview_env.accounts) == set(service.PLATFORM_NAMES)


def test_old_videos_do_not_hide_current_drafts_or_failed_render_retries(overview_env):
    script, _ = video(overview_env, status='APPROVED', render_status='COMPLETED')
    store.add_version(script['id'], title='当前新稿', sections=[], length_check={}, edit_type='GENERATED')
    store.update_script(script['id'], status='DRAFT')
    failed, _ = video(overview_env, status='APPROVED', render_status='FAILED')
    running, _ = video(overview_env, status='APPROVED', render_status='QUEUED')
    reviewing, _ = video(overview_env)
    store.update_script(reviewing['id'], stage='改写后重新审计')
    data = snapshot()
    rows = {r['script_id']: r for r in data['items']['rows']}
    assert rows[script['id']]['version'] == 2 and '旧版成片' in rows[script['id']]['detail']
    assert rows[failed['id']]['label'] == '出片失败'
    assert snapshot(view='working')['summary']['working'] == 2
    assert running['id'] not in rows and reviewing['id'] not in rows


def test_publication_state_reuses_verification_without_auto_retry(overview_env):
    script, _ = video(overview_env, status='APPROVED', render_status='COMPLETED')
    for key, state in [('published', 'PUBLISHED'), ('reviewing', 'SUBMITTED'), ('unknown', 'UNKNOWN')]:
        overview_env.uploads.append({'id': key, 'script_id': script['id'], 'version': 1, 'status': state,
                                    'verification': {'automatic': key == 'reviewing', 'state': 'reviewing' if key == 'reviewing' else ''}})
    data = snapshot()
    assert [r['id'] for r in data['items']['rows']] == ['upload:unknown']
    assert 'task=unknown' in data['items']['rows'][0]['href']
    assert snapshot(view='working')['summary']['working'] == 1


def test_research_daily_latest_results_and_unassigned_bucket(overview_env, web_env):
    StateManager.update('research/ai', 'failed', error='quota')
    StateManager.update('research/history', 'success')
    StateManager.update('research/old', 'failed')
    write_job(web_env, 'research/standalone', '独立任务')
    script, _ = video(overview_env, matrix='')
    data = snapshot()
    assert data['summary']['today_success'] == 1 and data['summary']['today_failed'] == 1
    assert data['summary']['jobs'] >= 3  # The video fixture also installs an independent topic-source job.
    assert data['summary']['attention'] == 2
    assert any(m['key'] == '_ungrouped' for m in data['matrices'])
    assert snapshot(matrix='_ungrouped')['items']['rows'][0]['script_id'] == script['id']


def test_admin_schedules_include_blocked_and_active_even_if_latest_is_different(overview_env, monkeypatch):
    db = service.managed_scheduler.store()
    plan = db.save('research/ai', '0 2 * * *', timezone='Asia/Shanghai')
    latest = {plan['id']: {'schedule_id': plan['id'], 'job_name': 'research/ai', 'status': 'blocked',
                          'finished_at': datetime.now().timestamp(), 'error': 'blocked-slot'}}
    monkeypatch.setattr(type(db), 'latest_runs', lambda *_: latest)
    data = snapshot(matrix='history')
    assert data['summary']['attention'] == 0 and data['schedules']['enabled'] == 0
    assert next(m for m in data['matrices'] if m['key'] == 'ai')['attention'] == 1
    data = snapshot()
    assert data['summary']['today_failed'] == 1 and data['schedules']['enabled'] == 1
    assert data['schedules']['next']['job'] == 'research/ai'


def test_partial_failures_remain_visible_and_do_not_become_zero(overview_env, monkeypatch):
    video(overview_env)
    monkeypatch.setattr(service, '_candidates', lambda _: (_ for _ in ()).throw(RuntimeError('unavailable')))
    data = snapshot()
    assert data['summary']['publish'] is None and data['summary']['attention'] == 1
    assert data['warnings'] and data['availability']['candidates'] is False
    monkeypatch.setattr(service.store, 'current_titles', lambda: (_ for _ in ()).throw(RuntimeError('database busy')))
    data = snapshot()
    assert data['summary']['attention'] is None and data['summary']['working'] is None
    assert not data['items']['complete'] and len(data['warnings']) == 2


def test_viewer_cannot_read_admin_schedule_details_and_bad_scope_never_broadens(client, overview_env, monkeypatch):
    assert client.get('/api/assistant/overview').status_code == 401
    login(client, 'viewer')
    monkeypatch.setattr(service.managed_scheduler, 'store', lambda: pytest.fail('viewer must not read schedules'))
    data = client.get('/api/assistant/overview').json()
    assert not data['schedules']['available'] and data['schedules']['next'] is None
    for query in ('matrix=missing', 'matrix=old', 'platform=missing', 'platform=weixin_channels', 'view=oops', 'offset=-1', 'limit=51'):
        assert client.get('/api/assistant/overview?' + query).status_code in (404, 422)


def test_real_assistant_tool_uses_same_projection_without_mutating_business_data(client, overview_env, monkeypatch):
    script, _ = video(overview_env)
    before = store.get_script(script['id'])
    response = chat(client, monkeypatch, [tool('query_operations_overview', matrix='ai')])
    assert response['actions'][0]['ok'], response
    assert store.get_script(script['id']) == before
    from web.services.assistant_runtime import references
    refs = references(snapshot(matrix='ai'), 'query_operations_overview')
    assert len(refs) == 1 and refs[0]['type'] == 'overview' and 'overview=1' in refs[0]['href']
    assert '/video/' not in refs[0]['href']  # Inventory must not pin unrelated videos in the workspace.

"""Publication recovery follows the original task and never recreates accepted uploads."""
import asyncio
import time

import pytest

from tests.test_assistant_api import login, tool
from tests.test_assistant_workspace import chat
from tests.test_assistant_management import result_for
from tests.test_social_publishing import OWNER, enqueue, social_env  # noqa: F401
from tests.test_platforms import render
from web.services import social_publishing as social, assistant_batches as batches
from web.services.assistant_batch_store import BatchStore
from web.services.publication_state import describe, resume_action


@pytest.mark.parametrize('status,verification,can_retry,label,active,recovery', [
    ('SUBMITTED', {'automatic': True, 'state': 'reviewing'}, False, '平台审核中', True, None),
    ('SUBMITTED', {'automatic': True, 'state': 'scheduled'}, False, '等待定时发布', True, None),
    ('SUBMITTED', {'automatic': True, 'state': 'unavailable'}, False, '暂时无法回查，请核对平台登录', False, 'connection'),
    ('SUBMITTED', {'automatic': True, 'state': 'private'}, False, '非公开可见', False, 'platform'),
    ('FAILED', {}, True, '投稿未完成', False, 'retry'),
    ('FAILED', {}, False, '投稿未完成', False, 'check'),
    ('PUBLISHED', {}, False, '已公开', False, None),
])
def test_progress_explains_waiting_and_actionable_failures(status, verification, can_retry, label, active, recovery):
    value = describe('xiaohongshu', 'original', {'status': status, 'verification': verification,
                                              'can_retry': can_retry, 'script_id': 'script', 'version': 2})
    assert value['label'] == label and value['active'] is active
    assert (value['recovery'] or {}).get('kind') == recovery
    if value['recovery']:
        assert 'task=original' in value['recovery']['href'] and 'version=2' in value['recovery']['href']
    assert resume_action('xiaohongshu', {'status': 'FAILED', 'can_retry': can_retry}) == ('retry' if can_retry else None)


@pytest.mark.parametrize('accepted', [True, False])
def test_assistant_resume_reuses_original_task_and_saved_fields(client, social_env, monkeypatch, accepted):
    job = enqueue('xiaohongshu', render())
    db = social.store('xiaohongshu')
    if accepted:
        db.update(job['id'], status='SUBMITTED', work_id='6ade22190000000033018ae8',
                  result={'accepted': True, 'submission_started': True})
    else:
        db.update(job['id'], status='FAILED', result={'confirmed_not_submitted': True})
    before = db.get(job['id'])
    response = chat(client, monkeypatch, [tool('manage_publishing', action='resume', platform='xiaohongshu',
                                              account_uid=OWNER['uid'], task_id=job['id'])])
    value = result_for(response, 'manage_publishing')
    assert value['ok'] and value['publication_tasks'][0]['id'] == job['id']
    assert len(db.list(kind='UPLOAD')) == 1 and db.get(job['id'])['payload'] == before['payload']
    if accepted:
        assert db.get(job['id'])['status'] == 'SUBMITTED'
        assert db.list(kind='RECONCILE')[0]['parent_id'] == job['id']
    else:
        assert db.get(job['id'])['status'] == 'QUEUED' and not db.list(kind='RECONCILE')


def test_resume_does_not_replay_unknown_failure_or_published_task(client, social_env, monkeypatch):
    job = enqueue('xiaohongshu', render())
    db = social.store('xiaohongshu')
    db.update(job['id'], status='FAILED', result={'submission_started': True})
    args = dict(action='resume', platform='xiaohongshu', account_uid=OWNER['uid'], task_id=job['id'])
    first = chat(client, monkeypatch, [tool('manage_publishing', **args)])
    assert not result_for(first, 'manage_publishing')['ok']
    db.update(job['id'], status='PUBLISHED')
    second = chat(client, monkeypatch, [tool('manage_publishing', **args)])
    assert result_for(second, 'manage_publishing')['receipts'][0]['status'] == 'PUBLISHED'
    assert len(db.list(kind='UPLOAD')) == 1 and not db.list(kind='RECONCILE')


def test_waiting_batch_recovers_after_connection_without_replacing_upload_or_success(client, social_env, monkeypatch):
    login(client)
    profile = client.get('/api/auth/me').json()
    storage = BatchStore()
    batch = storage.create(profile, '恢复原投稿', {'kind': 'publish'}, [
        {'target': 'done-render', 'title': '已完成', 'payload': {'platform': 'xiaohongshu'}},
        {'target': 'original-render', 'title': '待恢复', 'payload': {'platform': 'xiaohongshu'}}])
    first, second = batch['items']
    storage.item(first['id'], state='completed', result={'upload_id': 'done'})
    storage.item(second['id'], state='active', result={'upload_id': 'original'})
    storage.set_status(batch['id'], 'running')
    state = {'status': 'SUBMITTED', 'verification': {'automatic': True, 'state': 'unavailable', 'checked_at': 1}}
    calls = []
    async def native(app, uid, method, path, **kwargs):
        calls.append((method, path))
        if method == 'POST':
            assert path.endswith('/original/reconcile')
            return {'id': 'check-id', 'kind': 'RECONCILE', 'status': 'QUEUED'}
        assert path.endswith('/original')
        return {'id': 'original', **state}
    monkeypatch.setattr(batches, 'native', native)
    asyncio.run(batches.tick(client.app))
    saved = storage.get(batch['id'])
    assert saved['status'] == 'waiting' and saved['items'][1]['state'] == 'blocked'
    assert saved['items'][1]['result']['recovery']['kind'] == 'connection'
    batches.manage(batch['id'], 'retry', profile)
    asyncio.run(batches.tick(client.app))
    saved = storage.get(batch['id'])
    assert saved['items'][1]['result']['upload_id'] == 'original'
    asyncio.run(batches.tick(client.app))
    assert storage.get(batch['id'])['items'][1]['state'] == 'active'  # Old failure cannot override the new pending check.
    state.update(verification={'automatic': True, 'state': 'unavailable', 'checked_at': time.time() + 1})
    asyncio.run(batches.tick(client.app))
    assert storage.get(batch['id'])['status'] == 'waiting'
    state.update(status='PUBLISHED', verification={})
    asyncio.run(batches.tick(client.app))
    saved = storage.get(batch['id'])
    assert saved['status'] == 'completed' and saved['counts'] == {'completed': 2}
    assert saved['items'][0]['result'] == {'upload_id': 'done'}
    assert [path for method, path in calls if method == 'POST'] == ['/api/platforms/social/xiaohongshu/uploads/original/reconcile']

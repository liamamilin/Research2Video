"""Read-only post-submit reconciliation: exact identity, visibility, restart and no resubmit."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import copy
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.social.adapter import AdapterError
from core.social.adapters import kuaishou as ks, xiaohongshu as xhs
from core.social.verification import EVIDENCE, delay, fingerprint, protect_context, readonly_request, select_work, submission_correlations
from core.video import store as videos
from tests.test_platforms import render
from tests.test_social_publishing import OWNER, PAYLOAD, WORK, enqueue, social_env  # noqa: F401
from web.services import social_publishing as manager
from scripts.douyin_worker import private_json

NOTE = '657b2244000000002002c666'
NOW = 1792918129


def ks_row(**changes):
    return {'workId': WORK, 'userId': int(OWNER['uid']), 'userIdStr': '3xOpaqueNotNumeric',
            'photoOwner': True, 'title': PAYLOAD['title'], 'uploadTime': NOW * 1000, 'durationSecond': 90,
            'publishStatus': 4, 'photoStatus': 0, 'judgementStatus': 1, 'photoStatusTags': [], **changes}


def xhs_row(**changes):
    return {'id': NOTE, 'display_title': PAYLOAD['title'], 'time': '2026-10-25 16:48:49',
            'video_info': {'duration': 90}, 'tab_status': 1, 'permission_code': 0,
            'schedule_post_time': 0, 'high_self': 0, **changes}


def parse_ks(row):
    return ks.work_records({'result': 1, 'data': {'list': [row]}}, OWNER['uid'], WORK)


def parse_xhs(row):
    return xhs.work_records({'code': 0, 'success': True, 'data': {'notes': [row]}}, '657b2244000000002002c62b', NOTE)


@pytest.mark.parametrize('changes,state', [({}, 'published'), ({'photoStatus': 1}, 'private'),
    ({'photoStatus': 3}, 'private'), ({'photoStatus': 99}, 'unknown'), ({'photoStatus': False}, 'unknown'),
    ({'photoStatus': None}, 'unknown'), ({'judgementStatus': 0}, 'unknown'),
    ({'photoStatusTags': [{'tagText': '限制展示'}]}, 'unknown'), ({'photoStatusTags': None}, 'unknown'),
    ({'publishStatus': 10}, 'reviewing'), ({'publishStatus': 2}, 'processing'),
    ({'publishStatus': 8}, 'processing'), ({'publishStatus': 1, 'publishType': 2}, 'scheduled'),
    ({'publishStatus': 1, 'publishType': 1}, 'reviewing'), ({'publishStatus': 11}, 'rejected'),
    ({'publishStatus': 3}, 'rejected'), ({'publishStatus': 6}, 'rejected'),
    ({'publishStatus': 9}, 'rejected'), ({'publishStatus': 15}, 'rejected'),
    ({'publishStatus': 5}, 'unknown'), ({'publishStatus': True}, 'unknown'), ({'publishStatus': '4'}, 'unknown')])
def test_kuaishou_verified_enums_fail_closed(changes, state):
    assert parse_ks(ks_row(**changes))[0]['state'] == state


def test_kuaishou_uses_exact_numeric_user_id_and_never_rounds_it():
    assert parse_ks(ks_row())[0]['work_id'] == WORK
    with pytest.raises(AdapterError, match='不属于'):
        parse_ks(ks_row(userId=123, userIdStr=OWNER['uid']))
    assert not parse_ks(ks_row(userId=float(OWNER['uid'])))
    assert not parse_ks(ks_row(workId='', publishId=1234))


@pytest.mark.parametrize('changes,state', [({}, 'published'), ({'tab_status': 2}, 'reviewing'),
    ({'tab_status': 3}, 'rejected'), ({'tab_status': 4}, 'scheduled'), ({'tab_status': 0}, 'unknown'),
    ({'permission_code': 1}, 'private'), ({'permission_code': 2}, 'private'),
    ({'permission_code': 3}, 'private'), ({'permission_code': 4}, 'private'),
    ({'permission_code': False}, 'unknown'), ({'permission_code': 99}, 'unknown'),
    ({'high_self': 1}, 'private'), ({'high_self': True}, 'private'), ({'high_self': None}, 'unknown'),
    ({'schedule_post_time': NOW + 100}, 'unknown'), ({'schedule_post_time': None}, 'unknown'),
    ({'tab_status': '1'}, 'unknown'), ({'tab_status': True}, 'unknown')])
def test_xiaohongshu_needs_explicit_public_permissions_and_published_tab(changes, state):
    assert parse_xhs(xhs_row(**changes))[0]['state'] == state


@pytest.mark.parametrize('data', [{'result': True, 'data': {'list': []}}, {'result': 0, 'data': {'list': []}},
                                {'result': 1, 'data': {}}, {'result': 1, 'data': {'list': {}}}])
def test_kuaishou_bad_list_is_not_an_empty_success(data):
    with pytest.raises(AdapterError):
        ks.work_records(data, OWNER['uid'])


@pytest.mark.parametrize('data', [{'code': 0, 'success': False, 'data': {'notes': []}},
    {'code': True, 'success': True, 'data': {'notes': []}}, {'code': 0, 'success': True, 'result': -1, 'data': {'notes': []}},
    {'code': 0, 'success': True, 'data': {}}, {'data': {'notes': []}}])
def test_xiaohongshu_challenge_or_bad_schema_never_means_no_work(data):
    with pytest.raises(AdapterError):
        xhs.work_records(data, '657b2244000000002002c62b')


def test_discovery_needs_fingerprint_or_unique_title_time_and_duration():
    rows = parse_ks(ks_row())
    assert select_work(rows, {'title': PAYLOAD['title']}) == (None, 'missing')
    lookup = {'title': PAYLOAD['title'], 'submitted_at': NOW - 10, 'duration_seconds': 90}
    assert select_work(rows, lookup)[0]['work_id'] == WORK
    assert select_work(rows, {**lookup, 'duration_seconds': 120}) == (None, 'missing')
    assert select_work(rows, {**lookup, 'submitted_at': NOW + 86400}) == (None, 'missing')
    duplicate = {**rows[0], 'work_id': '3xAnotherExact000'}
    assert select_work([*rows, duplicate], lookup) == (None, 'ambiguous')
    correlated = parse_ks(ks_row(unPublishCoverKey='opaque-cover-key'))
    assert select_work(correlated, {'correlations': [fingerprint('opaque-cover-key')], 'submitted_at': NOW - 10, 'duration_seconds': 90})[0]['work_id'] == WORK
    assert 'opaque-cover-key' not in str(correlated)


def test_cover_url_correlation_drops_signatures_and_never_retains_auth_values():
    url = 'https://cdn.kuaishou.example/covers/unique-upload-opaque-file-123456.png'
    correlations = submission_correlations({'cover': url + '?signature=secret&token=private', 'api_ph': 'private', 'cookie': 'secret'})
    rows = parse_ks(ks_row(publishCoverUrl=url + '?signature=different', title=''))
    assert select_work(rows, {'correlations': correlations, 'submitted_at': NOW - 10, 'duration_seconds': 90})[0]['work_id'] == WORK
    assert select_work(rows, {'correlations': correlations, 'submitted_at': NOW - 86400, 'duration_seconds': 90}) == (None, 'missing')
    assert not any(value in str(correlations) for value in ('private','secret','https://','api_ph'))


@pytest.mark.parametrize('method,url,body', [
    ('POST', 'https://cp.kuaishou.com/rest/cp/works/v2/video/pc/submit', None),
    ('POST', 'https://cp.kuaishou.com/rest/cp/works/v2/video/pc/isolation/submit', None),
    ('POST', 'https://cp.kuaishou.com/rest/cp/works/v2/video/pc/edit/delete', None),
    ('POST', 'https://cp.kuaishou.com/rest/new-publishing-path', None),
    ('POST', 'https://edith.xiaohongshu.com/web_api/sns/v2/note', None),
    ('POST', 'https://edith.xiaohongshu.com/web_api/sns/v1/note/delete', None),
    ('POST', 'https://www.kuaishou.com/graphql', {'operationName': 'userInfoQuery', 'query': 'mutation evil {}'}),
    ('POST', 'https://evil.example/lookup', {}), ('DELETE', 'https://creator.xiaohongshu.com/work', None),
    ('POST', 'https://cp.kuaishou.com:444/rest/cp/works/v2/video/pc/photo/list', None),
])
def test_verification_browser_blocks_all_publishing_and_unknown_writes(method, url, body):
    assert not readonly_request(method, url, body)
    route = SimpleNamespace(request=SimpleNamespace(method=method, url=url, post_data_json=body),
                            abort=AsyncMock(), continue_=AsyncMock(), fetch=AsyncMock())
    context = SimpleNamespace(route=AsyncMock())
    asyncio.run(protect_context(context))
    asyncio.run(context.route.call_args.args[1](route))
    route.abort.assert_awaited_once(); route.continue_.assert_not_awaited(); route.fetch.assert_not_awaited()


def pending_job(platform='kuaishou', *, work=WORK):
    row = render(); job = enqueue(platform, row); db = manager.store(platform)
    result = {'accepted': True, 'invocation_started': True, 'submission_started': True,
              'submission_started_at': NOW, 'duration_seconds': 90, 'submission_response': {'http_status': 200}}
    db.update(job['id'], status='SUBMITTED', work_id=work, result=result)
    return db, db.get(job['id'])


def check(db, parent, state='published', **changes):
    platform = db.platform; target = parent['work_id'] or (WORK if platform == 'kuaishou' else NOTE)
    path = f'https://www.kuaishou.com/short-video/{target}' if platform == 'kuaishou' else f'https://www.xiaohongshu.com/explore/{target}'
    receipt = {'work_id': target, 'uid': parent['uid'], 'owned': True, 'public': state == 'published',
               'platform_state': state, 'evidence': EVIDENCE[platform], 'published_at': NOW, 'url': path, **changes}
    row = db.create('RECONCILE', parent['uid'], {'work_id': parent['work_id']}, parent_id=parent['id'])
    db.update(row['id'], status='DONE', result=receipt)
    return db.get(row['id'])


@pytest.mark.parametrize('platform,target', [('kuaishou', WORK), ('xiaohongshu', NOTE)])
def test_public_receipt_updates_only_exact_platform_version_and_writes_once(social_env, platform, target):
    db, parent = pending_job(platform, work=target)
    videos.add_publication(parent['script_id'], version=1, platform='bilibili', url='https://www.bilibili.com/video/BV1xx411c7mD', published_on='2026-10-25')
    check(db, parent)
    for _ in range(3):
        manager.settle(platform, db)
    row = db.get(parent['id']); publications = videos.list_publications(parent['script_id'])
    assert row['status'] == 'PUBLISHED' and row['result']['submission_started'] is True
    assert row['result']['submission_response'] == parent['result']['submission_response']
    actual = [p for p in publications if p['platform'] == platform]
    assert len(actual) == 1 and len(publications) == 2
    assert actual[0]['work_id'] == target and actual[0]['version'] == parent['version'] and actual[0]['render_id'] == parent['render_id']
    assert actual[0]['account_uid'] == parent['uid'] and actual[0]['confirmed_at']
    assert len(db.list(kind='UPLOAD')) == 1
    assert not manager.public_job(platform, row)['can_retry']


@pytest.mark.parametrize('state', ['reviewing','scheduled','private','unknown'])
def test_nonpublic_receipt_stays_submitted_and_keeps_guard(social_env, state):
    db, parent = pending_job(); check(db, parent, state)
    manager.settle('kuaishou', db)
    row = db.get(parent['id'])
    assert row['status'] == 'SUBMITTED' and row['result']['submission_started']
    assert not row['result']['public'] and not videos.list_publications(parent['script_id'])


@pytest.mark.parametrize('changes', [{'uid': '123'}, {'work_id': '3xDifferentWork0'}, {'owned': False},
                                    {'url': 'https://evil.example/video'}, {'evidence': 'unverified_success_text'}, {'public': False}])
def test_invalid_public_proof_cannot_update_publication(social_env, changes):
    db, parent = pending_job(); check(db, parent, **changes)
    manager.settle('kuaishou', db)
    assert db.get(parent['id'])['status'] != 'PUBLISHED'
    assert db.get(parent['id'])['result']['verification']['state'] != 'published'
    assert not videos.list_publications(parent['script_id'])


def test_rejected_post_is_not_retryable_or_automatically_resubmitted(social_env):
    db, parent = pending_job(); check(db, parent, 'rejected')
    manager.settle('kuaishou', db)
    row = db.get(parent['id'])
    assert row['status'] == 'FAILED' and row['result']['accepted'] is True
    assert row['result']['confirmed_not_submitted'] is False
    with pytest.raises(AdapterError):
        manager.action('kuaishou', parent['id'], 'retry')
    manager.schedule_checks('kuaishou', db)
    assert len(db.list(kind='UPLOAD')) == 1 and len(db.list(kind='RECONCILE')) == 1


def test_backoff_and_concurrent_scheduler_never_create_or_requeue_uploads(social_env, monkeypatch):
    db, parent = pending_job(work=''); clock = [time.time() + 20]
    monkeypatch.setattr(manager.time, 'time', lambda: clock[0])
    def schedule(_):
        with manager.dispatch_lock('kuaishou'):
            manager.schedule_checks('kuaishou', db)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(schedule, range(16)))
    checks = db.list(kind='RECONCILE')
    assert len(checks) == 1 and checks[0]['payload']['lookup']['work_id'] == ''
    assert 'video_path' not in str(checks[0]['payload'])
    db.update(checks[0]['id'], status='FAILED', error='temporary failure')
    manager.settle('kuaishou', db)
    after = db.get(parent['id']); tracking = after['result']['verification']
    assert tracking['state'] == 'unavailable' and tracking['attempts'] == 1
    assert tracking['next_check_at'] == clock[0] + 15
    manager.schedule_checks('kuaishou', db)
    assert len(db.list(kind='RECONCILE')) == 1
    clock[0] += 16
    manager.schedule_checks('kuaishou', db)
    assert len(db.list(kind='RECONCILE')) == 2 and len(db.list(kind='UPLOAD')) == 1
    assert after['status'] == 'SUBMITTED' and after['result']['submission_started']
    assert delay(10000) == 3600


def test_read_only_check_has_priority_without_overlapping_upload(social_env):
    db, parent = pending_job()
    second = enqueue('kuaishou', render())
    child = db.create('RECONCILE', parent['uid'], {'work_id': WORK}, parent_id=parent['id'])
    assert db.claim(prefer_checks=True)['id'] == child['id']
    assert db.claim(prefer_checks=True) is None
    assert db.get(second['id'])['status'] == 'QUEUED'


def test_late_check_does_not_override_manually_confirmed_publication(social_env):
    db, parent = pending_job(); check(db, parent, 'private')
    db.update(parent['id'], status='PUBLISHED', phase='用户确认', result={**parent['result'], 'manual': True})
    before = copy.deepcopy(db.get(parent['id']))
    manager.settle('kuaishou', db)
    assert db.get(parent['id']) == before


def test_restart_between_publication_insert_and_task_update_is_idempotent(social_env, monkeypatch):
    db, parent = pending_job(); check(db, parent)
    update = db.update
    def crash(key, **fields):
        if key == parent['id'] and fields.get('status') == 'PUBLISHED':
            raise RuntimeError('simulated process exit')
        return update(key, **fields)
    monkeypatch.setattr(db, 'update', crash)
    with pytest.raises(RuntimeError, match='process exit'):
        manager.settle('kuaishou', db)
    assert len(videos.list_publications(parent['script_id'])) == 1
    monkeypatch.setattr(db, 'update', update)
    manager.settle('kuaishou', db)
    assert db.get(parent['id'])['status'] == 'PUBLISHED'
    assert len(videos.list_publications(parent['script_id'])) == 1 and len(db.list(kind='UPLOAD')) == 1


def test_changed_account_cannot_claim_pending_results_or_publications(social_env):
    db, parent = pending_job(); check(db, parent)
    private_json(manager.directory('kuaishou') / 'account.json', {'uid': '123', 'name': '另一个账号'})
    manager.settle('kuaishou', db)
    assert db.get(parent['id'])['status'] == 'SUBMITTED'
    assert not videos.list_publications(parent['script_id'])
    manager.schedule_checks('kuaishou', db)
    assert len(db.list(kind='RECONCILE')) == 1

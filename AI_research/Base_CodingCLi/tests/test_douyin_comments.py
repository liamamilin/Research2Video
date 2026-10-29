"""Author-comment lifecycle and a real worker guard with a fake provider (no live posts)."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import time
from types import SimpleNamespace

import pytest

from core.douyin import DouyinError, clean_first_comment, comment_receipt, creator_diagnostics
from core.douyin_store import DouyinStore
from scripts import douyin_worker as worker
from tests.test_douyin import douyin_env, OWNER, WORK, PAYLOAD, detail
from tests.test_platforms import render
from tests.test_video_scripts import _csrf, _login
from web.services import douyin as manager
from core.douyin import public_receipt

TEXT = '评论原文 https://example.test/path\n\n保留段落'
OPTION = {'enabled': True, 'text': TEXT, 'pin': False}
CID = '@j/author-comment-123456=='


def response(**changes):
    return {'status_code': 0, 'comment_info': {'comment_id': CID, 'text': TEXT, 'is_author': True, 'level': 1, **changes}}


@pytest.mark.parametrize('option', [{'enabled': True}, {'pin': True}, {'enabled': 1}, {'enabled': True, 'text': '字' * 501}, {'text': []}, {'unexpected': 'secret'}])
def test_invalid_options_never_reach_a_queue(option):
    with pytest.raises(DouyinError):
        clean_first_comment(option)


def test_comment_content_preserves_links_and_paragraphs():
    assert clean_first_comment(OPTION) == OPTION
    assert clean_first_comment({}) == {'enabled': False, 'text': '', 'pin': False}
    assert comment_receipt(response(), TEXT)['comment_id'] == CID
    assert comment_receipt({'status_code': 42}, TEXT)['confirmed_not_submitted'] is True


@pytest.mark.parametrize('option,enabled', [({'text': TEXT}, True), ({'text': TEXT, 'enabled': False}, False)])
def test_comment_text_without_flag_is_enabled_but_explicit_disable_is_kept(option, enabled):
    assert clean_first_comment(option) == {'enabled': enabled, 'text': TEXT, 'pin': False}


@pytest.mark.parametrize('options,enabled', [(None, True), ({'text': TEXT}, True),
                                           ({'text': TEXT, 'enabled': False}, False)])
def test_upload_api_preserves_omitted_comment_fields_and_inherits_template(client, douyin_env, options, enabled):
    manager.save_first_comment_template(OPTION)
    row = render()
    _login(client)
    payload = {'render_id': row['id'], **PAYLOAD}
    if options is not None:
        payload['first_comment'] = options
    response = client.post('/api/platforms/douyin/uploads', json=payload, headers=_csrf(client))
    assert response.status_code == 202, response.text
    task = manager.store().get(response.json()['id'])
    assert task['payload']['first_comment'] == {'enabled': enabled, 'text': TEXT, 'pin': False}
    assert bool(manager.store().comments(OWNER['uid']).get(task['id'])) is enabled


def test_upload_api_keeps_other_defaults_when_comment_is_omitted(client, douyin_env):
    _login(client)
    response = client.post('/api/platforms/douyin/uploads', json={'render_id': render()['id'], 'title': '标题'}, headers=_csrf(client))
    assert response.status_code == 202, response.text
    assert response.json()['payload']['description'] == '' and response.json()['payload']['tags'] == []


@pytest.mark.parametrize('data', [None, {}, {'status_code': False}, {'status_code': 0}, response(comment_id=1234567890123456789), response(text='different'), response(is_author=False), response(level=2), response(level=True)])
def test_ambiguous_receipt_never_allows_resending(data):
    with pytest.raises(DouyinError):
        comment_receipt(data, TEXT)


def test_template_account_scope_and_existing_job_snapshot(douyin_env):
    row = render()
    manager.save_first_comment_template(OPTION)
    job = manager.enqueue(row['id'], {**PAYLOAD, 'first_comment': OPTION})
    manager.save_first_comment_template({**OPTION, 'text': '新的模板'})
    assert manager.store().comment_settings('other')['enabled'] is False
    assert manager.store().comments(OWNER['uid'])[job['id']]['payload']['text'] == TEXT
    assert manager.overview()['settings']['first_comment']['text'] == '新的模板'
    assert manager.overview()['capabilities']['pin_comment'] is False


def test_waits_for_exact_public_receipt_and_promotes_once(douyin_env):
    row = render(); db = manager.store()
    parent = manager.enqueue(row['id'], {**PAYLOAD, 'first_comment': OPTION})
    db.update(parent['id'], status='SUBMITTED', work_id=WORK)
    manager._settle(db)
    child = db.comments(OWNER['uid'])[parent['id']]
    assert child['status'] == 'WAITING'  # Accepted upload alone is insufficient.
    check = manager.reconcile(parent['id'])
    db.update(check['id'], status='DONE', result=public_receipt(detail(), WORK, OWNER))
    manager._settle(db); manager._settle(db)
    children = db.list(kind='FIRST_COMMENT')
    assert len(children) == 1 and children[0]['status'] == 'QUEUED'
    assert children[0]['work_id'] == WORK
    assert db.get(parent['id'])['status'] == 'PUBLISHED'


def test_add_existing_work_is_concurrent_safe_and_immutable(douyin_env):
    row = render(); db = manager.store()
    parent = manager.enqueue(row['id'], PAYLOAD)
    db.update(parent['id'], status='SUBMITTED', work_id=WORK)
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda _: manager.add_first_comment(parent['id'], OPTION), range(8)))
    assert len({r['first_comment']['id'] for r in results}) == 1
    assert len(db.list(kind='FIRST_COMMENT')) == 1
    with pytest.raises(DouyinError, match='另一份'):
        manager.add_first_comment(parent['id'], {**OPTION, 'text': '不同内容'})
    child = db.comments(OWNER['uid'])[parent['id']]
    db.update(child['id'], status='UNKNOWN', result={'submission_started': True})
    with pytest.raises(DouyinError, match='不能重发'):
        manager.first_comment_action(parent['id'], 'retry')
    assert db.get(parent['id'])['status'] == 'SUBMITTED'
    db.update(child['id'], status='FAILED', result={'confirmed_not_submitted': True})
    manager.first_comment_action(parent['id'], 'retry')
    assert db.get(child['id'])['status'] == 'WAITING'


@pytest.mark.parametrize('result,status,retry', [({}, 'FAILED', True), ({'submission_started': True}, 'UNKNOWN', False), ({'comment_id': CID}, 'SENT', False)])
def test_recovery_never_requeues_an_uncertain_comment(tmp_path, result, status, retry):
    db = DouyinStore(str(tmp_path)); parent = db.create('UPLOAD', '123', render={'id': 'r', 'script_id': 's', 'version': 1})
    db.update(parent['id'], status='PUBLISHED', work_id=WORK)
    child = db.create('FIRST_COMMENT', '123', {'text': TEXT}, parent_id=parent['id'])
    db.update(child['id'], status='RUNNING', result=result, heartbeat=time.time() - 100, work_id=WORK)
    db.recover()
    assert db.get(child['id'])['status'] == status
    assert db.get(child['id'])['result']['confirmed_not_submitted'] is retry
    assert db.claim() is None


@pytest.mark.parametrize('mode,expected,posts', [('ok', 'SENT', 1), ('existing', 'SENT', 0), ('timeout', 'UNKNOWN', 1), ('bad_receipt', 'UNKNOWN', 1), ('rejected', 'FAILED', 1), ('wrong_owner', 'FAILED', 0), ('no_token', 'FAILED', 0), ('private', 'FAILED', 0)])
def test_worker_single_post_durable_receipt_and_preflight(tmp_path, monkeypatch, mode, expected, posts):
    db = DouyinStore(str(tmp_path)); parent = db.create('UPLOAD', '123', render={'id': 'r', 'script_id': 's', 'version': 1})
    db.update(parent['id'], status='SUBMITTED' if mode == 'private' else 'PUBLISHED', work_id=WORK)
    child = db.create('FIRST_COMMENT', '123', {'text': TEXT}, parent_id=parent['id'])
    db.update(child['id'], status='RUNNING', work_id=WORK)
    calls = {'posts': 0, 'closed': 0}

    class Response:
        status = 200
        headers = {'x-ware-csrf-token': '' if mode == 'no_token' else '1,private-token'}
        def __init__(self, value=None): self.value = value
        async def json(self): return self.value

    class Request:
        async def head(self, url, **kwargs): return Response()
        async def post(self, url, **kwargs):
            calls['posts'] += 1
            assert db.get(child['id'])['result']['submission_started'] is True
            assert kwargs['max_retries'] == 0 and kwargs['max_redirects'] == 0
            assert kwargs['data'] == {'item_id': 'private-item', 'comment_Id': '', 'text': TEXT}
            if mode == 'timeout': raise TimeoutError()
            return Response({'status_code': 42} if mode == 'rejected' else {} if mode == 'bad_receipt' else response())
        async def get(self, url, **kwargs):
            if not calls['posts']:
                return Response({'status_code': 0, 'comment_info_list':
                    [response(text='另一条评论')['comment_info'], response()['comment_info']] if mode == 'existing' else []})
            # A receipt is persisted before the optional read-back (or a crash).
            assert db.get(child['id'])['status'] == 'SENT'
            return Response({'status_code': 0, 'comment_info_list': [response()['comment_info']]})

    class Browser:
        async def new_context(self, **kwargs): return SimpleNamespace(request=Request())
        async def close(self): calls['closed'] += 1

    async def launch(*a, **kw): return Browser()
    async def identity(*a): return {**OWNER, 'uid': 'other' if mode == 'wrong_owner' else '123'}
    async def owned(*a): return {'provider_item_id': 'private-item'}
    monkeypatch.setattr(worker, 'launch', launch); monkeypatch.setattr(worker, 'identity', identity); monkeypatch.setattr(worker, 'owned_record', owned)
    asyncio.run(worker.send_first_comment(db, db.get(child['id']), None, tmp_path / 'storage.json'))
    assert db.get(child['id'])['status'] == expected
    assert calls['posts'] == posts
    if expected in ('SENT', 'UNKNOWN'):
        # Simulate re-entry after restart; guard survives process death.
        asyncio.run(worker.send_first_comment(db, db.get(child['id']), None, tmp_path / 'storage.json'))
        assert calls['posts'] == posts
    if mode in ('ok', 'existing'): assert db.get(child['id'])['result']['read_back'] is True
    if mode == 'existing': assert db.get(child['id'])['result']['existing_comment'] is True
    if posts or mode != 'private': assert calls['closed'] >= 1
    assert db.get(parent['id'])['status'] == ('SUBMITTED' if mode == 'private' else 'PUBLISHED')


def test_comment_api_roles_validation_and_scope(douyin_env, client):
    row = render(); parent = manager.enqueue(row['id'], PAYLOAD)
    manager.store().update(parent['id'], status='SUBMITTED', work_id=WORK)
    path = '/api/platforms/douyin/uploads/' + parent['id'] + '/first-comment'
    _login(client, 'viewer', 'viewer-pass-123')
    assert client.post(path, json=OPTION, headers=_csrf(client)).status_code == 403
    _login(client, 'editor', 'editor-pass-123')
    assert client.put('/api/platforms/douyin/first-comment-template', json=OPTION, headers=_csrf(client)).status_code == 403
    assert client.post(path, json=OPTION).status_code == 403
    assert client.post(path, json={**OPTION, 'pin': True}, headers=_csrf(client)).status_code == 409
    result = client.post(path, json=OPTION, headers=_csrf(client))
    assert result.status_code == 202 and result.json()['first_comment']['status'] == 'WAITING'
    worker.private_json(manager.directory() / 'account.json', {'uid': '456', 'name': '其他账号'})
    assert client.post(path, json=OPTION, headers=_csrf(client)).status_code == 409


def test_creator_diagnostics_never_retains_tokens_or_opaque_ids():
    assert creator_diagnostics({'status': {'reviewed': 1, 'sessionid': 'secret'}, 'private_status': 0, 'item_id': '@private', 'token': 'secret'}) == {'status': {'reviewed': 1}, 'private_status': 0}


def test_same_work_cannot_get_a_second_author_comment_from_another_upload(tmp_path):
    db = DouyinStore(str(tmp_path))
    first = db.create('FIRST_COMMENT', '123', {'text': TEXT}, parent_id='one')
    second = db.create('FIRST_COMMENT', '123', {'text': TEXT}, parent_id='two')
    assert db.activate_comment(first['id'], '123', WORK)
    assert not db.activate_comment(second['id'], '123', WORK)
    # A different account may legitimately comment under its own work independently.
    other = db.create('FIRST_COMMENT', '456', {'text': TEXT}, parent_id='other')
    assert db.activate_comment(other['id'], '456', WORK)


def test_server_rendered_json_has_exact_identity_and_never_executes_scripts():
    from urllib.parse import quote
    item = detail()['aweme_detail']
    for raw in (quote(json.dumps({'video': item})), 'window._ROUTER_DATA = ' + json.dumps({'video': item}) + ';'):
        assert worker.rendered_work_records([raw], WORK) == [item]
    assert worker.rendered_work_records(['window._ROUTER_DATA = stealCredentials()'], WORK) == []
    assert worker.rendered_work_records([json.dumps({'aweme_id': WORK + '0'})], WORK) == []


def test_foreign_work_receipt_cannot_release_this_videos_comment(douyin_env):
    parent = manager.enqueue(render()['id'], {**PAYLOAD, 'first_comment': OPTION}); db = manager.store()
    db.update(parent['id'], status='SUBMITTED', work_id=WORK)
    check = manager.reconcile(parent['id'])
    receipt = public_receipt(detail(), WORK, OWNER)
    db.update(check['id'], status='DONE', result={**receipt, 'work_id': '7559876543210123457'})
    manager._settle(db)
    assert db.get(parent['id'])['work_id'] == WORK
    assert db.get(parent['id'])['status'] == 'SUBMITTED'
    assert db.comments(OWNER['uid'])[parent['id']]['status'] == 'WAITING'


def test_overview_batches_comments_and_reports_latest_verification(douyin_env):
    parent = manager.enqueue(render()['id'], {**PAYLOAD, 'first_comment': OPTION}); db = manager.store()
    db.update(parent['id'], status='SUBMITTED', work_id=WORK)
    older = db.create('RECONCILE', '123', {'work_id': WORK}, parent_id=parent['id'])
    db.update(older['id'], status='RECORDED', error='旧结果')
    newer = db.create('RECONCILE', '123', {'work_id': WORK}, parent_id=parent['id'])
    db.update(newer['id'], status='RECORDED', result={'check_reason': '匿名页仍需核对', 'provider_item_id': 'secret'})
    row = manager.overview()['uploads'][0]
    assert row['verification']['count'] == 2
    assert row['verification']['reason'] == '匿名页仍需核对'
    assert row['first_comment']['status'] == 'WAITING'
    assert 'secret' not in json.dumps(row)


def test_react_flight_embedded_detail_retains_public_proof_requirements():
    record = detail()['aweme_detail']
    camel = {**record, 'awemeId': record['aweme_id'], 'status': {'privateStatus': 0, 'isDelete': False, 'isProhibited': False, 'reviewed': 1}}
    camel.pop('aweme_id')
    script = 'self.__pace_f.push(' + json.dumps([1, '12:' + json.dumps(['$', 'main', None, {'video': camel}]) + '\n']) + ');'
    extracted = worker.rendered_work_records([script], WORK)
    assert len(extracted) == 1 and public_receipt({'status_code': 0, 'aweme_detail': extracted[0]}, WORK, OWNER)['public'] is True
    camel['status']['privateStatus'] = 1
    assert public_receipt({'status_code': 0, 'aweme_detail': camel}, WORK, OWNER) is None
    camel['status'].pop('privateStatus')
    assert public_receipt({'status_code': 0, 'aweme_detail': camel}, WORK, OWNER) is None
    assert worker.rendered_work_records(['self.__pace_f.push(fetch("https://evil.test/' + WORK + '"))'], WORK) == []


def test_current_anonymous_schema_without_reviewed_requires_video_address():
    data = detail(); data['aweme_detail']['status'].pop('reviewed')
    assert public_receipt(data, WORK, OWNER) is None
    data['aweme_detail']['video'] = {'play_addr': {'url_list': ['https://www.douyin.com/aweme/v1/play/?video_id=provider-video']}}
    assert public_receipt(data, WORK, OWNER)['public'] is True
    data['aweme_detail']['status']['reviewed'] = 0
    assert public_receipt(data, WORK, OWNER) is None
    data['aweme_detail']['status'].pop('reviewed')
    data['aweme_detail']['status']['in_reviewing'] = True
    assert public_receipt(data, WORK, OWNER) is None
    data['aweme_detail']['status'].pop('in_reviewing')
    data['aweme_detail']['video']['play_addr']['url_list'] = ['https://evil.test/video']
    assert public_receipt(data, WORK, OWNER) is None


@pytest.mark.parametrize('author,level,expected', [(True, 1, 'SENT'), (False, 1, 'UNKNOWN'), (True, 2, 'UNKNOWN')])
def test_comment_check_is_read_only_and_matches_recent_author_text(tmp_path, monkeypatch, author, level, expected):
    db = DouyinStore(str(tmp_path)); parent = db.create('UPLOAD', '123', render={'id': 'r', 'script_id': 's', 'version': 1})
    db.update(parent['id'], status='PUBLISHED', work_id=WORK)
    child = db.create('FIRST_COMMENT', '123', {'text': TEXT}, parent_id=parent['id'])
    db.update(child['id'], status='UNKNOWN', work_id=WORK, result={'submission_started': True})
    check = db.create('COMMENT_CHECK', '123', parent_id=child['id'])
    calls = []

    class Response:
        status = 200
        async def json(self):
            return {'status_code': 0, 'comment_info_list': [{**response()['comment_info'], 'text': TEXT.replace('\n\n', ' '), 'is_author': author, 'level': level, 'create_time': str(int(time.time()))}]}

    class Request:
        async def get(self, *a, **kw): calls.append('GET'); return Response()
        async def post(self, *a, **kw): raise AssertionError('A read-only check must never post')

    class Browser:
        async def new_context(self, **kw): return SimpleNamespace(request=Request())
        async def close(self): pass

    async def launch(*a, **kw): return Browser()
    async def identity(*a): return OWNER
    async def owned(*a): return {'provider_item_id': 'private-item'}
    monkeypatch.setattr(worker, 'launch', launch); monkeypatch.setattr(worker, 'identity', identity); monkeypatch.setattr(worker, 'owned_record', owned)
    asyncio.run(worker.check_first_comment(db, check, None, tmp_path / 'storage'))
    assert calls == ['GET']
    assert db.get(child['id'])['status'] == expected
    assert db.get(child['id'])['payload']['text'] == TEXT
    assert db.get(check['id'])['status'] == 'DONE'
    assert TEXT not in json.dumps(db.get(check['id'])['result'])
    if expected == 'SENT':
        assert db.get(child['id'])['result']['read_back'] is True
        assert db.get(child['id'])['result']['normalized_whitespace'] is True


def test_repeated_read_only_checks_do_not_duplicate_or_retry_comments(douyin_env):
    parent = manager.enqueue(render()['id'], PAYLOAD); db = manager.store()
    db.update(parent['id'], status='PUBLISHED', work_id=WORK)
    child = manager.add_first_comment(parent['id'], OPTION)['first_comment']
    db.update(child['id'], status='UNKNOWN', result={'submission_started': True})
    for _ in range(3): manager.first_comment_action(parent['id'], 'check')
    assert len(db.list(kind='COMMENT_CHECK')) == 1
    assert db.get(child['id'])['status'] == 'UNKNOWN'
    assert db.get(child['id'])['result']['submission_started'] is True


@pytest.mark.parametrize('read_back,prior_checks,expected', [(False, 0, 1), (True, 0, 0), (False, 5, 5)])
def test_receipted_comments_waiting_for_readback_get_bounded_checks(douyin_env, monkeypatch, read_back, prior_checks, expected):
    from tests.test_douyin import REAL_TICK
    parent = manager.enqueue(render()['id'], {**PAYLOAD, 'first_comment': OPTION})
    db = manager.store()
    db.update(parent['id'], status='PUBLISHED', work_id=WORK)
    child = db.comments(OWNER['uid'])[parent['id']]
    db.update(child['id'], status='SENT', work_id=WORK,
              result={'submission_started': True, 'comment_id': CID, 'read_back': read_back})
    for _ in range(prior_checks):
        check = db.create('COMMENT_CHECK', OWNER['uid'], parent_id=child['id'])
        db.update(check['id'], status='RECORDED')
    monkeypatch.setattr(manager, 'runtime', lambda: {'ready': False})
    REAL_TICK()
    REAL_TICK()
    assert len(db.list(kind='COMMENT_CHECK')) == expected
    assert len(db.list(kind='FIRST_COMMENT')) == 1
    assert db.get(child['id'])['result']['comment_id'] == CID


def test_comment_receipt_tolerates_only_platform_whitespace_normalization():
    collapsed = TEXT.replace('\n\n', ' ')
    receipt = comment_receipt(response(text=collapsed), TEXT)
    assert receipt['normalized_whitespace'] is True
    for changed in (TEXT.replace('/path', '/different'), TEXT + ' added', TEXT.replace('评论原文', '其他内容')):
        with pytest.raises(DouyinError):
            comment_receipt(response(text=changed), TEXT)

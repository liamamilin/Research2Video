"""Request cards follow native workflow results, without mistaking old output for new."""
import pytest

from core.video import store
from core.video.adapt.review import unrequested
from tests.test_assistant_api import login, tool
from tests.test_assistant_workspace import chat
from tests.test_video_scripts import video_env, _approved_script  # noqa: F401
from web.services.assistant_store import AssistantStore
from web.services.assistant_video_state import review


def render(sid, version=1, status='COMPLETED'):
    row = store.create_render(sid, version, title='样片', source_text='正文', source_sha256='0' * 64, request={})
    store.update_render(row['id'], status=status)
    return row['id']


def workspace(client, cid):
    response = client.get('/api/assistant/workspace', params={'conversation_id': cid})
    assert response.status_code == 200, response.text
    return response.json()


def write_version(sid):
    return store.add_version(sid, title='重新生成的文稿', sections=[{'title': '开场', 'paragraphs': []}],
        length_check={'status': 'OVER', 'estimated_seconds': 123, 'target_minutes': 1.25, 'ratio': 1.64},
        edit_type='GENERATED', meta={'checks': unrequested(), 'report_path': 'output/source.md'})


@pytest.mark.parametrize('generic', [False, True])
def test_assistant_reads_distinguish_unrequested_review_and_soft_duration(client, video_env, monkeypatch, generic):
    script = _approved_script()
    sid = script['id']
    write_version(sid)
    call = tool('read_application', action_id='GET /api/video/scripts/{script_id}', path={'script_id': sid}) if generic else tool('query_video', action='get', script_id=sid)
    result = chat(client, monkeypatch, [call])
    receipt = AssistantStore().receipt(result['actions'][0]['operation_id'], {'id': 1, 'username': 'admin'})['result']
    data = receipt['result'] if generic else receipt
    assert data['review']['state'] == 'not_requested' and data['review']['verdict'] is None
    length = data['duration'] if generic else data['length']
    assert length['binding'] is False and length['estimated_seconds'] == 123 and 'status' not in length
    checks = data['version_shown']['checks']['summary'] if generic else data['check_summary']
    assert checks is None
    # Native detail and historical measurements are preserved, not edited to hide failures.
    original = client.get('/api/video/scripts/' + sid).json()['version_shown']
    assert original['checks']['summary']['passed'] is False and original['length_check']['status'] == 'OVER'


@pytest.mark.parametrize(('checks', 'state', 'verdict'), [
    ({'model_ran': False}, 'not_run', None),
    ({'model_ran': True, 'model_checks_stale': True}, 'stale', None),
    ({'model_ran': True, 'audits_complete': False}, 'incomplete', None),
    ({'model_ran': True, 'audits_complete': True, 'summary': {'serious': 1}}, 'completed', 'findings'),
    ({'model_ran': True, 'audits_complete': True, 'summary': {'serious': 0, 'must': 0}}, 'completed', 'no_blocking_findings'),
])
def test_actual_review_findings_are_not_hidden(checks, state, verdict):
    result = review({'checks': checks})
    assert result['state'] == state and result['verdict'] == verdict


def test_card_follows_new_version_then_render_and_keeps_original_result(client, video_env, monkeypatch):
    from web.services import video_executor
    script = _approved_script()
    sid = script['id']
    old = render(sid)
    monkeypatch.setattr(video_executor, 'rerun', lambda *args: store.update_script(sid, status='WRITING', error=None))
    result = chat(client, monkeypatch, [tool('operate_application',
        action_id='POST /api/video/scripts/{script_id}/steps/{step}/rerun', path={'script_id': sid, 'step': 'write'}),
        tool('continue_video', script_id=sid)], message='重新生成文稿和视频', context={'kind': 'video', 'id': sid})
    cid = result['conversation_id']
    first = workspace(client, cid)
    card = first['tasks'][0]
    assert len(card['changes']) == 2 and card['active']
    item = card['videos'][0]
    assert item['target_version'] == 2 and not item['script_ready'] and item['render'] is None
    assert item['previous_video_version'] == 1 and item['status'] == 'writing'
    assert write_version(sid) == 2
    store.update_script(sid, status='APPROVED', approved_version=2)
    rid = render(sid, 2, 'RUNNING')
    making = workspace(client, cid)['tasks'][0]['videos'][0]
    assert making['status'] == 'rendering' and making['render']['id'] == rid
    assert making['report']['path'] == 'output/source.md'
    assert '?tab=draft&version=2' in making['script_href']
    assert making['review']['verdict'] is None
    store.update_render(rid, status='COMPLETED')
    done = workspace(client, cid)['tasks'][0]
    assert done['status'] == 'completed' and not done['active']
    assert 'render=' + rid in done['videos'][0]['video_href']
    # A subsequent rewrite cannot turn the old request into the new request.
    write_version(sid)
    preserved = workspace(client, cid)['tasks'][0]['videos'][0]
    assert preserved['target_version'] == 2 and preserved['current_version'] == 3
    assert preserved['render']['id'] == rid and preserved['status'] == 'completed'
    assert store.get_render(old)['status'] == 'COMPLETED'


def test_failed_same_version_rerender_does_not_use_old_success_and_retry_is_render_only(client, video_env, monkeypatch):
    from web.services import video_render
    script = _approved_script()
    sid = script['id']
    old = render(sid)
    def start(*args, **kwargs):
        rid = render(sid, 1, 'RUNNING')
        return store.get_render(rid)
    monkeypatch.setattr(video_render, 'create', start)
    monkeypatch.setattr(video_render, 'start', lambda *args: None)
    result = chat(client, monkeypatch, [tool('manage_video', action='render', script_id=sid)])
    item = workspace(client, result['conversation_id'])['tasks'][0]['videos'][0]
    assert item['status'] == 'rendering' and item['render']['id'] != old
    store.update_render(item['render']['id'], status='FAILED', error_message='配音服务暂不可用')
    failed = workspace(client, result['conversation_id'])['tasks'][0]
    assert failed['status'] == 'failed' and not failed['active']
    assert failed['videos'][0]['error'] == '配音服务暂不可用'
    assert failed['videos'][0]['retry_step'] == 'render'


def test_failed_rewrite_and_held_workflow_do_not_poll_forever(client, video_env, monkeypatch):
    from web.services import video_executor
    from core.video import autopilot_store as ap
    script = _approved_script()
    sid = script['id']
    render(sid)
    monkeypatch.setattr(video_executor, 'rerun', lambda *args: store.update_script(sid, status='WRITING'))
    result = chat(client, monkeypatch, [tool('operate_application', action_id='POST /api/video/scripts/{script_id}/steps/{step}/rerun',
        path={'script_id': sid, 'step': 'write'}), tool('continue_video', script_id=sid)])
    store.update_script(sid, status='APPROVED', error='模型暂不可用，保留原稿')
    item = workspace(client, result['conversation_id'])['tasks'][0]['videos'][0]
    assert item['status'] == 'failed' and item['retry_step'] == 'write' and item['render'] is None
    store.update_script(sid, status='DRAFT', error=None)
    write_version(sid)
    batch = next(ref['id'] for a in result['actions'] for ref in a['references'] if ref['type'] == 'batch')
    ap.update_item(ap.get_run(batch)['items'][0]['id'], state='HELD', note='等待确认制作方案')
    card = workspace(client, result['conversation_id'])['tasks'][0]
    assert card['status'] == 'waiting' and not card['active']


def test_configuration_fields_and_failed_operations_stay_in_the_request_card(client, video_env, monkeypatch):
    login(client)
    original = client.get('/api/video/recipes/flash').json()
    result = chat(client, monkeypatch, [tool('operate_application', action_id='PUT /api/video/recipes/{key}', path={'key': 'flash'},
        body={'content': original['content'] + '\n# comment\n', 'expected_mtime': original['mtime']}),
        tool('operate_application', action_id='PUT /api/video/recipes/{key}', path={'key': 'flash'},
        body={'content': original['content'], 'expected_mtime': original['mtime']})], message='调整这个配方')
    card = workspace(client, result['conversation_id'])['tasks'][0]
    assert card['message'] == '调整这个配方' and card['status'] == 'failed'
    assert card['changes'][0]['href'] == '/video-recipes/flash'
    assert card['changes'][1]['status'] == 'failed'
    assert '已有产物需重新生成' in card['changes'][0]['impact']
    login(client, 'viewer')
    assert client.get('/api/assistant/workspace', params={'conversation_id': result['conversation_id']}).status_code >= 400


def test_changed_fields_describe_nested_changes_and_removals():
    from web.services.assistant_application import changed_fields
    assert changed_fields({'content': 'writing:\n  style: old\n  extra: remove\n'},
        {'content': 'writing:\n  style: new\n', 'expected_mtime': 1}) == ['writing.style', 'writing.extra']


def test_failed_write_cannot_claim_version_produced_by_a_later_request(client, video_env, monkeypatch):
    from web.services import video_executor
    script = _approved_script()
    sid = script['id']
    monkeypatch.setattr(video_executor, 'rerun', lambda *args: store.update_script(sid, status='WRITING', error=None))
    call = tool('operate_application', action_id='POST /api/video/scripts/{script_id}/steps/{step}/rerun', path={'script_id': sid, 'step': 'write'})
    first = chat(client, monkeypatch, [call], message='第一次重写')
    store.update_script(sid, status='APPROVED', error='第一次写稿失败')
    second = chat(client, monkeypatch, [call], message='只重试写稿', conversation_id=first['conversation_id'])
    write_version(sid)
    store.update_script(sid, status='DRAFT')
    cards = workspace(client, first['conversation_id'])['tasks']
    assert cards[0]['id'] == second['request_id'] and cards[0]['videos'][0]['script_ready']
    previous = cards[1]['videos'][0]
    assert previous['status'] == 'superseded' and not previous['script_ready'] and previous['render'] is None

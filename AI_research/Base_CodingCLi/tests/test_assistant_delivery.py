"""Real application tools, isolated workflow state, and final/recovered links."""
import json

from core.llm import ChatResponse, ToolCall
from core.video import store
from tests.test_assistant_api import fake_llm, login, tool
from tests.test_assistant_workspace import chat
from tests.test_video_scripts import video_env, _approved_script  # noqa: F401
from web.services.assistant_delivery import VideoHandoffs, canonical_links
from web.services.assistant_store import AssistantStore


def test_delegated_video_stops_repeated_polling_without_spending_step_budget(client, video_env, monkeypatch):
    script = _approved_script()
    store.update_script(script['id'], status='WRITING')
    steps = [tool('continue_video', script_id=script['id']),
             tool('query_video', action='get', script_id=script['id']),
             tool('query_video', action='get', script_id=script['id'])]
    result = chat(client, monkeypatch, steps, message='重写后自动制作视频')
    assert [a['tool'] for a in result['actions']] == ['continue_video', 'query_video']
    assert result['background_tasks'][0]['script_id'] == script['id']
    assert 'continuation' not in result and '上限' not in result['answer']
    assert '后台' in result['answer'] and '已完成' not in result['answer']
    # Durable work is left running, rather than stopped with the chat response.
    from core.video import autopilot_store
    assert autopilot_store.get_run(result['background_tasks'][0]['batch_id'])['status'] == 'RUNNING'


def test_poll_guard_finishes_other_calls_in_same_batch(client, video_env, monkeypatch):
    script = _approved_script()
    other = _approved_script('另一个对象')
    store.update_script(script['id'], status='WRITING')
    batch = ChatResponse(tool_calls=[
        ToolCall(id='poll', name='query_video', arguments={'action': 'get', 'script_id': script['id']}),
        ToolCall(id='other', name='query_video', arguments={'action': 'get', 'script_id': other['id']}),
        ToolCall(id='create', name='operate_application', arguments={'action_id': 'POST /api/matrices',
                 'body': {'key': 'second', 'name': '另一个操作'}}),
    ])
    result = chat(client, monkeypatch, [tool('continue_video', script_id=script['id']),
                  tool('query_video', action='get', script_id=script['id']), batch])
    assert len(result['actions']) == 4 and all(a['ok'] for a in result['actions'])
    assert client.get('/api/matrices/second').json()['name'] == '另一个操作'


def test_poll_guard_handles_native_detail_reads_but_not_version_reads_or_pages():
    handoffs = VideoHandoffs()
    handoffs.record('continue_video', {}, {'status': 'started', 'script_id': 'one', 'batch_id': 'batch', 'href': '/video/one'})
    arguments = {'action_id': 'GET /api/video/scripts/{script_id}', 'path': {'script_id': 'one'}}
    handoffs.record('read_application', arguments, {'result': {'status': 'WRITING', 'current_version': 1}})
    assert handoffs.waiting('query_video', {'action': 'get', 'script_id': 'one'})
    assert handoffs.waiting('read_application', arguments)
    assert not handoffs.waiting('read_application', {**arguments, 'query': {'version': 1}})
    assert not handoffs.waiting('read_application', {**arguments, 'result_offset': 30000})
    assert not handoffs.waiting('query_video', {'action': 'get', 'script_id': 'other'})


def test_followup_progress_request_uses_the_existing_durable_handoff(client, video_env, monkeypatch):
    script = _approved_script()
    store.update_script(script['id'], status='WRITING')
    started = chat(client, monkeypatch, [tool('continue_video', script_id=script['id'])])
    result = chat(client, monkeypatch, [tool('query_video', action='get', script_id=script['id']),
        tool('query_video', action='get', script_id=script['id'])],
        message='请一直查到视频完成', conversation_id=started['conversation_id'])
    assert len(result['actions']) == 1
    assert result['actions'][0]['tool'] == 'query_video'
    assert result['background_tasks'][0]['script_id'] == script['id']
    assert 'continuation' not in result


def test_old_completed_render_does_not_hide_a_new_in_progress_attempt():
    handoffs = VideoHandoffs()
    handoffs.record('continue_video', {}, {'status': 'started', 'script_id': 'one', 'batch_id': 'batch', 'href': '/video/one'})
    arguments = {'action': 'get', 'script_id': 'one'}
    handoffs.record('query_video', arguments, {'status': 'APPROVED', 'current_version': 2,
        'renders': [{'version': 2, 'status': 'QUEUED'}, {'version': 2, 'status': 'COMPLETED'}]})
    assert handoffs.waiting('query_video', arguments)
    handoffs.record('query_video', arguments, {'status': 'APPROVED', 'current_version': 2,
        'renders': [{'version': 2, 'status': 'FAILED'}]})
    assert not handoffs.waiting('query_video', arguments)


def test_failures_are_available_to_the_model_for_recovery(client, video_env, monkeypatch):
    script = _approved_script()
    store.update_script(script['id'], status='FAILED', error='真实失败')
    result = chat(client, monkeypatch, [tool('continue_video', script_id=script['id']),
        tool('query_video', action='get', script_id=script['id']),
        tool('query_video', action='get', script_id=script['id'])])
    assert len(result['actions']) == 3 and result['answer'] == '已处理。'


def test_known_app_links_use_canonical_paths_and_external_sources_are_preserved():
    refs = [{'href': '/video/one'}, {'href': '/?automation=1'}, {'href': '/reports/view?path=a%2Fb.md'}]
    text = '[文稿](https://example.invalid/video/one#section) [批次](https://wrong.example/?automation=1) ' + \
           '[来源](https://docs.example/guide) [其他](https://example.invalid/video/not-known)'
    result = canonical_links(text, refs)
    assert '[文稿](/video/one#section)' in result and '[批次](/?automation=1)' in result
    assert '[来源](https://docs.example/guide)' in result
    assert canonical_links('[来源](https://example.invalid.example.com/news)', refs) == '[来源](https://example.invalid.example.com/news)'
    assert 'example.invalid' not in result and '/video/not-known)' not in result
    assert canonical_links('https://example.invalid/missing', refs).startswith('（入口未核实')


def test_reply_stream_final_and_recovery_have_real_links(client, video_env, monkeypatch):
    script = _approved_script()
    bad = f'[查看文稿](https://example.invalid/video/{script["id"]})'
    fake_llm(monkeypatch, [tool('query_video', action='get', script_id=script['id']), ChatResponse(content=bad)])
    response = client.post('/api/assistant/stream', headers=login(client), json={'message': '查询当前文稿'})
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
    result = next(e['result'] for e in events if e['type'] == 'done')
    assert result['answer'] == f'[查看文稿](/video/{script["id"]})'
    restored = client.get('/api/assistant/conversations/' + result['conversation_id']).json()
    assert restored['requests'][0]['result']['answer'] == result['answer']


def test_historical_link_projection_does_not_rewrite_original_receipt(client, web_env, monkeypatch):
    write = chat(client, monkeypatch, [], message='历史链接')
    storage = AssistantStore()
    href = '/video/one'
    bad = '[文稿](https://example.invalid/video/one)'
    storage.references(write['conversation_id'], [{'type': 'video', 'id': 'one', 'href': href}])
    storage.save(write['request_id'], {'answer': bad, 'actions': [], 'error': None}, 'completed')
    restored = client.get('/api/assistant/conversations/' + write['conversation_id']).json()
    assert restored['requests'][0]['result']['answer'] == '[文稿](/video/one)'
    with storage.db() as db:
        original = json.loads(db.execute('select result from requests where id=?', (write['request_id'],)).fetchone()[0])
    assert original['answer'] == bad
    # Request replay returns corrected presentation without another model call.
    replay = client.post('/api/assistant', headers=login(client), json={'message': '历史链接',
        'conversation_id': write['conversation_id'], 'request_id': write['request_id']}).json()
    assert replay['answer'] == '[文稿](/video/one)'

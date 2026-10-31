"""Model views shrink context without changing stored receipts or business actions."""
from copy import deepcopy
import json

import pytest

from core.llm import ChatResponse, ToolCall
from web.services import assistant_context as context, assistant_tools, jobs_assistant
from web.services.assistant_runtime import definitions
from web.services.assistant_store import AssistantStore
from tests.test_assistant_api import fake_llm, login


def test_schema_compaction_keeps_every_tool_parameter_and_literal():
    original = jobs_assistant.tool_definitions('admin') + assistant_tools.definitions('admin')
    compact = definitions('admin')
    assert [d['function']['name'] for d in compact] == [d['function']['name'] for d in original]
    assert len(context.dumps(compact)) < len(context.dumps(original))
    example = {'title': 'Generated schema', 'type': 'object', 'required': ['title'],
               'properties': {'title': {'title': 'Title', 'type': 'string',
                   'default': 'keep', 'enum': ['first', 'second']},
                   'body': {'type': 'object', 'default': {'title': 'literal title'}}}}
    value = context.compact_schema(example)
    assert value['required'] == ['title']
    assert value['properties']['title'] == {'type': 'string', 'default': 'keep', 'enum': ['first', 'second']}
    assert value['properties']['body']['default'] == {'title': 'literal title'}
    assert example['title'] == 'Generated schema'


def board():
    return {'items': [{'script_id': str(i), 'title': f'Video {i}', 'status': 'FAILED' if i == 79 else 'APPROVED',
                      'note': 'failure reason' if i == 79 else '', 'matrix': 'ai_media',
                      'column_label': '模型竞技场', 'created_at': '2026-10-30',
                      'production_name': 'a repeated display name ' * 15} for i in range(80)],
            'failed': 1, 'stages': {'complete': 79}, 'attention': [{'script_id': '79', 'error': 'failure reason'}]}


def test_large_model_view_is_explicitly_paged_and_full_receipt_is_unchanged():
    original = board()
    frozen = deepcopy(original)
    projected = context.model_result('query_video', {'action': 'board'}, original, 'operation')
    assert original == frozen
    assert len(context.dumps(projected)) < len(context.dumps(original)) * .4
    assert projected['failed'] == 1 and projected['attention'] == frozen['attention']
    assert len(projected['items']) == 20
    assert projected['_model_view']['complete'] is False
    assert projected['_model_view']['total_items'] == 80
    assert projected['_model_view']['next_read'] == {
        'operation_id': 'operation', 'result_path': '/items', 'offset': 20, 'limit': 20, 'view': 'brief'}
    receipt = {'id': 'operation', 'tool': 'query_video', 'status': 'completed', 'result': original}
    collected = []
    offset = 0
    hashes = set()
    while True:
        page = context.receipt_page(receipt, '/items', offset, 20, 12000)
        collected.extend(page['items'])
        hashes.add(page['sha256'])
        if page['complete']:
            break
        offset = page['next_offset']
    assert collected == frozen['items']
    assert len(hashes) == 1
    assert collected[-1]['note'] == 'failure reason'


def test_no_durable_receipt_and_explicit_detail_or_write_are_never_summarized():
    value = {'content': '正文、原始计算及重要配置。' * 10000}
    for tool, args in [('query_reports', {'action': 'read'}),
                       ('read_application', {'action_id': 'GET /api/video/scripts/{script_id}'}),
                       ('get_job', {'job_name': 'research/demo'}),
                       ('manage_publishing', {'action': 'submit'}),
                       ('query_video', {'action': 'get'})]:
        assert context.model_result(tool, args, value, 'operation') is value
    assert context.model_result('query_video', {'action': 'board'}, board(), None).get('_model_view') is None


def test_brief_paging_cursors_follow_actual_rows_not_requested_page_size():
    original = board()
    receipt = {'id': 'operation', 'tool': 'query_video', 'status': 'completed',
               'arguments': {'action': 'board'}, 'result': original}
    ids, offset = [], 0
    while True:
        page = context.receipt_page(receipt, '/items', offset, 100, 12000, view='brief')
        ids.extend(item['script_id'] for item in page['items'])
        assert page['end_offset'] == offset + len(page['items'])
        if page['complete']:
            break
        offset = page['next_offset']
    assert ids == [item['script_id'] for item in original['items']]


def test_task_projection_keeps_failures_account_identity_and_recovery():
    task = {'id': 'request', 'active': True, 'failures': [{'error': 'exact failure'}], 'message': 'show tasks',
            'runs': [{'source': 'scheduled', 'run_id': 'run', 'job_name': 'science/daily',
                      'status': 'running', 'cancel_requested': False}],
            'videos': [{'script_id': 'script', 'status': 'RUNNING', 'render': {'id': 'render'}}],
            'publications': [{'platform': 'douyin', 'id': 'upload', 'account_uid': 'uid',
                              'status': 'UNKNOWN', 'recovery': {'allowed': False}, 'error': 'lookup failed'}]}
    original = {'items': [deepcopy(task) for _ in range(30)], 'total_requests': 30}
    actual = context.model_result('query_assistant_tasks', {}, original, 'operation')
    row = actual['items'][0]
    assert row['failures'] == task['failures']
    assert row['publications'] == task['publications']
    assert row['runs'] == task['runs']
    assert actual['_model_view']['complete'] is False


def test_history_keeps_auth_cards_failures_and_original_objects():
    failed = {'ok': False, 'operation_id': 'failed', 'message': 'failure details', 'references': []}
    card = {'ok': True, 'operation_id': 'card', 'input_card': {'id': 'protected'}, 'input_status': 'pending'}
    success = {'ok': True, 'operation_id': 'done', 'tool': 'manage_schedule', 'message': 'saved',
               'references': [{'type': 'schedule', 'id': 'id', 'href': '/scheduler', 'title': 'Repeated'}] * 2,
               'detail': {'schedule': '0 2 * * *', 'last_run': {'status': 'success', 'usage': {'total_tokens': 100}}}}
    source = [failed, card, success]
    original = deepcopy(source)
    compact = context.history_actions(source, brief=True)
    assert compact[:2] == source[:2]
    assert compact[2]['operation_id'] == 'done'
    assert 'detail' not in compact[2] and compact[2]['detail_in_receipt'] is True
    assert compact[2]['references'] == [{'type': 'schedule', 'id': 'id', 'href': '/scheduler'}]
    assert source == original


def test_nested_task_pages_expose_all_active_work_and_can_be_expanded():
    pubs = [{'platform': 'douyin', 'id': str(i), 'account_uid': 'account',
             'active': i >= 5, 'status': 'RUNNING' if i >= 5 else 'DONE',
             'verification': {'details': 'detail ' * 200}} for i in range(15)]
    original = {'items': [{'id': 'request', 'active': True, 'publications': pubs}]}
    projected = context.model_result('query_assistant_tasks', {}, original, 'op')
    row = projected['items'][0]
    assert len(row['publications']) == 5
    nested = row['_model_view']['publications']
    assert nested['active_count'] == 10
    assert nested['status_counts'] == {'DONE': 5, 'RUNNING': 10}
    assert nested['next_read']['result_path'] == '/items/0/publications'
    receipt = {'id': 'op', 'tool': 'query_assistant_tasks', 'status': 'completed', 'result': original}
    page = context.receipt_page(receipt, nested['next_read']['result_path'], 5, 20, 12000)
    assert [p['id'] for p in page['items']] == [str(i) for i in range(5, 15)]
    assert page['complete'] is True


def test_unexpected_task_shape_falls_back_to_original():
    original = {'items': [{'id': 'request', 'publications': None}], 'note': 'x' * 9000}
    assert context.model_result('query_assistant_tasks', {}, original, 'op') is original


def test_receipt_fragments_are_stable_and_json_pointer_is_local():
    result = {'a/b': {'~key': '正文' * 200}}
    receipt = {'id': 'operation', 'tool': 'get_job', 'status': 'completed', 'result': result}
    text, offset, hashes = '', 0, set()
    while True:
        page = context.receipt_page(receipt, '/a~1b/~0key', offset, 20, 100)
        text += page['content']
        hashes.add(page['sha256'])
        if page['complete']:
            break
        offset = page['next_offset']
    assert json.loads(text) == result['a/b']['~key']
    assert len(hashes) == 1
    with pytest.raises(ValueError):
        context.receipt_page(receipt, '/missing', 0, 20, 100)
    with pytest.raises(ValueError):
        context.receipt_page(receipt, 'not-a-pointer', 0, 20, 100)


def test_paged_tool_reads_owned_receipt_without_replaying_action(client, web_env, monkeypatch):
    from web.services.assistant_tools.work import operation, OperationQuery
    from web.auth.db import get_user_by_username
    headers = login(client)
    user = get_user_by_username('admin')
    storage = AssistantStore()
    cid = storage.new_conversation(user)
    storage.begin(user, 'original', cid, 'original operation')
    op = storage.operation('original', 'query_video', {'action': 'board'})
    original = board()
    storage.complete(op, original, True)
    storage.save('original', {'answer': 'Read complete', 'actions': []}, 'completed')
    legacy = operation(OperationQuery(operation_id=op), None, user)
    assert legacy['result'] == original
    read = operation(OperationQuery(operation_id=op, result_path='/items', offset=60), None, user)
    assert read['items'] == original['items'][60:80]
    assert read['complete'] is True
    with pytest.raises(ValueError, match='无权'):
        operation(OperationQuery(operation_id=op, result_path='/items'), None, {'id': 'different'})
    fake_llm(monkeypatch, [ChatResponse(content='原任务保持。')])
    reply = client.post('/api/assistant', headers=headers, json={'message': '只读分析'}).json()
    assert reply['usage']['total_tokens'] == 12
    assert reply['context_usage']['calls'] == 1


def test_runtime_gives_model_brief_but_persists_original_receipt(client, web_env, monkeypatch):
    from web.services.assistant_tools import video
    original = board()
    monkeypatch.setattr(video.video, 'home_board', lambda **kwargs: deepcopy(original))

    def inspect(messages, _options):
        model = json.loads(messages[-1]['content'])
        assert model['_model_view']['total_items'] == 80
        op = model['_model_view']['operation_id']
        with AssistantStore().db() as db:
            raw = db.execute('SELECT result FROM operations WHERE id=?', (op,)).fetchone()[0]
        assert json.loads(raw) == original
        assert len(model['items']) == 20
        assert '完整范围' in messages[0]['content']
        assert '当前请求上下文' in messages[1]['content']
        return ChatResponse(content='已读取第一页，可继续展开。')

    fake_llm(monkeypatch, [ChatResponse(tool_calls=[ToolCall(id='call', name='query_video',
        arguments={'action': 'board'})]), inspect])
    reply = client.post('/api/assistant', headers=login(client), json={'message': '看板状态'}).json()
    assert reply['actions'][0]['ok']
    assert reply['answer'] == '已读取第一页，可继续展开。'


def test_projection_failure_preserves_success_and_does_not_repeat_operation(client, web_env, monkeypatch):
    from web.services.assistant_tools import video
    calls = []

    def read(**kwargs):
        calls.append(True)
        return {'items': [], 'stages': {'done': 1}}

    def broken(*args, **kwargs):
        raise TypeError('unexpected future result shape')

    monkeypatch.setattr(video.video, 'home_board', read)
    monkeypatch.setattr(context, 'model_result', broken)

    def inspect(messages, options):
        assert json.loads(messages[-1]['content']) == {'items': [], 'stages': {'done': 1}}
        return ChatResponse(content='已查询。')

    fake_llm(monkeypatch, [ChatResponse(tool_calls=[ToolCall(id='call', name='query_video',
        arguments={'action': 'board'})]), inspect])
    reply = client.post('/api/assistant', headers=login(client), json={'message': '只查询'}).json()
    assert reply['actions'][0]['ok'] and reply['answer'] == '已查询。'
    assert calls == [True]

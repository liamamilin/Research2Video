"""Old list bodies can be recalled without touching prompts or native receipts."""
from copy import deepcopy
import json

import pytest

from web.services.assistant_context import dumps
from web.services.assistant_prompt_context import PromptContext


def snapshot(context, messages, index, *, tool='query_reports', arguments=None,
             result=None, operation_id='op', ok=True):
    result = result or {'items': [{'path': f'{i}.md', 'snippet': '一段研究内容' * 100} for i in range(20)],
                       'has_more': True, 'page': 2, 'total_in_scope': 100, 'scope_basis': '日期范围',
                       'unavailable': ['missing.md'], 'coverage': {'from': '2026-10-01'}}
    message = {'role': 'tool', 'tool_call_id': f'call-{len(messages)}', 'content': dumps(result)}
    messages.append(message)
    context.remember(message, tool, arguments or {'action': 'list'}, result, operation_id, index, ok=ok)
    return message


def test_unseen_and_two_recent_rounds_are_never_retired():
    context, messages = PromptContext('分析报告', limit=1), []
    first = snapshot(context, messages, 0)
    original = first['content']
    assert context.prepare(messages, 5)['context_retired'] == 0
    context.consumed()
    for index in (1, 2):
        assert context.prepare(messages, index)['context_retired'] == 0
    metrics = context.prepare(messages, 3)
    assert metrics['context_retired'] == 1
    assert metrics['context_saved_chars'] > len(original) * .8
    summary = json.loads(first['content'])
    assert summary['metadata']['has_more'] and summary['metadata']['page'] == 2
    assert summary['metadata']['unavailable'] == ['missing.md']
    assert summary['_context_receipt']['read'] == {'tool': 'query_operation', 'arguments': {'operation_id': 'op'}}
    assert context.prepare(messages, 4)['context_retired'] == 0


def test_parallel_batch_reaches_model_together_and_protocol_prose_stays_exact():
    context = PromptContext('整理列表', limit=1)
    messages = [{'role': 'user', 'content': '用户原话'},
        {'role': 'assistant', 'content': '思考及回答', 'reasoning_content': 'provider reasoning',
         'tool_calls': [{'id': 'call-2', 'function': {'name': 'query_reports', 'arguments': '{}'}}]}]
    original = deepcopy(messages)
    first = snapshot(context, messages, 0)
    context.consumed()
    parallel = [snapshot(context, messages, 3, operation_id=str(i)) for i in range(2)]
    fresh = deepcopy(parallel)
    context.prepare(messages, 3)
    assert messages[:2] == original
    assert parallel == fresh
    assert first['role'] == 'tool' and first['tool_call_id'] == 'call-2'


def test_repeated_query_retires_only_old_consumed_snapshot_with_same_scope():
    context, messages = PromptContext('查询列表', limit=1000000), []
    old = snapshot(context, messages, 0)
    context.consumed()
    latest = snapshot(context, messages, 2)
    other = snapshot(context, messages, 0, arguments={'action': 'list', 'page': 9})
    context.consumed()
    context.prepare(messages, 3)
    assert json.loads(old['content'])['_context_receipt']['reason'] == 'newer_same_query'
    assert '_context_receipt' not in latest['content']
    assert '_context_receipt' not in other['content']


def test_overview_retains_scope_account_availability_and_nested_pagination():
    context, messages = PromptContext('整理全局状态', limit=1), []
    result = {'matrices': [{'key': 'ai', 'details': '详细列表' * 5000}],
        'platform': 'douyin', 'account': {'uid': 'account'},
        'availability': {'jobs': True, 'publishing': False}, 'summary': {'working': None},
        'scope': '当前账号与矩阵', 'items': {'view': 'working', 'rows': [{'id': 'row'}],
            'total': 50, 'offset': 0, 'next_offset': 10, 'complete': False}}
    original = deepcopy(result)
    message = snapshot(context, messages, 0, tool='query_operations_overview',
                       arguments={'view': 'working'}, result=result)
    context.consumed()
    context.prepare(messages, 3)
    metadata = json.loads(message['content'])['metadata']
    assert metadata['items'] == {k: v for k, v in result['items'].items() if k != 'rows'}
    assert metadata['account'] == result['account']
    assert metadata['availability'] == result['availability']
    assert metadata['summary']['working'] is None and result == original


@pytest.mark.parametrize('tool,arguments', [
    ('query_reports', {'action': 'read'}), ('get_job', {'job_name': 'demo'}),
    ('read_application', {'action_id': 'video.script'}), ('query_operation', {'operation_id': 'old'}),
    ('query_video', {'action': 'get'}), ('manage_publishing', {'action': 'submit'}),
    ('query_sources', {'id': 'source-detail'}),
    ('query_matrices', {'key': 'ai'}), ('new_unscoped_tool', {})])
def test_explicit_details_recalled_receipts_writes_and_unknown_reads_stay_full(tool, arguments):
    context, messages = PromptContext('分析', limit=1), []
    snapshot(context, messages, 0, tool=tool, arguments=arguments)
    before = deepcopy(messages)
    context.consumed()
    context.prepare(messages, 10)
    assert messages == before


@pytest.mark.parametrize('message,options', [
    ('比较前后变化', {}), ('分析', {'ok': False}), ('分析', {'operation_id': None}),
    ('分析', {'result': {'error': 'failure', 'items': ['X' * 10000]}}),
    ('分析', {'result': {'content': 'An unexpected future detail shape' * 1000}}),
    ('分析', {'result': {'authentication_required': True, 'items': ['X' * 10000]}})])
def test_comparisons_errors_and_undurable_results_are_pinned(message, options):
    context, messages = PromptContext(message, limit=1), []
    snapshot(context, messages, 0, **options)
    before = deepcopy(messages)
    context.consumed()
    context.prepare(messages, 10)
    assert messages == before


def test_runtime_recall_keeps_original_receipt_without_reexecuting_business_read(client, web_env, monkeypatch):
    from core.llm import ChatResponse
    from tests.test_assistant_api import fake_llm, login, tool
    from tests.test_assistant_token_context import board
    from web.services import assistant_runtime as runtime
    from web.services.assistant_tools import video
    original, calls, owned = board(), [], {}

    def read(**kwargs):
        calls.append(True)
        return deepcopy(original)

    monkeypatch.setattr(video.video, 'home_board', read)
    monkeypatch.setattr(runtime, 'PromptContext', lambda message: PromptContext(message, limit=1))

    def seen(messages, kwargs):
        result = json.loads(messages[-1]['content'])
        owned['id'] = result['_model_view']['operation_id']
        assert len(result['items']) == 20
        return tool('list_jobs')

    def retired(messages, kwargs):
        old = json.loads(next(m['content'] for m in messages if m['role'] == 'tool'))
        assert old['_context_receipt']['operation_id'] == owned['id']
        assert old['_context_receipt']['summary_only']
        return tool('query_operation', operation_id=owned['id'])

    def recalled(messages, kwargs):
        receipt = json.loads(messages[-1]['content'])
        assert receipt['result'] == original and receipt['status'] == 'completed'
        return ChatResponse(content='已从原回执展开，未重新读取看板。')

    llm = fake_llm(monkeypatch, [tool('query_video', action='board'), seen,
                              tool('list_jobs'), retired, recalled])
    reply = client.post('/api/assistant', headers=login(client), json={'message': '整理看板和任务'}).json()
    assert reply['error'] is None and all(a['ok'] for a in reply['actions'])
    assert calls == [True]
    assert sum(m['context_retired'] for m in llm.context_metrics) == 1
    assert sum(m['context_saved_chars'] for m in llm.context_metrics) > 2400


def test_context_failure_keeps_native_result_and_does_not_repeat_work(client, web_env, monkeypatch):
    from core.llm import ChatResponse
    from tests.test_assistant_api import fake_llm, login, tool
    from web.services import assistant_runtime as runtime

    def broken(*args):
        raise ValueError('unexpected context shape')

    monkeypatch.setattr(runtime.PromptContext, 'prepare', broken)
    fake_llm(monkeypatch, [tool('list_jobs'), ChatResponse(content='查询完成。')])
    reply = client.post('/api/assistant', headers=login(client), json={'message': '查询任务'}).json()
    assert reply['error'] is None and len(reply['actions']) == 1
    assert reply['actions'][0]['ok']


def test_followup_keeps_comparison_context_from_recent_user_history(client, monkeypatch):
    from core.llm import ChatResponse
    from tests.test_assistant_api import fake_llm, login
    from web.services import assistant_runtime as runtime
    captured = []

    def create_context(message):
        context = PromptContext(message)
        captured.append(context.comparison)
        return context

    monkeypatch.setattr(runtime, 'PromptContext', create_context)
    fake_llm(monkeypatch, [ChatResponse(content='继续比较。')])
    reply = client.post('/api/assistant', headers=login(client), json={
        'message': '继续', 'history': [{'role': 'user', 'content': '比较两个版本的差异'}]}).json()
    assert reply['error'] is None and captured == [True]

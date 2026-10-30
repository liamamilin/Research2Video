"""Demand-loaded schemas retain discovery, roles and native business dispatch."""
import json

import pytest

from core.llm import ChatResponse
from tests.test_assistant_api import fake_llm, login, tool, write_job
from web.services.assistant_context import dumps
from web.services.assistant_runtime import definitions
from web.services.assistant_toolset import CORE, GROUPS, LOADER, ToolSet


def names(defs):
    return {item['function']['name'] for item in defs}


def test_small_seed_reduces_schema_and_all_tools_are_discoverable():
    session = ToolSet(definitions, '你好', [])
    full = definitions('admin')
    seed = session.definitions('admin')
    assert names(seed) == CORE | {LOADER}
    assert len(dumps(seed)) < len(dumps(full)) * .4
    directory = session.load({}, 'admin')
    discovered = set(directory['common']) | set().union(*map(set, directory['catalog'].values()))
    assert discovered == names(full)
    assert not directory['business_operation']
    session.load({'groups': ['all']}, 'admin')
    assert names(session.definitions('admin')) == names(full) | {LOADER}
    assert [item for item in session.definitions('admin') if item['function']['name'] != LOADER] == full


@pytest.mark.parametrize('message,group', [
    ('看看最近的视频', 'video'), ('暂停周期调度', 'schedules'),
    ('添加信息源', 'sources'), ('跨阶段批量制作', 'batches'),
    ('修改模型配置', 'settings'), ('整理矩阵', 'matrices'), ('查询报告', 'reports')])
def test_request_seeds_related_capabilities_without_classifier(message, group):
    session = ToolSet(definitions, message, [])
    assert set(GROUPS[group]) <= names(session.definitions('admin'))
    assert len(session.definitions('admin')) < len(definitions('admin'))


def test_cross_domain_loading_is_monotonic_and_current_role_filtered():
    session = ToolSet(definitions, '查报告', [])
    before = names(session.definitions('admin'))
    session.load({'groups': ['publishing'], 'names': ['query_schedules']}, 'admin')
    assert before <= names(session.definitions('admin'))
    assert {'manage_publishing', 'query_schedules'} <= names(session.definitions('admin'))
    viewer = names(session.definitions('viewer'))
    assert viewer <= names(definitions('viewer')) | {LOADER}
    assert 'manage_publishing' not in viewer
    with pytest.raises(ValueError):
        session.load({'names': ['manage_publishing']}, 'viewer')
    session.load({'groups': ['all']}, 'viewer')
    assert names(session.definitions('viewer')) == names(definitions('viewer')) | {LOADER}


def test_future_tools_have_discovery_fallback_and_invalid_load_is_atomic():
    future = {'type': 'function', 'function': {'name': 'future_tool', 'parameters': {}}}
    session = ToolSet(lambda role: definitions(role) + [future], '你好', [])
    assert session.load({}, 'viewer')['catalog']['other'] == ['future_tool']
    before = set(session.loaded)
    for arguments in ({'groups': ['video', 'missing']}, {'names': 'query_video'}, {'extra': 1}):
        with pytest.raises(ValueError):
            session.load(arguments, 'viewer')
        assert session.loaded == before
    session.load({'groups': ['other']}, 'viewer')
    assert 'future_tool' in names(session.definitions('viewer'))


def test_refs_only_seed_short_followup_and_generic_discovery_loads_domain():
    refs = [{'type': 'report'}, {'type': 'video'}]
    session = ToolSet(definitions, '继续', refs)
    assert 'create_video' in names(session.definitions('editor'))
    unrelated = ToolSet(definitions, '查询信息源', refs)
    assert 'create_video' not in names(unrelated.definitions('editor'))
    unrelated.observe('application_actions', {'domain': 'platforms'})
    assert 'manage_publishing' in names(unrelated.definitions('editor'))


def test_loader_is_context_only_and_expands_next_model_call(client, monkeypatch):
    def expanded(messages, kwargs):
        assert 'manage_schedule' in names(kwargs['tools'])
        assert json.loads(messages[-1]['content'])['business_operation'] is False
        return ChatResponse(content='已提供周期管理能力，尚未修改计划。')
    fake_llm(monkeypatch, [tool(LOADER, groups=['schedules']), expanded])
    result = client.post('/api/assistant', headers=login(client), json={'message': '看看能做什么'}).json()
    assert result['error'] is None and result['actions'] == []
    from web.services.assistant_store import AssistantStore
    with AssistantStore().db() as db:
        assert db.execute('SELECT COUNT(*) FROM operations WHERE request_id=?',
                          (result['request_id'],)).fetchone()[0] == 0


def test_valid_unadvertised_call_uses_native_handler_and_maintains_permissions(client, web_env, monkeypatch):
    write_job(web_env)
    def first(messages, kwargs):
        assert 'get_job' not in names(kwargs['tools'])
        return tool('get_job', job_name='research/memory')
    def next_step(messages, kwargs):
        assert 'get_job' in names(kwargs['tools'])
        assert json.loads(messages[-1]['content'])['job_name'] == 'research/memory'
        return ChatResponse(content='已读取。')
    fake_llm(monkeypatch, [first, next_step])
    result = client.post('/api/assistant', headers=login(client, 'viewer'), json={'message': '你好'}).json()
    assert result['actions'][0]['ok']
    fake_llm(monkeypatch, [tool(LOADER, groups=['all']),
        tool('delete_job', job_name='research/memory'), ChatResponse(content='权限不足。')])
    denied = client.post('/api/assistant', headers=login(client, 'viewer'), json={'message': '删除'}).json()
    assert not denied['actions'][0]['ok']
    assert (web_env[0] / 'jobs/research/memory.yaml').exists()

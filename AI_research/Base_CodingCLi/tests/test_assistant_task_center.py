"""Live research tracking is exact, user-scoped, read-only, and survives conversation changes."""
import time
from types import SimpleNamespace

import pytest

from core.llm import ChatResponse
from tests.test_assistant_api import fake_llm, login, tool, write_job
from tests.test_assistant_workspace import chat


class Task(SimpleNamespace):
    @property
    def is_running(self):
        return self.status == 'running'


@pytest.fixture
def manual(client, web_env, monkeypatch):
    from web.routes import jobs
    from web.runner.log_store import append_event
    write_job(web_env)
    state = {'current': None, 'starts': 0}
    def start(**kwargs):
        state['starts'] += 1
        task = Task(task_id='run-one', started_at='now', status='running', future=None)
        state['current'] = task
        append_event(str(web_env[0] / 'logs'), 'research/memory', task.task_id, {'type': 'progress', 'message': '正在读取素材'})
        return task
    monkeypatch.setattr(jobs, 'start_job', start)
    monkeypatch.setattr(jobs._registry, 'get', lambda name: state['current'] if name == 'research/memory' else None)
    monkeypatch.setattr(jobs._registry, 'is_running', lambda name: bool(state['current'] and state['current'].status == 'running'))
    return state


def center(client, limit=20):
    response = client.get('/api/assistant/tasks', params={'limit': limit})
    assert response.status_code == 200, response.text
    return response.json()


def test_registry_proves_live_run_before_first_status_event(client, web_env, monkeypatch, manual):
    chat(client, monkeypatch, [tool('run_job', job_name='research/memory')])
    from web.runner.log_store import read_events
    rows, _ = read_events(str(web_env[0] / 'logs'), 'research/memory')
    assert rows[0]['status'] == 'unknown'
    run = center(client)['tasks'][0]['runs'][0]
    assert run['status'] == 'running' and run['active'] and run['progress'] == '正在读取素材'


def test_research_tracks_original_run_and_report_after_a_later_execution(client, web_env, monkeypatch, manual):
    from web.runner.log_store import append_event
    made = chat(client, monkeypatch, [tool('run_job', job_name='research/memory')])
    value = center(client)
    run = value['tasks'][0]['runs'][0]
    assert value['active'] and run['run_id'] == 'run-one' and run['status'] == 'running'
    report = web_env[0] / 'output' / 'first.md'
    report.write_text('# 本次报告\n来自第一次运行。')
    append_event(str(web_env[0] / 'logs'), 'research/memory', 'run-one', {'type': 'status', 'status': 'success', 'output_path': str(report)})
    append_event(str(web_env[0] / 'logs'), 'research/memory', 'run-two', {'type': 'status', 'status': 'running'})
    manual['current'] = Task(task_id='run-two', status='running', future=None)
    value = center(client)
    run = value['tasks'][0]['runs'][0]
    assert value['tasks'][0]['status'] == 'completed' and not value['active']
    assert run['run_id'] == 'run-one' and run['report']['path'] == 'first.md'
    assert run['report']['verified_file'] and manual['starts'] == 1
    assert value['tasks'][0]['conversation_id'] == made['conversation_id']


def test_center_crosses_conversations_but_never_accounts(client, web_env, monkeypatch, manual):
    first = chat(client, monkeypatch, [tool('run_job', job_name='research/memory')])
    headers = login(client)
    cid = client.post('/api/assistant/conversations', headers=headers).json()['id']
    second = chat(client, monkeypatch, [tool('query_run', source='manual', job_name='research/memory', run_id='run-one')], conversation_id=cid)
    value = center(client)
    assert {t['conversation_id'] for t in value['tasks']} == {first['conversation_id'], second['conversation_id']}
    assert value['total'] == 2
    assert center(client, 1)['total'] == 2 and len(center(client, 1)['tasks']) == 1
    login(client, 'viewer')
    assert center(client)['tasks'] == []
    assert client.get('/api/assistant/tasks?limit=101').status_code == 422


def test_interrupted_run_and_unknown_operation_never_appear_completed(client, web_env, monkeypatch, manual):
    chat(client, monkeypatch, [tool('run_job', job_name='research/memory')])
    manual['current'] = None
    value = center(client)
    assert not value['active'] and value['tasks'][0]['status'] == 'waiting'
    assert value['tasks'][0]['runs'][0]['status'] == 'interrupted'
    from web.services.assistant_store import AssistantStore
    from web.auth.db import get_user_by_username
    store = AssistantStore(); user = get_user_by_username('admin'); cid = store.new_conversation(user)
    store.begin(user, 'unknown-configuration', cid, '上次未返回的操作')
    store.operation('unknown-configuration', 'manage_matrix', {'action': 'create', 'key': 'maybe_created'})
    with store.db() as db:
        db.execute("UPDATE requests SET owner='old-server' WHERE id='unknown-configuration'")
    item = center(client)['tasks'][0]
    assert item['status'] == 'waiting' and not item['active'] and item['changes'][0]['status'] == 'started'
    assert manual['starts'] == 1


def test_repeated_research_polling_hands_off_and_keeps_other_actions(client, web_env, monkeypatch, manual):
    fake_llm(monkeypatch, [tool('run_job', job_name='research/memory'),
        tool('query_run', source='manual', job_name='research/memory', run_id='run-one'),
        tool('query_run', source='manual', job_name='research/memory', run_id='run-one')])
    response = client.post('/api/assistant', headers=login(client), json={'message': '开始研究并跟进'}).json()
    assert len(response['actions']) == 2 and '任务中心' in response['answer'] and not response.get('continuation')
    assert manual['starts'] == 1


def test_cancellation_keeps_running_until_worker_acknowledges(client, web_env, monkeypatch, manual):
    chat(client, monkeypatch, [tool('run_job', job_name='research/memory')])
    manual['current'].status = 'cancelled'
    manual['current'].future = SimpleNamespace(done=lambda: False)
    run = center(client)['tasks'][0]['runs'][0]
    assert run['active'] and run['status'] == 'cancelling' and run['cancel_requested']
    manual['current'].future = SimpleNamespace(done=lambda: True)
    from web.runner.log_store import append_event
    append_event(str(web_env[0] / 'logs'), 'research/memory', 'run-one', {'type': 'status', 'status': 'cancelled'})
    run = center(client)['tasks'][0]['runs'][0]
    assert not run['active'] and run['status'] == 'cancelled'


def test_native_generic_run_and_read_only_center_tool_are_tracked(client, web_env, monkeypatch, manual):
    chat(client, monkeypatch, [tool('operate_application', action_id='POST /api/jobs/{name}/run', path={'name': 'research/memory'})])
    value = center(client)
    assert value['tasks'][0]['runs'][0]['run_id'] == 'run-one'
    response = chat(client, monkeypatch, [tool('query_assistant_tasks', limit=10, query='research/memory')])
    assert response['actions'][0]['ok'] and manual['starts'] == 1
    action = response['actions'][0]
    assert action['href'] == '/ask?tasks=1'
    assert any(ref['type'] == 'conversation' and '?conversation=' in ref['href'] for ref in action['references'])
    # A plain read-only status request does not bury the actual operation.
    assert center(client)['total'] == 1


def test_multiple_requests_for_same_job_read_log_snapshot_once(client, web_env, monkeypatch, manual):
    chat(client, monkeypatch, [tool('run_job', job_name='research/memory')])
    chat(client, monkeypatch, [tool('query_run', source='manual', job_name='research/memory', run_id='run-one')])
    from web.routes import jobs
    original = jobs.get_job_logs
    calls = []
    def read(*args, **kwargs):
        calls.append(kwargs.get('run_id'))
        return original(*args, **kwargs)
    monkeypatch.setattr(jobs, 'get_job_logs', read)
    assert len(center(client)['tasks']) == 2 and calls == [None]


def test_scheduled_exact_run_respects_current_role(client, web_env, monkeypatch):
    from web.services import managed_scheduler as managed
    write_job(web_env)
    store = managed.store(); now = time.time()
    plan = store.save('research/memory', '* * * * *', now=now-120, max_retries=0)
    store.enqueue_due(now); run = store.claim(now); assert store.start(run['id'], 123, now)
    store.finish(run['id'], 'failed', error='研究提供者不可用')
    result = chat(client, monkeypatch, [tool('query_run', source='scheduled', run_id=run['id'])])
    value = center(client)['tasks'][0]['runs'][0]
    assert value['status'] == 'failed' and value['error'] == '研究提供者不可用' and not value['active']
    assert plan['id'] in value['href']
    from web.auth.db import get_user_by_username
    from web.services.assistant_store import AssistantStore
    user = get_user_by_username('admin')
    # Demotion cannot turn a saved admin receipt into fresh admin access.
    from web.auth import db as users
    with users.connect() as db:
        db.execute("UPDATE users SET role='viewer' WHERE id=?", (user['id'],))
    login(client)
    item = center(client)['tasks'][0]['runs'][0]
    assert item['status'] == 'unavailable' and item['report'] is None

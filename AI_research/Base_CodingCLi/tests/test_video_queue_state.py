"""Queue labels describe durable work without changing or restarting it."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime

import pytest

from core.video import autopilot_store as ap, store
from tests.test_video_scripts import video_env  # noqa: F401
from web.services.video_queue_state import phase, snapshot


@pytest.mark.parametrize('status,render,expected,executing', [
    ('RESEARCHING', None, 'researching', True), ('WRITING', None, 'writing', True),
    ('PLAN_READY', None, 'waiting_plan', False), ('DRAFT', None, 'waiting_approval', False),
    ('APPROVED', None, 'queued_render', False), ('FAILED', None, 'failed', False),
    ('APPROVED', 'QUEUED', 'queued_render', False), ('APPROVED', 'SUBMITTING', 'preparing_render', True),
    ('APPROVED', 'RUNNING', 'rendering', True), ('APPROVED', 'DOWNLOADING', 'downloading', True),
    ('APPROVED', 'COMPLETED', 'done', False), ('APPROVED', 'FAILED', 'held', False),
])
def test_current_stage_does_not_equate_working_with_running(status, render, expected, executing):
    item = {'state': 'WORKING', 'script_id': 's'}
    script = {'status': status, 'current_version': 2, 'stage': None}
    renders = [{'version': 2, 'status': render}] if render else []
    before = deepcopy((item, script, renders))
    result = phase(item, script, renders)
    assert result['stage'] == expected and result['executing'] is executing
    assert (item, script, renders) == before


def test_pending_missing_and_terminal_items_keep_their_meaning():
    assert phase({'state': 'PENDING', 'script_id': None}, None, [])['stage'] == 'queued_script'
    assert phase({'state': 'WORKING', 'script_id': 'deleted'}, None, [])['stage'] == 'unknown'
    for state, expected in [('HELD', 'held'), ('DONE', 'done'), ('FAILED', 'failed'), ('CANCELLED', 'cancelled')]:
        assert phase({'state': state}, None, [])['stage'] == expected
    queued = phase({'state': 'WORKING', 'script_id': 's'},
                   {'status': 'WRITING', 'current_version': 1, 'stage': '排队'}, [])
    assert queued['stage'] == 'queued_script' and queued['executing'] is False


def test_old_completed_video_does_not_complete_a_new_version():
    item = {'state': 'WORKING', 'script_id': 's'}
    script = {'status': 'APPROVED', 'current_version': 2}
    assert phase(item, script, [{'version': 1, 'status': 'COMPLETED'}])['stage'] == 'queued_render'
    renders = [{'version': 2, 'status': 'FAILED'}, {'version': 2, 'status': 'COMPLETED'}]
    assert phase(item, script, renders)['stage'] == 'done'
    renders.insert(0, {'version': 2, 'status': 'QUEUED'})
    assert phase(item, script, renders)['stage'] == 'queued_render'


def test_summary_includes_batches_outside_latest_ten_with_one_read_connection(video_env, monkeypatch):
    runs = [ap.create_run({}, [{'column_key': 'flash', 'topic': {'title': str(i)}}]) for i in range(15)]
    preparing = ap.create_run({}, [])
    writing = store.create_script('flash', {'title': '正在写稿'})
    store.update_script(writing['id'], status='WRITING')
    ap.update_item(runs[0]['items'][0]['id'], state='WORKING', script_id=writing['id'])
    rendering = store.create_script('flash', {'title': '等待出片'})
    store.update_script(rendering['id'], status='APPROVED', current_version=1)
    ap.update_item(runs[1]['items'][0]['id'], state='WORKING', script_id=rendering['id'])
    render = store.create_render(rendering['id'], 1, title='t', source_text='t', source_sha256='hash', request={})
    store.update_render(render['id'], status='QUEUED')
    past = store.create_script('flash', {'title': '已完成'})
    done = store.create_render(past['id'], 1, title='t', source_text='t', source_sha256='hash', request={})
    store.update_render(done['id'], status='COMPLETED', completed_at='2026-10-30T12:00:00+0800')
    before = ap.list_runs(limit=None)
    real_connect, calls = store.connect, []

    @contextmanager
    def counted(*args, **kwargs):
        calls.append(kwargs)
        with real_connect(*args, **kwargs) as db:
            yield db

    monkeypatch.setattr(store, 'connect', counted)
    result = snapshot(runs[-10:], now=datetime.fromisoformat('2026-10-30T15:00:00+08:00'))
    assert calls == [{}]
    summary = result['summary']
    assert summary['active_batches'] == 16 and summary['preparing_batches'] == 1
    assert summary['executing_items'] == 1 and summary['queued_items'] == 14
    assert summary['phase_counts'] == {'writing': 1, 'queued_render': 1, 'queued_script': 13}
    assert summary['completed_renders_today'] == 1
    assert result['items'][runs[0]['items'][0]['id']]['stage'] == 'writing'
    assert len(result['items']) == 15
    assert ap.list_runs(limit=None) == before
    assert ap.get_run(preparing['id'])['stage'] == 'TOPICS'

"""Deterministic maintenance/run admission and abandoned control receipts."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
import threading
import time

from fastapi import Request
import pytest

from tests.test_assistant_api import login, write_job
from web.services import assistant_maintenance as maintenance


def test_run_that_passed_an_early_check_cannot_start_during_maintenance(client, web_env, monkeypatch):
    from web.routes import jobs
    root, _ = web_env
    write_job(web_env)
    original = jobs.load_job
    def load_and_begin_maintenance(*args, **kwargs):
        (root / 'state/assistant_maintenance.json').write_text(json.dumps({'pid': os.getpid(), 'action': 'restart', 'status': 'applying'}))
        return original(*args, **kwargs)
    monkeypatch.setattr(jobs, 'load_job', load_and_begin_maintenance)
    monkeypatch.setattr(jobs, 'start_job', lambda **kwargs: pytest.fail('task registered after maintenance admission closed'))
    response = client.post('/api/jobs/research/memory/run', headers=login(client))
    assert response.status_code == 409, response.text


def test_maintenance_checks_activity_after_any_admitted_run_registration(web_env, monkeypatch):
    registered = threading.Event()
    competing = threading.Event()
    request = Request({'type': 'http', 'method': 'POST', 'headers': []})
    user = {'id': 42, 'username': 'admin', 'role': 'admin'}
    monkeypatch.setattr(maintenance, 'status', lambda: {'restart_supported': True,
        'manual_runs': [{'run_id': 'admitted'}] if registered.is_set() else [],
        'rounds': [], 'application': {'pid': os.getpid()}, 'gateway': {}})
    def maintain():
        competing.set()
        with pytest.raises(ValueError, match='正在运行'):
            maintenance.start('restart', 'refuse', request, user)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with maintenance.run_admission():
            future = pool.submit(maintain)
            assert competing.wait(3)
            registered.set()
        future.result(timeout=5)


@pytest.mark.parametrize('matches', [False, True])
def test_gateway_replacement_reconciles_an_unconsumed_command(web_env, matches):
    root, users = web_env
    state = root / 'state'
    (state / 'maintenance').mkdir()
    key = 'd' * 32
    user = {'id': users['admin']['id'], 'username': 'admin'}
    target = {'public_port': 9999, 'app_port': 9998, 'lan_access': True, 'bind_host': '0.0.0.0'}
    record = {'id': key, 'user_id': str(user['id']), 'action': 'rebind_gateway', 'status': 'applying',
              'created': time.time()-10, 'gateway_pid': 999999, 'gateway_target': target}
    receipt = state / 'maintenance' / (key + '.json')
    receipt.write_text(json.dumps(record))
    (state / 'assistant_maintenance.json').write_text(json.dumps(record))
    actual = {'pid': os.getpid(), 'started_at': time.time(), 'heartbeat': time.time(),
              'port': 9999, 'app_port': 9998, 'lan_access': matches, 'bind_host': '0.0.0.0' if matches else '127.0.0.1'}
    (state / 'gateway_runtime.json').write_text(json.dumps(actual))
    result = maintenance.receipt(key, user)
    assert result['status'] == ('completed' if matches else 'interrupted')
    assert json.loads(receipt.read_text())['status'] == result['status']
    assert not (state / 'gateway_control.json').exists()


def test_shutdown_passive_traffic_does_not_spawn_but_document_can(web_env, monkeypatch):
    from scripts.gateway import Supervisor
    root, _ = web_env
    (root / 'state/app_runtime.json').write_text(json.dumps({'pid': 12345}))
    (root / 'state/assistant_maintenance.json').write_text(json.dumps({'pid': 12345, 'action': 'shutdown', 'status': 'completed'}))
    supervisor = Supervisor(0, root, cooldown=0)
    monkeypatch.setattr(supervisor, 'healthy', lambda: False)
    monkeypatch.setattr(supervisor, 'claimed', lambda: False)
    spawns = []
    monkeypatch.setattr(supervisor, '_spawn', lambda: spawns.append(True))
    assert supervisor.ensure(allow_shutdown_wake=False) == (False, 'stopped')
    assert not spawns
    assert supervisor.ensure(allow_shutdown_wake=True) == (False, 'starting')
    assert len(spawns) == 1


def test_assistant_returns_maintenance_receipt_before_another_model_call(client, monkeypatch):
    from tests.test_assistant_api import fake_llm, tool
    fake_llm(monkeypatch, [tool('maintain_service', action='restart')])
    monkeypatch.setattr(maintenance, 'start', lambda *args: {'maintenance_id': 'a'*32, 'status': 'waiting'})
    response = client.post('/api/assistant', headers=login(client), json={'message': '重启应用'})
    result = response.json()
    assert response.status_code == 200 and result['error'] is None
    assert result['actions'][0]['maintenance_id'] == 'a'*32
    assert '已提交' in result['answer']


@pytest.mark.parametrize('state,exited', [('Z', True), ('Z+', True), ('S', False)])
def test_process_exit_probe_handles_unreaped_children(monkeypatch, state, exited):
    from core import processes
    from types import SimpleNamespace
    monkeypatch.setattr(processes.os, 'kill', lambda *args: None)
    monkeypatch.setattr(processes.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0, stdout=state))
    assert processes.process_exited(1234) is exited

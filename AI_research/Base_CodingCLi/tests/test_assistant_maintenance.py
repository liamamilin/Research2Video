"""Isolated service lifecycle checks; never signal the developer's service."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]


def port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def wait_until(operation, timeout=25):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        try:
            value = operation()
            if value:
                return value
        except Exception as exc:
            last = exc
        time.sleep(0.2)
    raise AssertionError(f'condition not reached: {last}')


@pytest.mark.parametrize('via_gateway', [False, True])
def test_isolated_service_restart_and_shutdown(web_env, monkeypatch, via_gateway):
    import httpx
    import yaml
    root, _ = web_env
    config = root / 'web.yaml'
    data = yaml.safe_load(config.read_text())
    data['server']['port'] = port()
    if via_gateway:
        data['gateway'] = {'enabled': True, 'app_port': port(), 'lan_access': False}
    config.write_text(yaml.safe_dump(data))
    (root / 'config' / 'system.yaml').write_text('ai:\n  model: offline-test\nsearch:\n  provider: duckduckgo\n')
    env = {**os.environ, 'AI_RESEARCH_WEB_CONFIG': str(config), 'AI_RESEARCH_ENV': 'development'}
    env.pop('AI_RESEARCH_SECRET_KEY', None)
    env.pop('WEB_SECRET_KEY', None)
    log = (root / 'server.log').open('wb')
    child = subprocess.Popen([sys.executable, str(ROOT / 'run_web.py'), 'serve'], cwd=ROOT, env=env, stdout=log, stderr=log)
    gateway_child = None
    client = httpx.Client(base_url=f'http://127.0.0.1:{data["server"]["port"]}', timeout=3, trust_env=False)
    new_pid = None
    try:
        if via_gateway:
            wait_until(lambda: httpx.get(f'http://127.0.0.1:{data["gateway"]["app_port"]}/api/health', trust_env=False).status_code == 200)
            gateway_child = subprocess.Popen([sys.executable, str(ROOT / 'scripts/gateway.py')], cwd=ROOT, env=env, stdout=log, stderr=log)
        wait_until(lambda: client.get('/api/health').status_code == 200)
        first = client.get('/api/health').json()['instance_id']
        duplicate = subprocess.Popen([sys.executable, str(ROOT / 'run_web.py'), 'serve'], cwd=ROOT, env=env, stdout=log, stderr=log)
        duplicate.wait(timeout=15)
        assert duplicate.returncode != 0
        assert json.loads((root / 'state/app_runtime.json').read_text())['pid'] == child.pid
        assert (root / 'state/server.pid').read_text().strip() == str(child.pid)
        assert client.post('/api/auth/login', json={'username': 'admin', 'password': 'admin-pass-123', 'remember': True}).status_code == 200
        headers = {'X-CSRF-Token': client.cookies.get('ai_research_csrf')}
        result = client.post('/api/assistant/service', headers=headers, json={'action': 'restart'})
        assert result.status_code == 200, result.text
        key = result.json()['maintenance_id']
        # Reap the old process so kill(pid,0) no longer sees a zombie.
        child.wait(timeout=15)
        receipt = root / 'state' / 'maintenance' / (key + '.json')
        def complete():
            record = json.loads(receipt.read_text())
            assert record['status'] != 'failed', record
            return record if record['status'] == 'completed' else None
        record = wait_until(complete, 30)
        new_pid = record['actual']['pid']
        assert new_pid != child.pid
        assert client.get('/api/health').json()['instance_id'] != first
        client.cookies.delete('ai_research_access')
        refreshed = client.post('/api/auth/refresh', headers=headers)
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.headers['X-Session-Days'] == '30'
        headers = {'X-CSRF-Token': client.cookies.get('ai_research_csrf')}
        assert client.get(f'/api/assistant/maintenance/{key}').json()['status'] == 'completed'
        result = client.post('/api/assistant/service', headers=headers, json={'action': 'shutdown'})
        assert result.status_code == 200, result.text
        stopped = root / 'state' / 'maintenance' / (result.json()['maintenance_id'] + '.json')
        wait_until(lambda: json.loads(stopped.read_text()).get('status') == 'completed', 20)
        if via_gateway:
            # This is the same passive endpoint polled by the mounted UI card.
            for _ in range(3):
                passive = client.get(f'/api/assistant/maintenance/{result.json()["maintenance_id"]}')
                assert passive.status_code == 503
                assert passive.json()['error']['code'] == 'app_stopped'
            assert json.loads((root / 'state/app_runtime.json').read_text())['pid'] == new_pid
            # A deliberate document navigation still revives the application.
            def reopen():
                client.get('/', headers={'Accept': 'text/html'})
                return client.get('/api/health').status_code == 200
            wait_until(reopen, 30)
            revived = json.loads((root / 'state/app_runtime.json').read_text())['pid']
            assert revived != new_pid
            new_pid = revived
    finally:
        client.close()
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=10)
        if new_pid:
            try:
                os.kill(new_pid, 15)
            except ProcessLookupError:
                pass
        if gateway_child:
            gateway_child.terminate()
            gateway_child.wait(timeout=10)
        log.close()


def test_gateway_rebind_receipt_and_actual_listener(web_env, monkeypatch):
    import yaml
    from web.services import assistant_maintenance
    root, users = web_env
    config = root / 'web.yaml'
    data = yaml.safe_load(config.read_text())
    data['server']['port'] = port()
    data['gateway'] = {'enabled': True, 'app_port': port(), 'lan_access': False}
    config.write_text(yaml.safe_dump(data))
    env = {**os.environ, 'AI_RESEARCH_WEB_CONFIG': str(config), 'AI_RESEARCH_ENV': 'development'}
    # Only its listener is tested; no requests to the gateway revive an app.
    log = (root / 'gateway.log').open('wb')
    child = subprocess.Popen([sys.executable, str(ROOT / 'scripts/gateway.py')], cwd=ROOT, env=env, stdout=log, stderr=log)
    runtime_path = root / 'state' / 'gateway_runtime.json'
    try:
        wait_until(lambda: runtime_path.exists())
        current = json.loads(runtime_path.read_text())
        assert current['bind_host'] == '127.0.0.1'
        data['gateway']['lan_access'] = True
        config.write_text(yaml.safe_dump(data))
        from web import settings
        settings.reset_settings()
        user = {'id': users['admin']['id'], 'username': 'admin', 'role': 'admin'}
        from fastapi import Request
        result = assistant_maintenance.start('rebind_gateway', 'refuse', Request({'type': 'http', 'method': 'POST', 'headers': []}), user)
        key = result['maintenance_id']
        def complete():
            value = assistant_maintenance.receipt(key, user)
            return value if value['status'] == 'completed' else None
        record = wait_until(complete, 15)
        assert record['actual']['bind_host'] == '0.0.0.0'
        assert record['actual']['lan_access'] is True
        assert assistant_maintenance.gateway_status()['restart_required'] is False
    finally:
        child.terminate()
        child.wait(timeout=10)
        log.close()


def test_gateway_owned_application_restarts_without_the_test_reaping_its_pid(web_env):
    """Exercise the production parent/child relationship, not a test-owned app."""
    import httpx
    import yaml
    root, _ = web_env
    config = root / 'web.yaml'
    data = yaml.safe_load(config.read_text())
    data['server']['port'] = port()
    data['gateway'] = {'enabled': True, 'app_port': port(), 'lan_access': False}
    config.write_text(yaml.safe_dump(data))
    (root / 'config/system.yaml').write_text('ai:\n  model: offline-test\n')
    env = {**os.environ, 'AI_RESEARCH_WEB_CONFIG': str(config), 'AI_RESEARCH_ENV': 'development'}
    env.pop('AI_RESEARCH_SECRET_KEY', None)
    env.pop('WEB_SECRET_KEY', None)
    log = (root / 'gateway-owned-app.log').open('wb')
    gateway = subprocess.Popen([sys.executable, str(ROOT / 'scripts/gateway.py')], cwd=ROOT, env=env, stdout=log, stderr=log)
    client = httpx.Client(base_url=f'http://127.0.0.1:{data["server"]["port"]}', timeout=3, trust_env=False)
    known_pids = set()
    try:
        def open_app():
            client.get('/', headers={'Accept': 'text/html'})
            return client.get('/api/health').status_code == 200
        wait_until(open_app, 30)
        original = json.loads((root / 'state/app_runtime.json').read_text())['pid']
        known_pids.add(original)
        parent = subprocess.check_output(['ps', '-p', str(original), '-o', 'ppid='], text=True)
        assert int(parent.strip()) == gateway.pid
        assert client.post('/api/auth/login', json={'username': 'admin', 'password': 'admin-pass-123'}).status_code == 200
        headers = {'X-CSRF-Token': client.cookies.get('ai_research_csrf')}
        response = client.post('/api/assistant/service', headers=headers, json={'action': 'restart'})
        assert response.status_code == 200, response.text
        key = response.json()['maintenance_id']
        def completed():
            record = json.loads((root / 'state/maintenance' / (key + '.json')).read_text())
            assert record['status'] != 'failed', record
            return record if record['status'] == 'completed' else None
        record = wait_until(completed, 30)
        known_pids.add(record['actual']['pid'])
        assert record['actual']['pid'] != original
        assert client.get('/api/health').json()['instance_id'] == record['actual']['instance_id']
    finally:
        client.close()
        for pid in known_pids:
            try:
                os.kill(pid, 15)
            except ProcessLookupError:
                pass
        gateway.terminate()
        gateway.wait(timeout=10)
        log.close()


def test_failed_gateway_rebind_restores_previous_listener(web_env, monkeypatch):
    import importlib.util
    from web.settings import get_settings
    root, _ = web_env
    state = root / 'state'
    key = 'f' * 32
    previous = {'bind_host': '127.0.0.1', 'port': 9999, 'lan_access': False, 'app_port': 9998}
    (state / 'gateway_runtime.json').write_text(json.dumps(previous))
    (state / 'maintenance').mkdir()
    receipt = state / 'maintenance' / (key + '.json')
    receipt.write_text(json.dumps({'id': key, 'status': 'applying', 'action': 'rebind_gateway'}))
    spec = importlib.util.spec_from_file_location('gateway_failure_test', ROOT / 'scripts/gateway.py')
    gateway = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gateway)
    calls = []
    def serve_once(rebinding, fallback):
        calls.append((rebinding, fallback))
        return [(0, key), (1, None), (0, None)][len(calls)-1]
    monkeypatch.setattr(gateway, '_serve_once', serve_once)
    assert gateway.serve() == 0
    assert calls[2] == (None, previous)
    assert json.loads(receipt.read_text())['status'] == 'failed'

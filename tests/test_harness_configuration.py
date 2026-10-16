"""Recovery must survive bad settings, interrupted responses and concurrent edits."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time

from fastapi import HTTPException, Request
import pytest

from tests.test_assistant_api import login
from web.services.assistant_store import AssistantStore
from web.services import assistant_configuration as service
from web.routes import config


@pytest.fixture
def configuration(web_env):
    root, users = web_env
    path = root / 'config/system.yaml'
    path.write_text('ai:\n  model: former\nbudget:\n  monthly_usd_limit: 12\n  block_pipeline: true\n')
    user = {'id': users['admin']['id'], 'username': 'admin', 'role': 'admin'}
    request = Request({'type': 'http', 'method': 'POST', 'headers': []})
    return path, user, request


@pytest.mark.parametrize('patch', [
    {'budget': {'monthly_usd_limit': 'not-a-number'}},
    {'budget': {'monthly_usd_limt': 25}},
    {'budget': {'monthly_usd_limt': None}},
    {'budget': {'monthly_usd_limit': -1}},
    {'budget': {'block_pipeline': 1}},
    {'ai': {'max_tokens': 'broken'}},
    {'research': {'mode': 'invented'}},
    {'notifications': {'email': {'to_addrs': 'someone@example.test'}}},
])
def test_invalid_config_fails_before_file_publish(client, configuration, patch):
    path, _, _ = configuration
    original = path.read_text()
    response = client.put('/api/config/system', json={'patch': patch}, headers=login(client))
    assert response.status_code == 422, response.text
    assert path.read_text() == original


def test_assistant_validates_before_testing_or_preparing_recovery(configuration, monkeypatch):
    path, user, request = configuration
    monkeypatch.setattr(service, 'test_candidate', lambda *a, **kw: pytest.fail('invalid config reached model probe'))
    with pytest.raises(ValueError):
        service.apply({'ai': {'max_tokens': 'bad'}}, path.stat().st_mtime, request, user)


@pytest.mark.parametrize('failure', ['snapshot', 'card'])
def test_recovery_prerequisite_failure_does_not_change_configuration(configuration, monkeypatch, failure):
    from web.services import yaml_io
    path, user, request = configuration
    original = path.read_text()
    if failure == 'snapshot':
        write = yaml_io.atomic_write
        def failing_write(target, content):
            if 'assistant-config-backups' in target:
                raise OSError('snapshot unavailable')
            return write(target, content)
        monkeypatch.setattr(yaml_io, 'atomic_write', failing_write)
    else:
        monkeypatch.setattr(AssistantStore, 'prepare', lambda *a, **kw: (_ for _ in ()).throw(OSError('card DB unavailable')))
    with pytest.raises(OSError):
        service.apply({'budget': {'monthly_usd_limit': 18}}, path.stat().st_mtime, request, user)
    assert path.read_text() == original


def test_recovery_survives_lost_response_and_input_expiry(client, configuration, monkeypatch):
    path, user, request = configuration
    original = path.read_text()
    saved = service.apply({'budget': {'monthly_usd_limit': 18}}, path.stat().st_mtime, request, user)
    key = saved['rollback_id']
    store = AssistantStore()
    with store.db() as db:
        db.execute('UPDATE protected_inputs SET created=? WHERE id=?', (time.time() - 86400, key))
    # Recovery lookup and rollback use no model, even without the old action card.
    headers = login(client)
    assert client.get('/api/assistant/configuration/recovery').json()['rollback_id'] == key
    response = client.post('/api/assistant/rollback/' + key, headers=headers)
    assert response.status_code == 200, response.text
    assert path.read_text() == original
    assert client.get('/api/assistant/configuration/recovery').json()['rollback_id'] is None


def test_old_rollback_cannot_overwrite_a_subsequent_settings_edit(client, configuration):
    path, user, request = configuration
    saved = service.apply({'budget': {'monthly_usd_limit': 18}}, path.stat().st_mtime, request, user)
    headers = login(client)
    current = client.get('/api/config/system').json()
    response = client.put('/api/config/system', json={'patch': {'budget': {'monthly_usd_limit': 24}}, 'expected_version': current['version']}, headers=headers)
    assert response.status_code == 200
    rollback = client.post('/api/assistant/rollback/' + saved['rollback_id'], headers=headers)
    assert rollback.status_code == 409
    assert config.get_system_config(user=user)['parsed']['budget']['monthly_usd_limit'] == 24


def test_legacy_rollback_is_discoverable_after_the_secret_card_expiry(client, configuration, web_env):
    path, user, request = configuration
    previous = path.read_text()
    changed = config.update_system_config({'patch': {'budget': {'monthly_usd_limit': 18}}}, request, user)
    key = AssistantStore().prepare(user, 'config_rollback', {'expected_mtime': changed['mtime']})
    directory = web_env[0] / 'state/assistant-config-backups'
    directory.mkdir()
    (directory / (key + '.yaml')).write_text(previous)
    with AssistantStore().db() as db:
        db.execute('UPDATE protected_inputs SET created=1 WHERE id=?', (key,))
    headers = login(client)
    assert client.get('/api/assistant/configuration/recovery').json()['rollback_id'] == key
    assert client.post('/api/assistant/rollback/' + key, headers=headers).status_code == 200
    assert path.read_text() == previous


def test_post_save_bookkeeping_failure_retains_recovery_and_real_saved_result(configuration, monkeypatch):
    from web.indexer import vector_sync
    path, user, request = configuration
    monkeypatch.setattr(vector_sync, 'reset_client', lambda: (_ for _ in ()).throw(RuntimeError('cache reset failed')))
    saved = service.apply({'budget': {'monthly_usd_limit': 18}}, path.stat().st_mtime, request, user)
    assert saved['saved'] and saved['warnings']
    assert service.recovery(user)['rollback_id'] == saved['rollback_id']
    assert config.get_system_config(user=user)['parsed']['budget']['monthly_usd_limit'] == 18


def test_config_compare_and_publish_is_atomic_across_writers(configuration):
    path, user, request = configuration
    snapshot = config.get_system_config(user=user)
    barrier = threading.Barrier(2)
    def write(value):
        barrier.wait(timeout=5)
        try:
            return config.update_system_config({'patch': {'budget': {'monthly_usd_limit': value}},
                'expected_version': snapshot['version'], 'expected_mtime': snapshot['mtime']}, request, user)
        except HTTPException as error:
            return {'rejected': error.status_code}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, [18, 24]))
    assert sum(bool(r.get('ok')) for r in results) == 1
    assert sum(r.get('rejected') == 409 for r in results) == 1


def test_schedule_compare_and_publish_rejects_stale_version(tmp_path):
    from core.schedule_store import ScheduleStore, ScheduleConflict
    store = ScheduleStore(str(tmp_path))
    first = store.save('research/test', '0 9 * * 1', timezone='Asia/Shanghai')
    store.save('research/test', '0 9 * * 5', schedule_id=first['id'], timezone='Asia/Shanghai', editing=True, expected_version=first['version'])
    with pytest.raises(ScheduleConflict, match='已被修改'):
        store.save('research/test', '0 9 * * 2', schedule_id=first['id'], timezone='UTC', editing=True, expected_version=first['version'])
    actual = store.get(first['id'])
    assert actual['schedule'] == '0 9 * * 5' and actual['timezone'] == 'Asia/Shanghai'

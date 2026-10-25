"""Interrupted protected operations expose metadata without replaying secrets."""
import json
import sqlite3

from tests.test_application_assistant import command
from tests.test_assistant_api import login
from web.services.assistant_store import AssistantStore


def test_processing_input_from_an_old_process_becomes_unknown_without_replay(client, web_env, monkeypatch):
    from web.services import assistant_store
    root, _ = web_env
    first = command(client, monkeypatch, 'request_protected_input', {'kind': 'create_token', 'token_name': 'recovery'})
    key = first['actions'][0]['input_card']['id']
    store = AssistantStore()
    from web.auth import db
    user = db.get_user_by_username('admin')
    assert store.claim_input(key, user)
    headers = login(client)
    native = client.post('/api/auth/tokens', headers=headers, json={'name': 'recovery', 'expires_days': 30}).json()
    plaintext = native['token']
    monkeypatch.setattr(assistant_store, 'OWNER', 'replacement-process')
    response = client.get('/api/assistant/inputs/' + key)
    assert response.json()['status'] == 'interrupted'
    assert client.post('/api/assistant/inputs/' + key, headers=headers, json={}).status_code == 409
    verification = client.get('/api/assistant/inputs/' + key + '/verify')
    assert verification.status_code == 200
    assert verification.json()['tokens'][0]['id'] == native['id']
    assert plaintext not in response.text + verification.text
    assert plaintext.encode() not in (root / 'state/assistant.db').read_bytes()
    assert len(client.get('/api/auth/tokens').json()['tokens']) == 1
    assert client.delete('/api/auth/tokens/' + str(native['id']), headers=headers).status_code == 200
    assert client.get('/api/assistant/inputs/' + key + '/verify').json()['tokens'][0]['revoked']
    login(client, 'editor')
    assert client.get('/api/assistant/inputs/' + key + '/verify').status_code == 404


def test_legacy_protected_input_database_is_upgraded_and_processing_is_not_replayed(web_env):
    root, users = web_env
    path = root / 'state/assistant.db'
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE protected_inputs (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL, parameters TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL, result TEXT)')
    db.execute('INSERT INTO protected_inputs VALUES (?,?,?,?,?,?,?)', ('legacy', str(users['admin']['id']), 'create_token', '{}', 'processing', 1, None))
    db.commit()
    db.close()
    store = AssistantStore()
    user = {'id': users['admin']['id'], 'username': 'admin'}
    recovered = store.input('legacy', user)
    assert recovered['status'] == 'interrupted'
    assert not store.claim_input('legacy', user)
    assert '核验' in json.loads(recovered['result'])['error']

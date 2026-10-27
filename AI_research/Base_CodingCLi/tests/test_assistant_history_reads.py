"""History recovery uses bounded SQL work and keeps receipt ownership intact."""
import sqlite3

import pytest

from web.services.assistant_store import AssistantStore


def test_operations_check_ownership_without_rebuilding_history(web_env, monkeypatch):
    store = AssistantStore()
    user = {'id': 'owner'}
    cid = store.new_conversation(user)
    monkeypatch.setattr(store, 'conversation', lambda *args: pytest.fail('history was decoded again'))
    assert store.operations(user, cid) == []
    with pytest.raises(ValueError, match='无权访问'):
        store.operations({'id': 'other'}, cid)
    with pytest.raises(ValueError, match='无权访问'):
        store.operations(user, 'missing')


def test_history_batches_receipts_and_recovers_interrupted_operations(web_env, monkeypatch):
    store = AssistantStore()
    user = {'id': 'owner'}
    cid = store.new_conversation(user)
    for index in range(60):
        request = f'request-{index}'
        store.begin(user, request, cid, str(index))
        operation = store.operation(request, 'query_matrix', {'key': 'ai'})
        store.complete(operation, {'job_name': 'matrix'}, True)
        if index < 59:
            store.save(request, {'answer': '', 'actions': []}, 'completed')
    with store.db() as db:
        db.execute("UPDATE requests SET owner='old-process' WHERE id='request-59'")
    statements = []
    connect = sqlite3.connect

    def traced(*args, **kwargs):
        db = connect(*args, **kwargs)
        db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(sqlite3, 'connect', traced)
    result = store.conversation(user, cid)
    reads = [sql for sql in statements if sql.startswith('SELECT * FROM operations')]
    assert len(reads) == 1
    assert len(result['requests']) == 60
    assert result['requests'][-1]['status'] == 'interrupted'
    assert all(len(row['result']['actions']) == 1 for row in result['requests'])
    assert all(row['result']['actions'][0]['ok'] for row in result['requests'])

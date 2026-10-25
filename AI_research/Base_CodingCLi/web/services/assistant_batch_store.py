"""A frozen list of application operations, with durable per-item receipts.

This ledger coordinates existing executors; it never generates or uploads media.
"""
from __future__ import annotations

import json
import time
import uuid
from collections import Counter

from web.services.assistant_store import AssistantStore, identity


class BatchStore(AssistantStore):
    def __init__(self):
        super().__init__()
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS application_batches (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL,
                    spec TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS application_batches_user ON application_batches(user_id,created);
                CREATE TABLE IF NOT EXISTS application_batch_items (
                    id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, slot INTEGER NOT NULL,
                    target TEXT NOT NULL, title TEXT NOT NULL, payload TEXT NOT NULL,
                    stage TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'pending',
                    result TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '');
                CREATE INDEX IF NOT EXISTS application_batch_items_batch ON application_batch_items(batch_id,slot);
            ''')

    def create(self, user: dict, title: str, spec: dict, items: list[dict]) -> dict:
        key, now = uuid.uuid4().hex, time.time()
        with self.db() as db:
            db.execute('INSERT INTO application_batches VALUES (?,?,?,?,?,?,?)',
                       (key, identity(user), title, json.dumps(spec, ensure_ascii=False), 'prepared', now, now))
            for slot, item in enumerate(items):
                db.execute('INSERT INTO application_batch_items(id,batch_id,slot,target,title,payload) VALUES (?,?,?,?,?,?)',
                           (uuid.uuid4().hex, key, slot, item['target'], item['title'], json.dumps(item['payload'], ensure_ascii=False)))
        return self.get(key, user)

    def get(self, key: str, user: dict | None = None) -> dict:
        with self.db() as db:
            row = db.execute('SELECT * FROM application_batches WHERE id=?', (key,)).fetchone()
            if not row or user is not None and row['user_id'] != identity(user):
                raise ValueError('批次不存在或无权访问')
            batch = {**dict(row), 'spec': json.loads(row['spec'])}
            batch['items'] = [{**dict(r), 'payload': json.loads(r['payload']), 'result': json.loads(r['result'])}
                              for r in db.execute('SELECT * FROM application_batch_items WHERE batch_id=? ORDER BY slot', (key,))]
        batch['counts'] = dict(Counter(i['state'] for i in batch['items']))
        batch['active'] = batch['status'] == 'running' or any(i['state'] == 'active' for i in batch['items'])
        batch['href'] = '/ask?tasks=1&batch=' + key
        return batch

    def list(self, user: dict | None = None, *, active: bool = False, limit: int = 100) -> list[dict]:
        where, values = [], []
        if user:
            where.append('user_id=?'); values.append(identity(user))
        if active:
            where.append("""(status='running' OR EXISTS (SELECT 1 FROM application_batch_items i WHERE i.batch_id=application_batches.id AND i.state='active')
                OR (status='waiting' AND json_extract(spec,'$.kind')='publish' AND EXISTS (
                    SELECT 1 FROM application_batch_items i WHERE i.batch_id=application_batches.id
                    AND i.state='blocked' AND json_extract(i.result,'$.upload_id') IS NOT NULL)))""")
        with self.db() as db:
            query = 'SELECT id FROM application_batches' + (' WHERE ' + ' AND '.join(where) if where else '') + ' ORDER BY created DESC'
            # The UI history is bounded; the worker must not starve older active batches.
            if not active or user is not None:
                query += ' LIMIT ?'
                values.append(limit)
            ids = [r[0] for r in db.execute(query, values)]
        return [self.get(key, user) for key in ids]

    def set_status(self, key: str, status: str) -> None:
        with self.db() as db:
            db.execute('UPDATE application_batches SET status=?,updated=? WHERE id=?', (status, time.time(), key))

    def item(self, key: str, *, state: str, stage: str | None = None, result: dict | None = None, error: str = '') -> None:
        fields = {'state': state, 'error': error}
        if stage is not None:
            fields['stage'] = stage
        if result is not None:
            fields['result'] = json.dumps(result, ensure_ascii=False)
        with self.db() as db:
            db.execute('UPDATE application_batch_items SET ' + ','.join(f'{k}=?' for k in fields) + ' WHERE id=?', (*fields.values(), key))

    def settle(self, key: str) -> None:
        batch = self.get(key)
        if batch['status'] not in ('running', 'waiting'):
            return
        states = {i['state'] for i in batch['items']}
        if batch['status'] == 'waiting' and not states <= {'completed', 'skipped'}:
            return
        if states & {'pending', 'active', 'dispatching'}:
            return
        status = 'waiting' if states & {'unknown', 'blocked'} else 'failed' if 'failed' in states else 'completed'
        self.set_status(key, status)


def view(batch: dict) -> dict:
    """Public projection: no internal dispatch arguments or original actor secrets."""
    return {k: batch[k] for k in ('id', 'title', 'status', 'created', 'updated', 'counts', 'active', 'href')} | {
        'kind': batch['spec']['kind'], 'scope': batch['spec'].get('scope', {}),
        'items': [{k: item[k] for k in ('id', 'target', 'title', 'stage', 'state', 'error')} |
                  {'result': {'href': fallback_href(batch, item), **item['result']}} for item in batch['items']]}


def fallback_href(batch: dict, item: dict) -> str:
    from urllib.parse import quote
    kind = batch['spec']['kind']
    if kind == 'publish':
        return '/platforms?platform=' + quote(item['payload']['platform'], safe='')
    if kind == 'matrix_render':
        return '/matrices/' + quote(item['target'], safe='')
    if kind == 'schedules':
        return '/scheduler?plan=' + quote(item['target'], safe='')
    if item['payload'].get('type') == 'research':
        return '/jobs/' + quote(item['payload']['job'], safe='')
    return '/outputs'

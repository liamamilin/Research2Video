"""User-scoped assistant conversations, operation receipts and protected forms."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time
import uuid

from web.settings import get_settings

OWNER = uuid.uuid4().hex


def identity(user: dict) -> str:
    return str(user.get('id') or user['username'])


class AssistantStore:
    def __init__(self):
        root = Path(get_settings().paths.state_dir)
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / 'assistant.db'
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, updated REAL NOT NULL,
                    refs TEXT NOT NULL DEFAULT '[]');
                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, user_id TEXT NOT NULL,
                    message TEXT NOT NULL, status TEXT NOT NULL, owner TEXT NOT NULL,
                    result TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS requests_conversation ON requests(conversation_id,created);
                CREATE TABLE IF NOT EXISTS operations (
                    id TEXT PRIMARY KEY, request_id TEXT NOT NULL, tool TEXT NOT NULL,
                    arguments TEXT NOT NULL, status TEXT NOT NULL, result TEXT, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS protected_inputs (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL,
                    parameters TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL,
                    result TEXT);
                CREATE INDEX IF NOT EXISTS requests_user_created ON requests(user_id,created);
                CREATE INDEX IF NOT EXISTS operations_request_created ON operations(request_id,created);
            ''')
            if not db.in_transaction:
                db.execute('BEGIN IMMEDIATE')
            columns = {row[1] for row in db.execute('PRAGMA table_info(protected_inputs)')}
            for name, declaration in (('owner', "TEXT NOT NULL DEFAULT ''"), ('updated', 'REAL NOT NULL DEFAULT 0')):
                if name not in columns:
                    db.execute(f'ALTER TABLE protected_inputs ADD COLUMN {name} {declaration}')
            if 'context' not in {row[1] for row in db.execute('PRAGMA table_info(conversations)')}:
                db.execute("ALTER TABLE conversations ADD COLUMN context TEXT NOT NULL DEFAULT 'null'")

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA busy_timeout=10000')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def conversation(self, user: dict, conversation_id: str | None = None) -> dict:
        with self.db() as db:
            if conversation_id:
                row = db.execute('SELECT * FROM conversations WHERE id=? AND user_id=?',
                                 (conversation_id, identity(user))).fetchone()
                if not row:
                    raise ValueError('对话不存在或无权访问')
            else:
                row = db.execute('SELECT * FROM conversations WHERE user_id=? ORDER BY updated DESC LIMIT 1',
                                 (identity(user),)).fetchone()
                if not row:
                    key = uuid.uuid4().hex
                    db.execute('INSERT INTO conversations(id,user_id,updated) VALUES (?,?,?)',
                               (key, identity(user), time.time()))
                    row = db.execute('SELECT * FROM conversations WHERE id=?', (key,)).fetchone()
            result = dict(row)
            result['refs'] = json.loads(result['refs'])
            result['context'] = json.loads(result['context'])
            records = list(reversed(db.execute('SELECT * FROM requests WHERE conversation_id=? ORDER BY created DESC LIMIT 60', (row['id'],)).fetchall()))
            # Recover receipts in one indexed read, rather than one query per message.
            operations_by_request: dict[str, list] = {}
            if records:
                ids = [record['id'] for record in records]
                for operation in db.execute(
                    f"SELECT * FROM operations WHERE request_id IN ({','.join('?' for _ in ids)}) ORDER BY created", ids
                ):
                    operations_by_request.setdefault(operation['request_id'], []).append(operation)
            result['requests'] = []
            for record in records:
                item = dict(record)
                item['result'] = json.loads(item['result'])
                if item['status'] == 'running' and item['owner'] != OWNER:
                    item['status'] = 'interrupted'
                    item['result']['error'] = '服务已重启，回复中断；请先核验已记录操作和实际业务状态。'
                    db.execute("UPDATE requests SET status='interrupted',result=? WHERE id=?",
                               (json.dumps(item['result'], ensure_ascii=False), item['id']))
                known = {action.get('operation_id') for action in item['result'].get('actions', [])}
                for operation in operations_by_request.get(item['id'], []):
                    if operation['id'] in known or item['status'] == 'running':
                        continue
                    operation_result = json.loads(operation['result']) if operation['result'] else {}
                    complete = operation['status'] == 'completed'
                    item['result'].setdefault('actions', []).append({
                        'operation_id': operation['id'], 'tool': operation['tool'], 'ok': complete,
                        'job_name': operation_result.get('job_name'),
                        'message': f"{operation['tool']}：" + ('已记录完成' if complete else '结果待核验，请先查询业务状态，勿重放整条请求')})
                item.pop('owner')
                from web.services.assistant_delivery import canonical_links
                links = result['refs'] + [ref for action in item['result'].get('actions', [])
                                          for ref in action.get('references', [])]
                item['result']['answer'] = canonical_links(item['result'].get('answer') or '', links)
                result['requests'].append(item)
            return result

    def begin(self, user: dict, request_id: str, conversation_id: str, message: str) -> tuple[bool, dict]:
        with self.db() as db:
            row = db.execute('SELECT * FROM requests WHERE id=?', (request_id,)).fetchone()
            if row:
                if row['user_id'] != identity(user) or row['conversation_id'] != conversation_id or row['message'] != message:
                    raise ValueError('请求标识已被使用，请刷新后重新提交')
                return False, {**json.loads(row['result']), 'status': row['status']}
            if db.execute("SELECT 1 FROM requests WHERE conversation_id=? AND status='running'", (conversation_id,)).fetchone():
                raise ValueError('此对话仍有请求在运行，请等待完成或停止响应')
            now = time.time()
            empty = {'answer': '', 'actions': [], 'error': None}
            db.execute('INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?)',
                       (request_id, conversation_id, identity(user), message, 'running', OWNER,
                        json.dumps(empty), now, now))
            db.execute('UPDATE conversations SET updated=? WHERE id=?', (now, conversation_id))
            return True, empty

    def save(self, request_id: str, result: dict, status: str = 'running'):
        with self.db() as db:
            db.execute('UPDATE requests SET result=?,status=?,updated=? WHERE id=?',
                       (json.dumps(result, ensure_ascii=False, default=str), status, time.time(), request_id))

    def request(self, key: str, user: dict) -> dict:
        with self.db() as db:
            row = db.execute('SELECT * FROM requests WHERE id=? AND user_id=?', (key, identity(user))).fetchone()
            if not row:
                raise ValueError('请求不存在或无权访问')
            return {**dict(row), 'result': json.loads(row['result'])}

    def new_conversation(self, user: dict) -> str:
        key = uuid.uuid4().hex
        with self.db() as db:
            db.execute('INSERT INTO conversations(id,user_id,updated) VALUES (?,?,?)',
                       (key, identity(user), time.time()))
        return key

    def conversations(self, user: dict) -> list[dict]:
        with self.db() as db:
            return [dict(row) for row in db.execute('''
                SELECT c.id,c.updated,
                COALESCE((SELECT substr(message,1,60) FROM requests WHERE conversation_id=c.id ORDER BY created LIMIT 1),'新对话') AS title,
                (SELECT count(*) FROM requests WHERE conversation_id=c.id) AS request_count
                FROM conversations c WHERE c.user_id=? ORDER BY c.updated DESC LIMIT 80''', (identity(user),))]

    def set_context(self, user: dict, conversation_id: str, context: dict | None):
        with self.db() as db:
            if not db.execute('UPDATE conversations SET context=? WHERE id=? AND user_id=?',
                              (json.dumps(context, ensure_ascii=False), conversation_id, identity(user))).rowcount:
                raise ValueError('对话不存在或无权访问')

    def operations(self, user: dict, conversation_id: str) -> list[dict]:
        with self.db() as db:
            # Ownership includes empty conversations, without decoding/recovering
            # the entire history a second time when the workspace already read it.
            if not db.execute('SELECT 1 FROM conversations WHERE id=? AND user_id=?',
                              (conversation_id, identity(user))).fetchone():
                raise ValueError('对话不存在或无权访问')
            rows = db.execute('''SELECT o.* FROM operations o JOIN requests r ON o.request_id=r.id
                WHERE r.conversation_id=? AND r.user_id=? ORDER BY o.created DESC LIMIT 80''',
                (conversation_id, identity(user))).fetchall()
            return [{**dict(row), 'arguments': json.loads(row['arguments']),
                     'result': json.loads(row['result']) if row['result'] else {}} for row in rows]

    def recent_work(self, user: dict, limit: int = 20) -> dict:
        """Page this user's operation requests; plain questions do not bury work."""
        from web.services import assistant_tools, jobs_assistant
        writes = sorted(set(jobs_assistant.TOOLS) - jobs_assistant.READ_TOOLS |
                        {name for name, spec in assistant_tools.registry().items() if spec.write})
        marks = ','.join('?' for _ in writes)
        where = f'''r.user_id=? AND EXISTS (
            SELECT 1 FROM operations o WHERE o.request_id=r.id AND
            (o.tool IN ({marks}) OR o.tool='query_run' OR (o.tool='query_publishing' AND
             (json_extract(o.result,'$.publication_tasks') IS NOT NULL OR
              json_extract(o.result,'$.publication_preparations') IS NOT NULL))))'''
        with self.db() as db:
            values = (identity(user), *writes)
            total = db.execute(f'SELECT count(*) FROM requests r WHERE {where}', values).fetchone()[0]
            records = [dict(r) for r in db.execute(f'''SELECT r.id,r.conversation_id,r.message,r.status,r.owner,r.created
                FROM requests r WHERE {where} ORDER BY r.created DESC LIMIT ?''', (*values, limit))]
            ids = [r['id'] for r in records]
            if not ids:
                return {'records': [], 'operations': [], 'total': total}
            rows = db.execute(f"SELECT * FROM operations WHERE request_id IN ({','.join('?' for _ in ids)}) ORDER BY created DESC", ids).fetchall()
        for record in records:
            if record.pop('owner') != OWNER and record['status'] == 'running':
                record['status'] = 'interrupted'
        operations = [{**dict(row), 'arguments': json.loads(row['arguments']),
                       'result': json.loads(row['result']) if row['result'] else {}} for row in rows]
        return {'records': list(reversed(records)), 'operations': operations, 'total': total}

    def operation(self, request_id: str, tool: str, arguments: dict, task: dict | None = None) -> str:
        from web.services.assistant_safety import redact
        key = uuid.uuid4().hex
        with self.db() as db:
            db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?)',
                       (key, request_id, tool, json.dumps(redact(arguments), ensure_ascii=False), 'started',
                        json.dumps({'task': task}, ensure_ascii=False) if task else None, time.time()))
        return key

    def complete(self, key: str, result: dict, ok: bool):
        with self.db() as db:
            db.execute('UPDATE operations SET status=?,result=? WHERE id=?',
                       ('completed' if ok else 'failed', json.dumps(result, ensure_ascii=False, default=str), key))

    def receipt(self, key: str, user: dict) -> dict:
        with self.db() as db:
            row = db.execute('SELECT o.* FROM operations o JOIN requests r ON o.request_id=r.id WHERE o.id=? AND r.user_id=?',
                             (key, identity(user))).fetchone()
            if not row:
                raise ValueError('操作回执不存在或无权访问')
            result = dict(row)
            for field in ('arguments', 'result'):
                result[field] = json.loads(result[field]) if result[field] else None
            return result

    def references(self, conversation_id: str, refs: list[dict]):
        with self.db() as db:
            db.execute('UPDATE conversations SET refs=? WHERE id=?',
                       (json.dumps(refs[-30:], ensure_ascii=False), conversation_id))

    def prepare(self, user: dict, kind: str, parameters: dict) -> str:
        key = uuid.uuid4().hex
        with self.db() as db:
            now = time.time()
            db.execute('INSERT INTO protected_inputs(id,user_id,kind,parameters,status,created,result,owner,updated) VALUES (?,?,?,?,?,?,?,?,?)',
                       (key, identity(user), kind, json.dumps(parameters, ensure_ascii=False), 'pending', now, None, '', now))
        return key

    def input(self, key: str, user: dict) -> dict:
        with self.db() as db:
            row = db.execute('SELECT * FROM protected_inputs WHERE id=? AND user_id=?', (key, identity(user))).fetchone()
            if not row:
                raise ValueError('输入卡片不存在或无权访问')
            result = dict(row)
            result['parameters'] = json.loads(result['parameters'])
            if result['status'] == 'processing' and result.get('owner') != OWNER:
                result.update(status='interrupted', updated=time.time(), result=json.dumps({'error': '提交执行进程已更换，业务结果待核验；不会自动重放'}))
                db.execute("UPDATE protected_inputs SET status=?,result=?,updated=? WHERE id=?", (result['status'], result['result'], result['updated'], key))
            if result['kind'] != 'config_rollback' and time.time() - result['created'] > 3600 and result['status'] == 'pending':
                raise ValueError('输入卡片已过期，请重新发起操作')
            return result

    def claim_input(self, key: str, user: dict) -> bool:
        with self.db() as db:
            return bool(db.execute("UPDATE protected_inputs SET status='processing',owner=?,updated=? WHERE id=? AND user_id=? AND status='pending' AND created>=?",
                                   (OWNER, time.time(), key, identity(user), time.time()-3600)).rowcount)

    def finish_input(self, key: str, result: dict, status: str = 'completed'):
        with self.db() as db:
            db.execute('UPDATE protected_inputs SET status=?,result=?,updated=? WHERE id=?',
                       (status, json.dumps(result, ensure_ascii=False), time.time(), key))

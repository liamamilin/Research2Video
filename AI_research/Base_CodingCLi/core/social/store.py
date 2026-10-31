"""Crash-safe queues for new publishers. Each platform has a separate database and locks."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
import uuid

from core import sqlite_util
from core.social.adapter import accepted_without_work_id
from core.social.registry import PLATFORMS

ACTIVE = ('QUEUED', 'RUNNING')
# Queue files whose schema this process has already ensured. The DDL takes the write lock, so running it on
# every read made a page wait behind any publisher holding that lock.
_SCHEMA_READY: set[str] = set()


class SocialStore:
    def __init__(self, state_dir: str, platform: str):
        if platform not in PLATFORMS:
            raise ValueError('Unsupported publisher')
        self.platform = platform
        folder = Path(state_dir) / 'platforms' / platform
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = str(folder / 'queue.db')
        if self.path in _SCHEMA_READY and os.path.exists(self.path):
            return
        with self.connect(write=True) as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,kind TEXT NOT NULL,uid TEXT NOT NULL,status TEXT NOT NULL,
                    phase TEXT NOT NULL DEFAULT '',payload TEXT NOT NULL,result TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT '',work_id TEXT NOT NULL DEFAULT '',parent_id TEXT NOT NULL DEFAULT '',
                    script_id TEXT NOT NULL DEFAULT '',version INTEGER NOT NULL DEFAULT 0,render_id TEXT NOT NULL DEFAULT '',
                    heartbeat REAL NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS once_per_target ON jobs(uid,script_id,version) WHERE kind='UPLOAD';
                CREATE INDEX IF NOT EXISTS queue_order ON jobs(status,created_at);
                CREATE TABLE IF NOT EXISTS job_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL,
                    kind TEXT NOT NULL,actor TEXT NOT NULL,detail TEXT NOT NULL,created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_by_job ON job_events(job_id,id);
                CREATE UNIQUE INDEX IF NOT EXISTS once_per_author_comment ON jobs(uid,parent_id) WHERE kind='FIRST_COMMENT';
                CREATE UNIQUE INDEX IF NOT EXISTS once_per_comment_work ON jobs(uid,work_id) WHERE kind='FIRST_COMMENT' AND work_id<>'';
                CREATE TABLE IF NOT EXISTS settings (uid TEXT PRIMARY KEY,first_comment TEXT NOT NULL);
            ''')
        Path(self.path).chmod(0o600)
        _SCHEMA_READY.add(self.path)

    def connect(self, write: bool = False):
        return sqlite_util.connect(self.path, write=write)

    @staticmethod
    def decode(row) -> dict | None:
        if row is None:
            return None
        value = dict(row)
        for field in ('payload', 'result'):
            value[field] = json.loads(value[field])
        return value

    def get(self, key: str) -> dict | None:
        with self.connect() as conn:
            return self.decode(conn.execute('SELECT * FROM jobs WHERE id=?', (key,)).fetchone())

    def list(self, *, uid: str | None = None, kind: str | None = None, statuses: tuple = ()) -> list[dict]:
        terms, params = [], []
        for field, value in (('uid', uid), ('kind', kind)):
            if value is not None:
                terms.append(field + '=?'); params.append(value)
        if statuses:
            terms.append('status IN (' + ','.join('?' for _ in statuses) + ')'); params.extend(statuses)
        with self.connect() as conn:
            rows = conn.execute('SELECT * FROM jobs' + (' WHERE ' + ' AND '.join(terms) if terms else '') +
                                ' ORDER BY created_at DESC', params).fetchall()
        return [self.decode(row) for row in rows]

    def create(self, kind: str, uid: str = '', payload: dict | None = None, *, render: dict | None = None, parent_id: str = '') -> dict:
        if kind not in ('LOGIN', 'VERIFY', 'UPLOAD', 'RECONCILE', 'FIRST_COMMENT', 'COMMENT_CHECK'):
            raise ValueError('Unsupported job kind')
        render = render or {}; key = uuid.uuid4().hex; now = time.time()
        with self.connect(write=True) as conn:
            conn.execute('INSERT OR IGNORE INTO jobs (id,kind,uid,status,payload,parent_id,script_id,version,render_id,heartbeat,created_at,updated_at) '
                         'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', (key, kind, uid, 'WAITING' if kind == 'FIRST_COMMENT' else 'QUEUED', json.dumps(payload or {}, ensure_ascii=False), parent_id,
                         render.get('script_id', ''), render.get('version', 0), render.get('id', ''), now, now, now))
            row = conn.execute('SELECT * FROM jobs WHERE id=?', (key,)).fetchone()
            if row is None and kind == 'UPLOAD':
                row = conn.execute("SELECT * FROM jobs WHERE kind='UPLOAD' AND uid=? AND script_id=? AND version=?",
                                   (uid, render['script_id'], render['version'])).fetchone()
            if row is None and kind == 'FIRST_COMMENT':
                row = conn.execute("SELECT * FROM jobs WHERE kind='FIRST_COMMENT' AND uid=? AND parent_id=?", (uid, parent_id)).fetchone()
        return self.decode(row)

    def update(self, key: str, *, expected: str | None = None, **fields) -> bool:
        if not fields or set(fields) - {'status','phase','payload','result','error','work_id','heartbeat'}:
            raise ValueError('Unsupported queue field')
        fields = {**fields, 'updated_at': time.time()}
        for name in ('payload', 'result'):
            if name in fields:
                fields[name] = json.dumps(fields[name], ensure_ascii=False)
        with self.connect(write=True) as conn:
            return bool(conn.execute('UPDATE jobs SET ' + ','.join(k + '=?' for k in fields) + ' WHERE id=?' +
                                     (' AND status=?' if expected else ''), (*fields.values(), key, *((expected,) if expected else ()))).rowcount)

    def claim(self, *, prefer_checks: bool = False) -> dict | None:
        with self.connect(write=True) as conn:
            if conn.execute("SELECT 1 FROM jobs WHERE status='RUNNING'").fetchone():
                return None
            order = "CASE WHEN kind='RECONCILE' THEN 0 ELSE 1 END," if prefer_checks else ''
            row = conn.execute("SELECT id FROM jobs WHERE status='QUEUED' ORDER BY " + order + "created_at LIMIT 1").fetchone()
            if not row:
                return None
            now = time.time()
            conn.execute("UPDATE jobs SET status='RUNNING',phase='准备',heartbeat=?,updated_at=? WHERE id=?", (now,now,row['id']))
        return self.get(row['id'])

    def record_duration(self, key: str, duration: float) -> bool:
        """Merge local metadata into the original pending upload, never reset its receipt."""
        from core.social.verification import seconds
        if seconds(duration) is None:
            return False
        with self.connect(write=True) as conn:
            row = self.decode(conn.execute('SELECT * FROM jobs WHERE id=?', (key,)).fetchone())
            if (not row or row['kind'] != 'UPLOAD' or row['status'] not in ('SUBMITTED', 'UNKNOWN')
                    or seconds(row['result'].get('duration_seconds')) is not None):
                return False
            result = {**row['result'], 'duration_seconds': duration,
                      'duration_source': 'existing_local_movie'}
            if result.get('verification'):
                result['verification'] = {**result['verification'], 'exhausted': False,
                                          'next_check_at': time.time(),
                                          'reason': '原视频时长已补齐，等待核对原投稿'}
            conn.execute('INSERT INTO job_events(job_id,kind,actor,detail,created_at) VALUES (?,?,?,?,?)',
                         (key, 'metadata_repaired', 'reconciliation',
                          json.dumps({'field': 'duration_seconds', 'value': duration}), time.time()))
            conn.execute('UPDATE jobs SET result=?,updated_at=? WHERE id=?',
                         (json.dumps(result, ensure_ascii=False), time.time(), key))
        return True

    def confirm_absent(self, key: str, uid: str, actor: str, expected_updated_at: float) -> bool:
        """Record a human absence decision without submitting or discarding the previous attempt."""
        with self.connect(write=True) as conn:
            row = self.decode(conn.execute('SELECT * FROM jobs WHERE id=?', (key,)).fetchone())
            if (not row or row['kind'] != 'UPLOAD' or row['uid'] != uid or row['status'] != 'UNKNOWN'
                    or row['work_id'] or row['result'].get('accepted') is True
                    or row['updated_at'] != expected_updated_at or not actor.strip()):
                return False
            now = time.time()
            conn.execute('INSERT INTO job_events(job_id,kind,actor,detail,created_at) VALUES (?,?,?,?,?)',
                         (key, 'confirmed_absent', actor, json.dumps(row, ensure_ascii=False), now))
            result = {**row['result'], 'confirmed_not_submitted': True,
                      'manual_confirmation': {'actor': actor, 'confirmed_at': now}}
            conn.execute("UPDATE jobs SET status='FAILED',phase=?,error='',result=?,updated_at=? WHERE id=?",
                         ('人工确认未发布，可手动重试', json.dumps(result, ensure_ascii=False), now, key))
        return True

    def repair_upload_preparation(self, key: str, uid: str, expected_updated_at: float) -> bool:
        """Repair known legacy Kuaishou preparation failures without uploading or resetting history."""
        if self.platform != 'kuaishou':
            return False
        with self.connect(write=True) as conn:
            row = self.decode(conn.execute('SELECT * FROM jobs WHERE id=?', (key,)).fetchone())
            if (not row or row['kind'] != 'UPLOAD' or row['uid'] != uid or row['status'] != 'UNKNOWN'
                    or row['updated_at'] != expected_updated_at or row['work_id']):
                return False
            result = row['result']
            failure = result.get('failure') or {}
            # These known adapter branches stop before a publish click.
            # A missing work-list match alone
            # never establishes that a previous submission is safe to retry.
            # The account check runs when the browser context is created, before any upload page or
            # publish route exists, so even a timeout there precedes every provider mutation.
            account_check = failure.get('stage') == '核对账号' and failure.get('error_type') == 'TimeoutError'
            known_preparation = (failure.get('stage') == '选择视频上传入口'
                                 or failure.get('stage') == '平台上传流程' and
                                 row['error'].startswith('快手视频素材上传超时，未点击发布。')
                                 or account_check)
            if (result.get('invocation_started') is not True
                    or not known_preparation
                    or failure.get('error_type') not in ('AdapterError', 'UploadPreparationError') and not account_check
                    or any(result.get(field) for field in
                           ('submission_started', 'submission_started_at', 'submission_response', 'accepted', 'work_id', 'url'))):
                return False
            now = time.time()
            conn.execute('INSERT INTO job_events(job_id,kind,actor,detail,created_at) VALUES (?,?,?,?,?)',
                         (key, 'preparation_failure_repaired', 'upload_preparation', json.dumps(row, ensure_ascii=False), now))
            result = {**result, 'confirmed_not_submitted': True, 'preparation_failed': True}
            conn.execute("UPDATE jobs SET status='FAILED',phase=?,result=?,updated_at=? WHERE id=?",
                         ('上传准备失败，尚未投稿', json.dumps(result, ensure_ascii=False), now, key))
        return True

    def requeue_upload(self, key: str, uid: str, payload: dict) -> bool:
        """Save the previous attempt before resetting the single-submit guard for a manual retry."""
        with self.connect(write=True) as conn:
            row = self.decode(conn.execute('SELECT * FROM jobs WHERE id=?', (key,)).fetchone())
            if (not row or row['kind'] != 'UPLOAD' or row['uid'] != uid or row['work_id']
                    or row['status'] not in ('FAILED', 'CANCELLED')
                    or not row['result'].get('confirmed_not_submitted')):
                return False
            now = time.time()
            conn.execute('INSERT INTO job_events(job_id,kind,actor,detail,created_at) VALUES (?,?,?,?,?)',
                         (key, 'retry_requested', '', json.dumps(row, ensure_ascii=False), now))
            conn.execute("UPDATE jobs SET status='QUEUED',phase='等待手动重试投稿',result='{}',error='',payload=?,updated_at=? WHERE id=?",
                         (json.dumps(payload, ensure_ascii=False), now, key))
        return True

    def recover(self) -> None:
        for row in self.list(statuses=('RUNNING',)):
            if time.time() - row['heartbeat'] < 90:
                continue
            if row['kind'] == 'FIRST_COMMENT':
                self.recover_comment(row)
                continue
            started = row['result'].get('invocation_started') or row['result'].get('submission_started')
            status = ('SUBMITTED' if row['work_id'] or accepted_without_work_id(self.platform, row['result'])
                      else 'UNKNOWN' if started else 'FAILED') if row['kind'] == 'UPLOAD' else 'FAILED'
            self.update(row['id'], expected='RUNNING', status=status, phase='执行中断，已保留记录',
                        result={**row['result'], 'confirmed_not_submitted': status == 'FAILED' and not started and not row['work_id']},
                        error='请先到创作中心核对；结果不明时不会重投。' if status == 'UNKNOWN' else '可继续核对连接或已有作品。')

    def recover_comment(self, row: dict) -> None:
        accepted = bool(row['result'].get('comment_id'))
        started = bool(row['result'].get('submission_started'))
        self.update(row['id'], expected='RUNNING', status='SENT' if accepted else 'UNKNOWN' if started else 'FAILED',
                    phase='已保留评论结果，不自动重发',
                    result={**row['result'], 'confirmed_not_submitted': not started and not accepted},
                    error='' if accepted else '评论结果待核对，不会自动重发。' if started else '评论提交前中断，可手动重试。')

    def comments(self, uid: str) -> dict[str, dict]:
        return {r['parent_id']: r for r in self.list(uid=uid, kind='FIRST_COMMENT')}

    def comment_settings(self, uid: str) -> dict:
        with self.connect() as conn:
            row = conn.execute('SELECT first_comment FROM settings WHERE uid=?', (uid,)).fetchone()
        return json.loads(row[0]) if row else {'enabled': False, 'text': '', 'pin': False}

    def save_comment_settings(self, uid: str, value: dict) -> dict:
        with self.connect(write=True) as conn:
            conn.execute('INSERT INTO settings VALUES (?,?) ON CONFLICT(uid) DO UPDATE SET first_comment=excluded.first_comment',
                         (uid, json.dumps(value, ensure_ascii=False)))
        return value

    def activate_comment(self, key: str, uid: str, work_id: str) -> bool:
        with self.connect(write=True) as conn:
            return bool(conn.execute(
                "UPDATE jobs SET status='QUEUED',work_id=?,phase=?,updated_at=? "
                "WHERE id=? AND uid=? AND kind='FIRST_COMMENT' AND status='WAITING' "
                "AND NOT EXISTS (SELECT 1 FROM jobs other WHERE other.kind='FIRST_COMMENT' "
                "AND other.uid=? AND other.work_id=? AND other.id<>?)",
                (work_id, '视频已公开，等待发送首条评论', time.time(), key, uid, uid, work_id, key)).rowcount)

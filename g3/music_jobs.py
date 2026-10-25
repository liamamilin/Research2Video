"""Durable music jobs and cancellation, independent of existing video job records."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import sqlite3
from uuid import UUID, uuid4

from g2.music_engine import GenerationError, digest, resolve


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MusicJobs:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.root = self.data_dir / 'music-jobs'
        self.db = self.data_dir / 'music-jobs.sqlite3'

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def initialize(self) -> None:
        if self.root.is_symlink() or self.db.is_symlink():
            raise GenerationError('MUSIC_WORKSPACE_INVALID', '音乐任务目录不安全')
        self.root.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS music_jobs (
                id TEXT PRIMARY KEY, status TEXT NOT NULL, stage TEXT NOT NULL,
                request_json TEXT NOT NULL, resolved_json TEXT NOT NULL,
                request_sha256 TEXT NOT NULL, idempotency_key TEXT UNIQUE,
                parent_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                error_code TEXT, error_message TEXT, asset_id TEXT, runner_pid INTEGER,
                cancel_requested INTEGER NOT NULL DEFAULT 0)''')

    def create(self, body: dict, parent_id: str | None = None) -> dict:
        body = dict(body); key = body.pop('idempotency_key', None)
        checksum = digest(body)
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if key:
                existing = conn.execute('SELECT * FROM music_jobs WHERE idempotency_key=?', (key,)).fetchone()
                if existing:
                    if existing['request_sha256'] != checksum:
                        raise GenerationError('MUSIC_SUBMISSION_CONFLICT', '同一提交编号的音乐设置不同，请重新提交')
                    return self.view(existing)
            identity = str(uuid4())
            saved = resolve(body, body.get('seed') if body.get('seed') is not None else secrets.randbelow(2**31))
            conn.execute('''INSERT INTO music_jobs (id,status,stage,request_json,resolved_json,
                request_sha256,idempotency_key,parent_id,created_at,updated_at)
                VALUES (?,'QUEUED','QUEUED',?,?,?,?,?,?,?)''',
                (identity,json.dumps(body,ensure_ascii=False),json.dumps(saved,ensure_ascii=False),checksum,key,parent_id,now(),now()))
            conn.commit()
        return self.get(identity)

    @staticmethod
    def view(row) -> dict:
        result = {key: row[key] for key in ['id','status','stage','parent_id','created_at','updated_at','error_code','error_message','asset_id']}
        result.update(request=json.loads(row['request_json']), settings=json.loads(row['resolved_json']),
                      cancel_requested=bool(row['cancel_requested']))
        return result

    def get(self, identity: str) -> dict:
        try:
            UUID(identity)
        except ValueError:
            raise GenerationError('MUSIC_JOB_NOT_FOUND', '音乐任务不存在') from None
        with self.connect() as conn:
            row = conn.execute('SELECT * FROM music_jobs WHERE id=?',(identity,)).fetchone()
        if not row:
            raise GenerationError('MUSIC_JOB_NOT_FOUND', '音乐任务不存在')
        return self.view(row)

    def listing(self, limit: int = 20) -> list[dict]:
        with self.connect() as conn:
            return [self.view(row) for row in conn.execute('SELECT * FROM music_jobs ORDER BY created_at DESC LIMIT ?', (limit,))]

    def claim(self) -> dict | None:
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute("SELECT * FROM music_jobs WHERE status='QUEUED' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                conn.commit(); return None
            conn.execute("UPDATE music_jobs SET status='RUNNING',stage='VERIFYING',updated_at=? WHERE id=?",(now(),row['id']))
            conn.commit()
        return self.get(row['id'])

    def update(self, identity: str, **values) -> None:
        allowed = {'status','stage','error_code','error_message','asset_id','runner_pid'}
        if not values or set(values)-allowed:
            raise ValueError('Unsupported music job update')
        with self.connect() as conn:
            conn.execute('UPDATE music_jobs SET '+','.join(key+'=?' for key in values)+',updated_at=? WHERE id=?',
                         [*values.values(),now(),identity])

    def cancel(self, identity: str) -> dict:
        self.get(identity)
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            current = conn.execute('SELECT status,stage FROM music_jobs WHERE id=?',(identity,)).fetchone()
            status = current['status']
            if status not in {'QUEUED','RUNNING'}:
                raise GenerationError('MUSIC_JOB_STATE', '只有等待或运行中的音乐任务可以取消')
            if current['stage']=='CHECKING':
                raise GenerationError('MUSIC_JOB_STATE', '音乐已生成，正在校验入库，请等待完成')
            conn.execute("UPDATE music_jobs SET cancel_requested=1,status=?,stage=?,updated_at=? WHERE id=?",
                         ('CANCELLED' if status=='QUEUED' else 'RUNNING','CANCELLED' if status=='QUEUED' else 'CANCELLING',now(),identity))
            conn.commit()
        return self.get(identity)

    def start_publish(self, identity: str) -> bool:
        """Serialize the cancellation decision with the final asset commit boundary."""
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute('SELECT cancel_requested FROM music_jobs WHERE id=?',(identity,)).fetchone()
            if row['cancel_requested']:
                conn.execute("UPDATE music_jobs SET status='CANCELLED',stage='CANCELLED',updated_at=? WHERE id=?",(now(),identity))
                conn.commit();return False
            conn.execute("UPDATE music_jobs SET stage='CHECKING',updated_at=? WHERE id=?",(now(),identity))
            conn.commit();return True

    def running(self) -> list[dict]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM music_jobs WHERE status='RUNNING'")]

    def work(self, identity: str) -> Path:
        if str(UUID(identity)) != identity:
            raise GenerationError('MUSIC_WORKSPACE_INVALID', '音乐任务编号无效')
        path = self.root / identity
        if path.is_symlink():
            raise GenerationError('MUSIC_WORKSPACE_INVALID', '音乐任务目录不安全')
        return path

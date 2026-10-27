"""A short handover gate for the managed restart; historical receipts remain intact."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import fcntl
import json
from pathlib import Path
import sqlite3
import time


class MaintenanceSuperseded(RuntimeError):
    def __init__(self, runtime: dict):
        super().__init__('后台实例已由另一操作更新，本次维护等待结束，未重复重启。')
        self.runtime = runtime


def busy_work(state: Path) -> list[dict]:
    """Count executing or queued work, not submitted receipts or waiting comments."""
    checks = [
        ('video.db', 'scripts', 'status', ('RESEARCHING', 'WRITING')),
        ('video.db', 'renders', 'status', ('SUBMITTING', 'QUEUED', 'RUNNING', 'DOWNLOADING')),
        ('video.db', 'rechecks', 'status', ('RUNNING',)),
        ('video.db', 'autopilot_runs', 'status', ('QUEUED', 'RUNNING')),
        ('assistant.db', 'requests', 'status', ('running',)),
        ('assistant.db', 'application_batches', 'status', ('queued', 'running')),
        ('schedules.db', 'schedule_runs', 'status', ('queued', 'starting', 'running', 'retry')),
        ('platforms.db', 'uploads', 'status', ('QUEUED', 'UPLOADING', 'RUNNING', 'PUBLISHING', 'PROCESSING')),
        ('douyin.db', 'jobs', 'status', ('QUEUED', 'RUNNING')),
    ]
    checks += [(str(path.relative_to(state)), 'jobs', 'status', ('QUEUED', 'RUNNING'))
               for path in (state / 'platforms').glob('*/queue.db')]
    busy = []
    for file, table, column, statuses in checks:
        path = state / file
        if not path.is_file():
            continue
        with sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=5) as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                continue
            count = conn.execute(f'SELECT count(*) FROM {table} WHERE {column} IN (' +
                                 ','.join('?' for _ in statuses) + ')', statuses).fetchone()[0]
        if count:
            busy.append({'database': file, 'table': table, 'count': count})
    return busy


@contextmanager
def restart_gate(state: Path, *, timeout: float = 3600, expected_runtime: dict | None = None):
    """Wait for current workers, then hold their existing locks across the handover.

    The dispatch locks make the idle check indivisible from claiming worker locks.
    They are released before stopping the old web process; its dispatch loops see
    the held worker locks and return without blocking graceful shutdown. Pending
    receipts and opt-in comment records are never changed or discarded.
    """
    end = time.monotonic() + timeout
    roots = sorted(p.parent for p in (state / 'platforms').glob('*/worker.lock'))
    while time.monotonic() < end:
        if expected_runtime is not None:
            current = json.loads((state / 'app_runtime.json').read_text())
            if (current.get('pid'), current.get('instance_id')) != (expected_runtime.get('pid'), expected_runtime.get('instance_id')):
                raise MaintenanceSuperseded(current)
        workers, dispatch = ExitStack(), ExitStack()
        acquired = False
        try:
            for root in roots:
                lock = dispatch.enter_context((root / 'dispatch.lock').open('a'))
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for root in roots:
                lock = workers.enter_context((root / 'worker.lock').open('a'))
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = not busy_work(state)
        except BlockingIOError:
            pass
        except BaseException:
            workers.close()
            raise
        finally:
            dispatch.close()
        if acquired:
            try:
                yield
            finally:
                workers.close()
            return
        workers.close()
        time.sleep(0.5)
    raise RuntimeError('现有研究、制作或发布工作尚未结束，未发送重启信号；原任务保持。')

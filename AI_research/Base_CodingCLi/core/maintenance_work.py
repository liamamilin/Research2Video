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


def busy_work(state: Path, *, include_waiting_video: bool = True) -> list[dict]:
    """Count executing or ready work that must finish before a restart."""
    checks = [
        ('chains.db', 'runs', "json_extract(data,'$.status')", ('RUNNING', 'CANCELLING')),
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
        if not include_waiting_video and file == 'video.db' and table == 'autopilot_runs':
            continue  # Only while autopilot-tick is held: durable queue, not an executing worker.
        path = state / file
        if not path.is_file():
            continue
        with sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=5) as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                continue
            condition = f"{column} IN ({','.join('?' for _ in statuses)})"
            if not include_waiting_video and file == 'chains.db':
                # The observer's VIDEO intent is durable; its actual writers and
                # renders are still checked below. Research/cancellation must finish.
                condition += """ AND NOT (json_extract(data,'$.status')='RUNNING'
                    AND json_extract(data,'$.stage') IS 'VIDEO'
                    AND json_extract(data,'$.batch_id') IS NOT NULL)"""
            if file == 'platforms.db':
                # Bilibili persists deferred automatic uploads for its next tick.
                # The worker lock still protects them once a tick starts processing.
                condition += """ AND NOT (status = 'QUEUED' AND CASE WHEN json_valid(payload)
                    THEN json_type(payload, '$.automatic') IS 'true'
                         AND json_type(payload, '$.held') IS 'true'
                         AND json_extract(payload, '$.dtime') IS NULL
                         AND json_extract(payload, '$.released_at') IS NULL
                    ELSE 0 END)"""
            count = conn.execute(f'SELECT count(*) FROM {table} WHERE {condition}', statuses).fetchone()[0]
        if count:
            busy.append({'database': file, 'table': table, 'count': count})
    return busy


def _web_research_active(state: Path, pid: int) -> bool:
    # The detached helper cannot read the web process's in-memory task
    # registry. ResearchEngine holds these locks until its run is finished.
    for path in (state / 'locks').glob('*.lock'):
        try:
            record = json.loads(path.read_text())
        except (FileNotFoundError, ValueError):
            continue
        if isinstance(record, dict) and record.get('pid') == pid:
            return True
    return False


@contextmanager
def _restart_gate(state: Path, *, timeout: float = 3600, expected_runtime: dict | None = None, include_waiting_video: bool = True):
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
            web_busy = expected_runtime is not None and _web_research_active(state, expected_runtime['pid'])
            acquired = not web_busy and not busy_work(state, include_waiting_video=include_waiting_video)
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


@contextmanager
def restart_gate(state: Path, *, timeout: float = 3600, expected_runtime: dict | None = None,
                 handoff_waiting_video: bool = False):
    """Optionally retain prepared video queue entries across a graceful handover.

    Hold the same admission lock as the native autopilot. Existing writing and
    render workers must still finish; no new topic picker, writer or render is
    dispatched. Only persisted ITEMS batches may be deferred. No queue rows,
    production configuration or schedule settings are modified.
    """
    if not handoff_waiting_video:
        with _restart_gate(state, timeout=timeout, expected_runtime=expected_runtime):
            yield
        return
    from core.fileio import file_lock

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        admission = ExitStack()
        try:
            admission.enter_context(file_lock(str(state / 'autopilot-tick'), timeout=0))
        except TimeoutError:
            admission.close()
            time.sleep(.5)
            continue
        with admission:
            path = state / 'video.db'
            ready = True
            if path.exists():
                with sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=5) as db:
                    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='autopilot_runs'").fetchone():
                        ready = not db.execute("SELECT 1 FROM autopilot_runs WHERE status IN ('QUEUED','RUNNING') AND (stage IS NULL OR stage != 'ITEMS') LIMIT 1").fetchone()
            if ready:
                with _restart_gate(state, timeout=max(.01, end-time.monotonic()),
                                   expected_runtime=expected_runtime, include_waiting_video=False):
                    yield
                return
        time.sleep(.5)
    raise RuntimeError('视频规划仍未落库，未中断原规划或修改排队记录。')

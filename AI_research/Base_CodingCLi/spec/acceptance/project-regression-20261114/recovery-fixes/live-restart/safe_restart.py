"""One-off operator restart: wait for idle, preserve queues, use SIGTERM only."""
from contextlib import ExitStack
from datetime import datetime
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path('/Users/milin/2026/软件开发/视频workflow')
RESEARCH = ROOT / 'AI_research/Base_CodingCLi'
STATE = RESEARCH / 'state'
VIDEO = ROOT / 'autovideo'
EVIDENCE = RESEARCH / 'spec/acceptance/project-regression-20261114/recovery-fixes/live-restart'
EVIDENCE.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(RESEARCH))
sys.path.insert(0, str(VIDEO))
from core.fileio import file_lock, atomic_write
from core.maintenance_work import busy_work, restart_gate
from core.processes import process_exited
from scripts.service_maintenance import _perform_idle
from g3.launcher.launch import start as start_video


def read(path):
    return json.loads(path.read_text())


def video_snapshot():
    result = {}
    for filename, table, key in [('jobs.sqlite3', 'jobs', 'job_id'), ('music-jobs.sqlite3', 'music_jobs', 'id')]:
        path = VIDEO / 'g3/data' / filename
        if path.exists():
            with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as db:
                result[table] = db.execute(f'SELECT {key},status FROM {table} ORDER BY {key}').fetchall()
    return result


def check_manual():
    activity = read(STATE / 'app_activity.json')
    if activity.get('running'):
        return False
    for path in (STATE / 'locks').glob('*.lock'):
        if path.name.endswith('.lock.lock') or not path.stat().st_size:
            continue
        payload = read(path)
        if payload.get('pid') and not process_exited(payload['pid']):
            return False
    return True


def restart_video():
    pid = 15545
    command = subprocess.check_output(['ps', '-p', str(pid), '-o', 'command='], text=True).strip()
    if 'uvicorn g3.app:app --host 127.0.0.1 --port 8011' not in command:
        raise RuntimeError('Video process identity changed; no signal sent')
    before = video_snapshot()
    with ExitStack() as locks:
        # Prevent both the worker and API from claiming/submitting across the idle check.
        for filename, table in [('jobs.sqlite3', 'jobs'), ('music-jobs.sqlite3', 'music_jobs')]:
            path = VIDEO / 'g3/data' / filename
            if not path.exists():
                continue
            db = sqlite3.connect(path, timeout=5, isolation_level=None)
            locks.callback(db.close)
            locks.callback(db.rollback)
            db.execute('BEGIN IMMEDIATE')
            if db.execute(f"SELECT count(*) FROM {table} WHERE status IN ('QUEUED','RUNNING')").fetchone()[0]:
                raise RuntimeError('Video/music work became active; no signal sent')
        print('Video queue and music queue idle; sending SIGTERM to verified PID', pid, flush=True)
        os.kill(pid, signal.SIGTERM)
        # Let uvicorn close admission before releasing the DB claim locks.
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            with socket.socket() as sock:
                sock.settimeout(.2)
                if sock.connect_ex(('127.0.0.1', 8011)) != 0:
                    break
            time.sleep(.1)
        else:
            raise RuntimeError('Video listener did not close; no force kill attempted')
    deadline = time.monotonic() + 45
    while not process_exited(pid) and time.monotonic() < deadline:
        time.sleep(.25)
    if not process_exited(pid):
        raise RuntimeError('Video process did not exit; no force kill attempted')
    config = read(Path('/Users/milin/Applications/AutoVideo.app/Contents/Resources/launcher.json'))
    result = start_video(config)
    after = video_snapshot()
    result.update(old_pid=pid, rows_unchanged=before == after,
                  before_counts={key: len(rows) for key, rows in before.items()})
    assert result['action'] == 'started' and result['rows_unchanged']
    (EVIDENCE / 'video-restart.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print('Video restarted', result['pid'], 'all existing job states preserved', flush=True)


runtime = read(STATE / 'app_runtime.json')
key = uuid4().hex
spec_path = STATE / 'maintenance' / f'{key}.json'
spec = {'id': key, 'action': 'restart', 'user_id': 'local-operator', 'status': 'waiting',
        'pid': runtime['pid'], 'created': time.time(), 'runtime': runtime,
        'state_dir': str(STATE), 'active_runs': 'wait', 'origin': 'user-requested Codex safe restart'}
deadline = time.monotonic() + 3600
last_busy = None
while time.monotonic() < deadline:
    busy = busy_work(STATE)
    video_busy = [row for rows in video_snapshot().values() for row in rows if row[1] in ('RUNNING', 'QUEUED')]
    if busy or video_busy or not check_manual():
        status = (busy, video_busy)
        if status != last_busy:
            print('Waiting for existing work', status, flush=True)
            last_busy = status
        time.sleep(1)
        continue
    with ExitStack() as stack:
        try:
            stack.enter_context(file_lock(str(STATE / 'autopilot-tick'), timeout=1))
            stack.enter_context(file_lock(str(STATE / 'assistant-maintenance-admission'), timeout=1))
            stack.enter_context(restart_gate(STATE, timeout=2, expected_runtime=runtime))
        except (TimeoutError, RuntimeError) as exc:
            print('Waiting for safe admission window:', type(exc).__name__, flush=True)
            continue
        if busy_work(STATE) or not check_manual():
            continue
        spec['status'] = 'applying'
        atomic_write(str(spec_path), json.dumps(spec, ensure_ascii=False))
        atomic_write(str(STATE / 'assistant_maintenance.json'), json.dumps(spec, ensure_ascii=False))
        (EVIDENCE / 'before.json').write_text(json.dumps({'research_runtime': runtime, 'busy_work': [],
            'manual_activity': read(STATE / 'app_activity.json'), 'checked_at': datetime.now().astimezone().isoformat()}, ensure_ascii=False, indent=2))
        try:
            restart_video()
            print('Research idle; invoking standard maintenance helper', runtime['pid'], flush=True)
            _perform_idle(spec_path)
            result = read(spec_path)
            (EVIDENCE / 'research-restart.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
            if result['status'] != 'completed':
                raise RuntimeError(result.get('error', 'Research restart not completed'))
            print('Research restarted', result['actual']['pid'], flush=True)
        except Exception as exc:
            spec.update(status='failed', error=str(exc), updated=time.time())
            atomic_write(str(spec_path), json.dumps(spec, ensure_ascii=False))
            atomic_write(str(STATE / 'assistant_maintenance.json'), json.dumps(spec, ensure_ascii=False))
            raise
        break
else:
    raise RuntimeError('Still busy after one hour; no forced restart')
print('DONE', flush=True)

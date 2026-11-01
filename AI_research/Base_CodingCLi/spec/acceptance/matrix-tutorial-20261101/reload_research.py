"""Load verified code with the existing idle-only managed restart helper."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def records(state: Path) -> dict:
    values = {}
    with sqlite3.connect(f'file:{state / "video.db"}?mode=ro', uri=True) as db:
        for name, fields in [('scripts', 'id,status,current_version,approved_version'),
                             ('renders', 'id,status,version,source_sha256')]:
            rows = db.execute(f'SELECT {fields} FROM {name} ORDER BY id').fetchall()
            values[name] = {row[0]: row[1:] for row in rows}
    return values


def main(out: Path) -> None:
    from core.maintenance_work import busy_work
    from core.fileio import atomic_write, file_lock
    from scripts.service_maintenance import perform
    state = (ROOT / 'state').resolve()
    runtime = json.loads((state / 'app_runtime.json').read_text())
    assert not busy_work(state), 'existing work must finish before loading changes'
    command = subprocess.check_output(['ps', '-p', str(runtime['pid']), '-o', 'command='], text=True)
    assert str(ROOT / 'run_web.py') in command and ' serve' in command
    previous = state / 'assistant_maintenance.json'
    if previous.exists():
        old = json.loads(previous.read_text())
        assert old.get('status') not in {'waiting', 'applying', 'starting'}, 'another maintenance owns the service'
    baseline = records(state)
    key = uuid4().hex
    directory = state / 'maintenance'
    directory.mkdir(exist_ok=True)
    path = directory / (key + '.json')
    spec = {'id': key, 'action': 'restart', 'status': 'waiting', 'pid': runtime['pid'],
            'runtime': runtime, 'state_dir': str(state), 'active_runs': 'wait', 'created': time.time(),
            'reason': 'Load user-requested matrix tutorial integration after regressions and real media acceptance.'}
    with file_lock(str(state / 'assistant-maintenance-admission')):
        assert not busy_work(state), 'new work arrived before the maintenance handover'
        assert json.loads((state / 'app_runtime.json').read_text())['instance_id'] == runtime['instance_id']
        encoded = json.dumps(spec, ensure_ascii=False, indent=2) + '\n'
        atomic_write(str(path), encoded)
        atomic_write(str(previous), encoded)
    perform(path)
    result = json.loads(path.read_text())
    assert result['status'] == 'completed', result
    current = records(state)
    assert all(current[table].get(key) == row for table, rows in baseline.items() for key, row in rows.items())
    files = ['web/services/video_render.py', 'core/video/visual_materials.py', 'config/content_columns/ai_opensource.yaml',
             'core/user_materials.py', 'core/video/writer.py', 'core/video/episodes.py',
             'web/services/video_workflow.py', 'web/services/assistant_materials.py']
    receipt = {'status': 'PASS', 'method': 'existing service_maintenance.perform + restart_gate',
        'old_pid': runtime['pid'], 'new_pid': result['actual']['pid'], 'health': result['health'],
        'idle_before_reload': True, 'preserved_records': {table: len(rows) for table, rows in baseline.items()},
        'existing_script_and_render_records_unchanged': True,
        'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in files},
        'maintenance_receipt': str(path)}
    out.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == '__main__':
    main(Path(sys.argv[1]).resolve())

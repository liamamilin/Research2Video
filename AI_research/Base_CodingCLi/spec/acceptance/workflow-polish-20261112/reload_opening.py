"""Load the verified writer through the existing idle-only restart mechanism."""
from __future__ import annotations
from contextlib import ExitStack
import fcntl
import sqlite3
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def held_queue(state: Path) -> dict:
    """Only persisted automatic holds qualify, never an executing upload."""
    with sqlite3.connect(f'file:{state/"platforms.db"}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(r) for r in db.execute(
            "SELECT * FROM uploads WHERE status IN ('QUEUED','UPLOADING','RUNNING','PUBLISHING','PROCESSING')")]
    if not rows or any(r['status'] != 'QUEUED' or not json.loads(r['payload']).get('automatic')
                       or not json.loads(r['payload']).get('held') for r in rows):
        return {}
    return {r['id']: hashlib.sha256(json.dumps(r,sort_keys=True).encode()).hexdigest() for r in rows}


def remaining_work(state, busy, held):
    return [w for w in busy if not (held and w['database']=='platforms.db'
                                    and w['table']=='uploads' and w['count']==len(held))]


def main(out: Path):
    from core.maintenance_work import busy_work
    from core.fileio import atomic_write, file_lock
    from scripts.service_maintenance import perform, _perform_idle
    state = ROOT/'state'
    deadline = time.monotonic()+7200
    previous = None
    while time.monotonic() < deadline:
        busy = busy_work(state)
        marker = state/'assistant_maintenance.json'
        old = json.loads(marker.read_text()) if marker.exists() else {}
        maintenance_active = old.get('status') in {'waiting', 'applying', 'starting'}
        held = held_queue(state)
        now = (busy, maintenance_active)
        if now != previous:
            print(json.dumps({'waiting_for_idle': busy, 'other_maintenance': maintenance_active}), flush=True)
            previous = now
        if remaining_work(state, busy, held) or maintenance_active:
            time.sleep(10)
            continue
        runtime = json.loads((state/'app_runtime.json').read_text())
        command = subprocess.check_output(['ps','-p',str(runtime['pid']),'-o','command='], text=True)
        assert str(ROOT/'run_web.py') in command and ' serve' in command
        key = uuid.uuid4().hex
        directory = state/'maintenance';directory.mkdir(exist_ok=True)
        path = directory/(key+'.json')
        spec = {'id':key,'action':'restart','status':'waiting','pid':runtime['pid'],
                'runtime':runtime,'state_dir':str(state),'active_runs':'wait','created':time.time(),
                'reason':'Load the verified natural narrator opening fix; preserve running production work.'}
        with ExitStack() as locks:
            if held:
                # Same platform worker/dispatch locks as restart_gate. Holding them
                # across the handover keeps the stored holds from being dispatched.
                try:
                    for folder in sorted(p.parent for p in (state/'platforms').glob('*/worker.lock')):
                        for name in ('dispatch.lock', 'worker.lock'):
                            lock = locks.enter_context((folder/name).open('a'))
                            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    time.sleep(1)
                    continue
            with file_lock(str(state/'assistant-maintenance-admission')):
                held_now = held_queue(state)
                if remaining_work(state, busy_work(state), held_now) or held_now != held:
                    continue
                current = json.loads((state/'app_runtime.json').read_text())
                if current['instance_id'] != runtime['instance_id']:
                    continue
                spec['preserved_held_uploads'] = held
                spec['handover'] = 'idle workers with persisted holds' if held else 'standard idle restart'
                encoded = json.dumps(spec,ensure_ascii=False,indent=2)+'\n'
                atomic_write(str(path),encoded);atomic_write(str(marker),encoded)
            if held:
                # A queued series hold is not an executing task. Use the same
                # managed PID/health handover after proving worker idleness,
                # without deleting, unholding, or changing publication records.
                _perform_idle(path)
                assert held_queue(state) == held, 'Held publication records changed during handover'
            else:
                perform(path)
        result = json.loads(path.read_text())
        files = ['core/video/persona.py','core/video/writer.py','config/persona.yaml']
        receipt = {'maintenance': result, 'idle_before_reload': True,
                   'source_sha256':{f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in files},
                   'maintenance_receipt':str(path)}
        out.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'status':result['status'],'old_pid':runtime['pid'],
                          'new_pid':result.get('actual',{}).get('pid'),'health':result.get('health')}),flush=True)
        assert result['status']=='completed', result
        return
    raise TimeoutError('No idle window; production work was not interrupted')

if __name__=='__main__':
    main(Path(sys.argv[1]).resolve())

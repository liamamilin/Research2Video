"""One-time rollout: preserve files, wait for idle, use the existing managed restart."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import sys
import time
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
STAGED = Path('/tmp/content-rollout-20261115')
sys.path.insert(0, str(ROOT))


def manual_busy(state):
    result = []
    for path in state.glob('*.json'):
        try:
            value = json.loads(path.read_text())
        except (ValueError, OSError):
            continue
        if isinstance(value, dict) and value.get('status') in ('running', 'starting'):
            result.append(path.name)
    from core.rounds import list_rounds
    result.extend('round:'+str(row.get('id')) for row in list_rounds(str(state)) if row.get('status') == 'running')
    return result


def main():
    from core.fileio import file_lock
    from core.maintenance_work import restart_gate, busy_work
    from web.services.yaml_io import atomic_write
    from scripts.service_maintenance import perform
    state = ROOT/'state'
    runtime = json.loads((state/'app_runtime.json').read_text())
    hashes = json.loads((HERE/'before-hashes.json').read_text())
    for relative, expected in hashes.items():
        path = ROOT/relative
        current = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        if current != expected:
            raise RuntimeError(f'Concurrent edit; not overwriting {relative}')
    key = uuid.uuid4().hex
    receipt = state/'maintenance'/(key+'.json')
    spec = {'id':key, 'action':'restart', 'active_runs':'wait', 'status':'waiting',
            'pid':runtime['pid'], 'runtime':runtime, 'state_dir':str(state),
            'user_id':'local-content-rollout', 'created':time.time()}
    marker = state/'assistant_maintenance.json'
    with file_lock(str(state/'assistant-maintenance-admission')):
        previous = json.loads(marker.read_text()) if marker.exists() else {}
        if previous.get('pid') == runtime['pid'] and previous.get('status') in ('waiting','applying','starting'):
            raise RuntimeError('Another maintenance operation is active')
        atomic_write(str(receipt), json.dumps(spec, ensure_ascii=False))
        atomic_write(str(marker), json.dumps(spec, ensure_ascii=False))
    atomic_write(str(HERE/'activation.json'), json.dumps({'receipt':str(receipt), 'old_pid':runtime['pid'],
                 'before_instance':runtime.get('instance_id'), 'busy_before':busy_work(state),
                 'manual_before':manual_busy(state)}, ensure_ascii=False, indent=2))
    try:
        deadline = time.monotonic()+3600
        while manual_busy(state):
            if time.monotonic() >= deadline:
                raise RuntimeError('Existing manual work is still running; no restart sent')
            time.sleep(0.5)
        with restart_gate(state, expected_runtime=runtime):
            # Admission marker blocks new manual work. The existing gate protects
            # queued research/video/publishing work and worker locks.
            if manual_busy(state):
                raise RuntimeError('Work appeared; no files or processes changed')
            backup = state/'acceptance-content-value-20261115/rollout-before'
            for relative, expected in hashes.items():
                path = ROOT/relative
                current = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
                if current != expected:
                    raise RuntimeError(f'Concurrent edit during idle wait: {relative}')
            for relative in hashes:
                current = ROOT/relative
                if current.exists():
                    saved = backup/relative
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(current, saved)
            written = []
            try:
                for relative in hashes:
                    atomic_write(str(ROOT/relative), (STAGED/relative).read_text())
                    written.append(relative)
            except BaseException:
                for relative in reversed(written):
                    if hashes[relative] is None:
                        (ROOT/relative).unlink()
                    else:
                        atomic_write(str(ROOT/relative), (backup/relative).read_text())
                raise
        # Rechecks current instance and every busy-work category before SIGTERM;
        # no forced termination or cancellation is used by this helper.
        perform(receipt)
        outcome = json.loads(receipt.read_text())
        atomic_write(str(HERE/'activation-result.json'), json.dumps(outcome, ensure_ascii=False, indent=2))
        if outcome['status'] != 'completed':
            raise RuntimeError('Managed restart did not complete; see activation-result.json')
        print(json.dumps({'status':outcome['status'], 'old_pid':runtime['pid'],
                          'new_pid':outcome.get('actual',{}).get('pid'), 'health':outcome.get('health')}, ensure_ascii=False))
    except BaseException as exc:
        current = json.loads(receipt.read_text())
        if current.get('status') in ('waiting','applying'):
            current.update(status='failed', error=str(exc), updated=time.time())
            atomic_write(str(receipt), json.dumps(current, ensure_ascii=False))
            if json.loads(marker.read_text()).get('id') == key:
                atomic_write(str(marker), json.dumps(current, ensure_ascii=False))
        raise


if __name__ == '__main__':
    main()

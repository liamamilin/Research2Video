"""Load adopted prompts via the project's existing managed, idle-only restart."""
from pathlib import Path
import json
import sys
import time
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))


def manual_busy(state):
    from core.rounds import list_rounds
    active = []
    for path in state.glob('*.json'):
        try:
            value = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(value, dict) and value.get('status') in ('running', 'starting'):
            active.append(path.name)
    active.extend('round:' + str(row.get('id')) for row in list_rounds(str(state))
                  if row.get('status') == 'running')
    return active


def main():
    from core.fileio import file_lock
    from core.maintenance_work import busy_work
    from web.services.yaml_io import atomic_write
    from scripts.service_maintenance import perform
    state = ROOT / 'state'
    runtime = json.loads((state / 'app_runtime.json').read_text())
    assert not busy_work(state) and not manual_busy(state), 'Work is active; no restart requested'
    key = uuid.uuid4().hex
    receipt = state / 'maintenance' / (key + '.json')
    spec = {'id': key, 'action': 'restart', 'active_runs': 'wait', 'status': 'waiting',
            'pid': runtime['pid'], 'runtime': runtime, 'state_dir': str(state),
            'user_id': 'local-natural-writing-author-input-dedup', 'created': time.time()}
    marker = state / 'assistant_maintenance.json'
    with file_lock(str(state / 'assistant-maintenance-admission')):
        previous = json.loads(marker.read_text()) if marker.exists() else {}
        assert not (previous.get('pid') == runtime['pid'] and previous.get('status') in
                    ('waiting', 'applying', 'starting')), 'Maintenance already active'
        assert not manual_busy(state), 'Manual work appeared; no restart requested'
        atomic_write(str(receipt), json.dumps(spec, ensure_ascii=False))
        atomic_write(str(marker), json.dumps(spec, ensure_ascii=False))
    try:
        # Existing helper waits for queued/active work and verifies process identity.
        perform(receipt)
        result = json.loads(receipt.read_text())
        (HERE / 'author-input-dedup-activation.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        assert result['status'] == 'completed', result.get('error')
        print(json.dumps({'status': result['status'], 'old_pid': runtime['pid'],
                          'new_pid': result['actual']['pid'], 'health': result['health']}))
    except BaseException:
        current = json.loads(receipt.read_text())
        if current.get('status') == 'waiting':
            current.update(status='failed', error='Activation interrupted before completion', updated=time.time())
            atomic_write(str(receipt), json.dumps(current, ensure_ascii=False))
            if json.loads(marker.read_text()).get('id') == key:
                atomic_write(str(marker), json.dumps(current, ensure_ascii=False))
        raise


if __name__ == '__main__':
    main()

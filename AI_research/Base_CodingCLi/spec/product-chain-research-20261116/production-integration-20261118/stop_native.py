"""Stop only this run's idle rehearsal services; retain all trial evidence."""
from pathlib import Path
import json
import os
import signal
import subprocess
import sys
import time
SOURCE = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SOURCE))
from core.maintenance_work import busy_work
from core.processes import process_exited
ROOT = SOURCE / 'state/validation/chain-production-integration-20261118/native'
assert not busy_work(ROOT / 'state'), 'Rehearsal work must finish first'
stopped = []
for name in ('dispatcher', 'web'):
    pid = int((ROOT / (name + '.pid')).read_text())
    if not process_exited(pid):
        cwd = subprocess.check_output(['lsof', '-a', '-p', str(pid), '-d', 'cwd', '-Fn'], text=True)
        assert any(Path(line[1:]).resolve() == ROOT.resolve() for line in cwd.splitlines() if line.startswith('n')), (name, pid)
        os.kill(pid, signal.SIGTERM)
        end = time.monotonic() + 45
        while not process_exited(pid) and time.monotonic() < end:
            time.sleep(.25)
        assert process_exited(pid), (name, 'did not stop gracefully')
    stopped.append({'service': name, 'pid': pid, 'gracefully_stopped': True})
result = {'status': 'PASS', 'stopped': stopped, 'trial_data_preserved': True,
          'formal_services_and_prior_renderer_untouched': True, 'at': time.time()}
(ROOT / 'evidence/service-cleanup.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
print(json.dumps(result, ensure_ascii=False, indent=2))

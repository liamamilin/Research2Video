"""Refresh only native rehearsal code/UI after its real work is complete."""
from pathlib import Path
import os
import shutil
import signal
import subprocess
import sys
import time
SOURCE=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(SOURCE))
from core.maintenance_work import busy_work
from core.processes import process_exited
ROOT=SOURCE/'state/validation/chain-production-integration-20261118/native'
assert not busy_work(ROOT/'state')
pid=int((ROOT/'web.pid').read_text())
if not process_exited(pid):
    cwd=subprocess.check_output(['lsof','-a','-p',str(pid),'-d','cwd','-Fn'],text=True)
    assert any(Path(line[1:]).resolve()==ROOT.resolve() for line in cwd.splitlines() if line.startswith('n'))
    os.kill(pid,signal.SIGTERM)
    end=time.monotonic()+45
    while not process_exited(pid) and time.monotonic()<end:time.sleep(.25)
    assert process_exited(pid),'Rehearsal did not stop gracefully'
for rel in ('web/services/chains.py','web/services/chain_runner.py','web/routes/production.py','core/maintenance_work.py','core/engine.py','scripts/service_maintenance.py'):
    shutil.copy2(SOURCE/rel,ROOT/rel)
shutil.copytree(SOURCE/'state/validation/chain-production-integration-20261118/candidate-ui-dist',ROOT/'ui/dist',dirs_exist_ok=True)
subprocess.run([sys.executable,str(Path(__file__).parent/'start_native.py')],cwd=SOURCE,check=True)

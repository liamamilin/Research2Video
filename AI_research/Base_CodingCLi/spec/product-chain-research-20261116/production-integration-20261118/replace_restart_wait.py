"""Replace only the waiting helper; never signal the application or its workers."""
from pathlib import Path
import json
import os
import shutil
import signal
import subprocess
import sys
import time
SOURCE=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(SOURCE))
from core.fileio import atomic_write,file_lock
from core.processes import process_exited
DATA=SOURCE/'state/validation/chain-production-integration-20261118'
request=json.loads((DATA/'formal-restart-request.json').read_text())
STATE=SOURCE/'state'
with file_lock(str(STATE/'assistant-maintenance-admission')):
    current=json.loads((STATE/'app_runtime.json').read_text())
    assert current['instance_id']==request['old_runtime']['instance_id']
    receipt=Path(request['spec']);old=json.loads(receipt.read_text())
    assert old['status']=='applying' and old['pid']==current['pid']
    command=subprocess.check_output(['ps','-p',str(request['helper_pid']),'-o','command='],text=True)
    assert 'service_maintenance.py' in command and str(receipt) in command
    os.kill(request['helper_pid'],signal.SIGTERM)
    end=time.monotonic()+15
    while not process_exited(request['helper_pid']) and time.monotonic()<end:time.sleep(.1)
    assert process_exited(request['helper_pid'])
    assert json.loads((STATE/'app_runtime.json').read_text())['instance_id']==current['instance_id']
    old.update(status='interrupted',updated=time.time(),error='Waiting helper superseded by admission-locked queue handover; application and workers retained')
    atomic_write(str(receipt),json.dumps(old,ensure_ascii=False))
    atomic_write(str(STATE/'assistant_maintenance.json'),json.dumps(old,ensure_ascii=False))
    shutil.copy2(DATA/'formal-restart-request.json',DATA/'formal-restart-request-initial.json')
print('Replaced waiting helper only; original application and workers retained',flush=True)
subprocess.run([sys.executable,str(Path(__file__).parent/'request_formal_restart.py')],cwd=SOURCE,check=True)

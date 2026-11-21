"""Load verified UI and enable chain management only after the managed handover succeeds."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import httpx

SOURCE=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(SOURCE))
DATA=SOURCE/'state/validation/chain-production-integration-20261118'
STATE=SOURCE/'state'
request=json.loads((DATA/'formal-restart-request.json').read_text())
receipt_path=Path(request['spec'])

def ids():
    result={}
    for filename, tables in {'video.db':['scripts','renders','autopilot_runs','autopilot_items'], 'schedules.db':['schedules','schedule_runs'], 'platforms.db':['uploads']}.items():
        with sqlite3.connect((STATE/filename).resolve().as_uri()+'?mode=ro',uri=True) as c:
            for table in tables:
                result[filename+':'+table]=sorted(str(r[0]) for r in c.execute('SELECT id FROM '+table))
    return result

before=ids()
(DATA/'formal-before-ids.json').write_text(json.dumps(before,indent=2))
last=None
while True:
    receipt=json.loads(receipt_path.read_text())
    if receipt['status']!=last:
        print('managed restart',receipt['status'],flush=True);last=receipt['status']
    if receipt['status']=='completed':break
    if receipt['status'] in ('failed','interrupted'):raise RuntimeError(receipt)
    time.sleep(5)
current=json.loads((STATE/'app_runtime.json').read_text())
assert current['pid']!=request['old_runtime']['pid']
with httpx.Client(timeout=10,trust_env=False) as c:
    health=c.get('http://127.0.0.1:8766/api/health');health.raise_for_status()
    assert health.json()['instance_id']==current['instance_id']
    assert c.get('http://127.0.0.1:8766/api/chains').status_code==401
for row in json.loads((DATA/'source-release.json').read_text()):
    assert hashlib.sha256((SOURCE/row['path']).read_bytes()).hexdigest()==row['after_sha256'],row['path']
# Keep every old hashed asset for already-open tabs and entry-point rollback.
baseline=DATA/'formal-ui-before';baseline.mkdir(exist_ok=True)
shutil.copy2(SOURCE/'ui/dist/index.html',baseline/'index.html')
candidate=DATA/'candidate-ui-dist'
for path in candidate.rglob('*'):
    if not path.is_file() or path.name=='index.html':continue
    target=SOURCE/'ui/dist'/path.relative_to(candidate);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
from core.fileio import atomic_write
atomic_write(str(SOURCE/'ui/dist/index.html'),(candidate/'index.html').read_text())
# Existing roles and authentication remain the gate for every operation.
from web.services import chains
access=SOURCE/'config/chain_access.yaml'
assert not access.exists(),'An operator already configured rollout access; preserve it and review'
with chains.transaction():
    atomic_write(str(access),'mode: enabled\nallow_create: true\nwritable_chains: ["*"]\n')
print('formal UI and chain entry enabled',flush=True)
# Reload only the existing launchd dispatcher, retaining its plans, leases and workers.
from core.schedule_store import ScheduleStore
schedules=ScheduleStore(str(STATE),config_dir=str(SOURCE/'config'),jobs_dir=str(SOURCE/'jobs'))
old_heartbeat=schedules.health();pid=old_heartbeat.get('pid')
if old_heartbeat.get('chain_tracking_version')!=1:
    assert old_heartbeat.get('runtime',{}).get('workspace')==str(SOURCE)
    command=subprocess.check_output(['ps','-p',str(pid),'-o','command='],text=True)
    assert 'dispatch_schedules.py' in command and str(SOURCE) in command
    os.kill(pid,signal.SIGTERM)
    end=time.monotonic()+60
    while time.monotonic()<end:
        heartbeat=schedules.health()
        if heartbeat.get('online') and heartbeat.get('chain_tracking_version')==1:break
        time.sleep(1)
    else:raise RuntimeError('Dispatcher did not report the expected loaded version')
else:heartbeat=old_heartbeat
after=ids();assert all(set(values)<=set(after[key]) for key,values in before.items())
result={'status':'PASS','runtime':current,'previous_pid':request['old_runtime']['pid'],
        'dispatcher':heartbeat,'old_ids_preserved':{key:len(v) for key,v in before.items()},
        'current_counts':{key:len(v) for key,v in after.items()},
        'access':chains.chain_access.snapshot(),'ui_sha256':hashlib.sha256((SOURCE/'ui/dist/index.html').read_bytes()).hexdigest(),
        'finished_at':time.time()}
(DATA/'formal-activation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)

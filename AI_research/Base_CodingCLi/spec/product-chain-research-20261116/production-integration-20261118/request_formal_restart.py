"""Use the existing managed restart helper after a verified backup; wait for busy work."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid

SOURCE=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(SOURCE))
from core.fileio import atomic_write,file_lock

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--action',choices=('restart','shutdown'),default='restart')
args=parser.parse_args()

DATA=SOURCE/'state/validation/chain-production-integration-20261118'
STATE=SOURCE/'state'
backup=json.loads((DATA/'formal-before/manifest.json').read_text())
assert backup['status']=='complete'
assert json.loads((DATA/'native/evidence/final-native-management.json').read_text())['status']=='PASS'
for row in json.loads((DATA/'source-release.json').read_text()):
    path=SOURCE/row['path']
    assert hashlib.sha256(path.read_bytes()).hexdigest()==row['after_sha256'],row['path']
with file_lock(str(STATE/'assistant-maintenance-admission')):
    runtime=json.loads((STATE/'app_runtime.json').read_text())
    assert runtime['config_path']==str(SOURCE/'config/web.yaml')
    os.kill(runtime['pid'],0)
    marker_path=STATE/'assistant_maintenance.json'
    marker=json.loads(marker_path.read_text()) if marker_path.exists() else {}
    assert not (marker.get('pid')==runtime['pid'] and marker.get('status') in ('waiting','applying','starting')),marker
    key=uuid.uuid4().hex
    spec={'id':key,'action':args.action,'user_id':'local-chain-integration-operator',
          'status':'applying','pid':runtime['pid'],'created':time.time(),'updated':time.time(),
          'runtime':runtime,'state_dir':str(STATE),'active_runs':'wait','handoff_waiting_video':True,
          'reason':'Authorized chain workbench integration; preserve in-flight production'}
    target=STATE/'maintenance'/ (key+'.json');target.parent.mkdir(exist_ok=True)
    atomic_write(str(target),json.dumps(spec,ensure_ascii=False))
    atomic_write(str(marker_path),json.dumps(spec,ensure_ascii=False))
with (DATA/'formal-restart-helper.log').open('ab') as log:
    child=subprocess.Popen([runtime['python'],str(SOURCE/'scripts/service_maintenance.py'),str(target)],cwd=SOURCE,
        stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
receipt={'maintenance_id':key,'helper_pid':child.pid,'old_runtime':runtime,'spec':str(target),'requested_at':time.time()}
(DATA/'formal-restart-request.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
print(json.dumps(receipt,ensure_ascii=False,indent=2),flush=True)

"""Confirm an observer/API-only backend refresh without changing entry flags or UI."""
from pathlib import Path
import hashlib
import json
import sqlite3
import time
import httpx
SOURCE=Path(__file__).resolve().parents[3]
DATA=SOURCE/'state/validation/chain-production-integration-20261118'
request=json.loads((DATA/'formal-restart-request.json').read_text())
receipt_path=Path(request['spec'])
with sqlite3.connect((SOURCE/'state/video.db').resolve().as_uri()+'?mode=ro',uri=True) as db:
 before={table:sorted(r[0] for r in db.execute('SELECT id FROM '+table)) for table in ('scripts','renders','autopilot_runs','autopilot_items')}
(DATA/'backend-refresh-before-ids.json').write_text(json.dumps(before))
while True:
 receipt=json.loads(receipt_path.read_text())
 if receipt['status']=='completed':break
 if receipt['status'] in ('failed','interrupted'):raise RuntimeError(receipt)
 time.sleep(3)
current=json.loads((SOURCE/'state/app_runtime.json').read_text())
assert current['instance_id']!=request['old_runtime']['instance_id']
with httpx.Client(trust_env=False,timeout=10) as c:
 r=c.get('http://127.0.0.1:8766/api/health');r.raise_for_status()
 assert r.json()['instance_id']==current['instance_id']
for row in json.loads((DATA/'source-release.json').read_text()):
 assert hashlib.sha256((SOURCE/row['path']).read_bytes()).hexdigest()==row['after_sha256'],row['path']
with sqlite3.connect((SOURCE/'state/video.db').resolve().as_uri()+'?mode=ro',uri=True) as db:
 assert all(set(ids)<=set(r[0] for r in db.execute('SELECT id FROM '+table)) for table,ids in before.items())
activation=json.loads((DATA/'formal-activation.json').read_text())
assert hashlib.sha256((SOURCE/'ui/dist/index.html').read_bytes()).hexdigest()==activation['ui_sha256']
activation.setdefault('refreshes',[]).append({'from':request['old_runtime'],'to':current,'maintenance_id':request['maintenance_id'],'all_existing_ids_preserved':True})
activation['runtime']=current
activation['finished_at']=time.time()
(DATA/'formal-activation.json').write_text(json.dumps(activation,ensure_ascii=False,indent=2))
(DATA/'backend-refresh-result.json').write_text(json.dumps({'status':'PASS','runtime':current,'maintenance_id':request['maintenance_id'],'existing_id_counts':{k:len(v) for k,v in before.items()},'ui_and_access_flags_unchanged':True},ensure_ascii=False,indent=2))
print('PASS backend refresh',current['pid'],flush=True)

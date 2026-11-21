"""Read-only checks after the real managed production handover."""
from pathlib import Path
import hashlib
import json
import sqlite3
import time
import httpx
SOURCE=Path(__file__).resolve().parents[3]
DATA=SOURCE/'state/validation/chain-production-integration-20261118'
activation=json.loads((DATA/'formal-activation.json').read_text());assert activation['status']=='PASS'
current=json.loads((SOURCE/'state/app_runtime.json').read_text());assert current['instance_id']==activation['runtime']['instance_id']
with httpx.Client(trust_env=False,timeout=15) as c:
    response=c.get('http://127.0.0.1:8766/api/health');response.raise_for_status()
    assert response.json()['instance_id']==current['instance_id']
for row in json.loads((DATA/'source-release.json').read_text()):
    assert hashlib.sha256((SOURCE/row['path']).read_bytes()).hexdigest()==row['after_sha256'],row['path']
manifest=json.loads((DATA/'formal-before/manifest.json').read_text())
changed=[]
for row in manifest['files']:
    path=Path(row['path'])
    if path.is_relative_to(SOURCE/'config') or path.is_relative_to(SOURCE/'jobs'):
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:changed.append(str(path.relative_to(SOURCE)))
assert not changed,changed
fields='id,job_name,schedule,timezone,missed_policy,max_retries,version,enabled'
plans=[]
for path in [DATA/'formal-before/state/schedules.db',SOURCE/'state/schedules.db']:
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
        plans.append(list(db.execute('SELECT '+fields+' FROM schedules ORDER BY id')))
assert plans[0]==plans[1]
queue=json.loads((DATA/'formal-queue-handoff-before.json').read_text())
with sqlite3.connect((SOURCE/'state/video.db').resolve().as_uri()+'?mode=ro',uri=True) as db:
    db.row_factory=sqlite3.Row
    for run in queue['runs']:
        actual=db.execute('SELECT * FROM autopilot_runs WHERE id=?',(run['id'],)).fetchone()
        assert actual and json.loads(actual['spec'])==json.loads(run['spec'])
    for item in queue['items']:
        assert db.execute('SELECT 1 FROM autopilot_items WHERE id=?',(item['id'],)).fetchone()
    media=[]
    for render in queue['active_renders']:
        actual=dict(db.execute('SELECT id,script_id,version,status,autovideo_job_id FROM renders WHERE id=?',(render['id'],)).fetchone())
        assert actual['autovideo_job_id']==render['autovideo_job_id']
        assert actual['status']=='COMPLETED',actual
        media.append(actual)
    active=[dict(r) for r in db.execute("SELECT id,script_id,status,active_stage,autovideo_job_id FROM renders WHERE status IN ('RUNNING','QUEUED','SUBMITTING','DOWNLOADING')")]
    old_scripts={r['script_id'] for r in queue['items'] if r['script_id']}
    resumed=[r for r in active if r['script_id'] in old_scripts]
    assert resumed,active
result={'status':'PASS','runtime':current,'source_files_verified':len(json.loads((DATA/'source-release.json').read_text())),
        'existing_yaml_unchanged':True,'existing_schedule_settings_unchanged':len(plans[1]),
        'original_batch_specs_preserved':len(queue['runs']),'original_items_preserved':len(queue['items']),
        'inflight_renders_completed_without_new_remote_handles':media,'resumed_existing_queue':resumed,
        'at':time.time()}
(DATA/'formal-after-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)

"""Read-only observation of in-flight production and its managed maintenance receipt."""
from pathlib import Path
import json
import sqlite3
import sys
import time
import httpx
SOURCE=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(SOURCE))
STATE=SOURCE/'state';DATA=STATE/'validation/chain-production-integration-20261118'
result={'at':time.time()}
request=json.loads((DATA/'formal-restart-request.json').read_text())
result['maintenance']=json.loads(Path(request['spec']).read_text())['status']
with sqlite3.connect((STATE/'video.db').resolve().as_uri()+'?mode=ro',uri=True,timeout=10) as c:
    c.row_factory=sqlite3.Row
    result['renders']=[dict(r) for r in c.execute("SELECT id,status,active_stage,autovideo_job_id,updated_at FROM renders WHERE status IN ('RUNNING','QUEUED','DOWNLOADING','SUBMITTING')")]
    result['items']=[dict(r) for r in c.execute("SELECT run_id,state,count(*) AS count FROM autopilot_items WHERE run_id IN (SELECT id FROM autopilot_runs WHERE status='RUNNING') GROUP BY run_id,state")]
for render in result['renders']:
    if not render['autovideo_job_id']:continue
    try:
        with httpx.Client(trust_env=False,timeout=10) as client:
            r=client.get('http://127.0.0.1:8011/api/jobs/'+render['autovideo_job_id']);r.raise_for_status();job=r.json()
            render['remote']={k:job.get(k) for k in ('id','status','active_stage','updated_at','stages','error') if k in job}
    except Exception as e:render['observation_error']=str(e)[:180]
print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
with (DATA/'formal-drain-observations.jsonl').open('a') as f:f.write(json.dumps(result,ensure_ascii=False)+'\n')

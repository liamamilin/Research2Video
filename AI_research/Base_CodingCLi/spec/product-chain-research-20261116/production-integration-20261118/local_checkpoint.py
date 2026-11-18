from pathlib import Path
import json
import sqlite3
import time
SOURCE=Path(__file__).resolve().parents[3]
DATA=SOURCE/'state/validation/chain-production-integration-20261118'
request=json.loads((DATA/'formal-restart-request.json').read_text())
receipt=json.loads(Path(request['spec']).read_text())
result={'at':time.strftime('%H:%M:%S'),'maintenance':receipt['status'],'renders':[]}
with sqlite3.connect((SOURCE/'state/video.db').resolve().as_uri()+'?mode=ro',uri=True,timeout=10) as c:
    for rid,stage,remote in c.execute("SELECT id,active_stage,autovideo_job_id FROM renders WHERE status IN ('RUNNING','QUEUED','DOWNLOADING','SUBMITTING')"):
        log=SOURCE.parents[1]/'autovideo/g3/data/jobs'/str(remote)/'attempts/1/worker.log'
        result['renders'].append({'id':rid,'stage':stage,'tail':log.read_text(errors='replace').splitlines()[-2:] if log.exists() else []})
    result['items']=[list(r) for r in c.execute("SELECT state,count(*) FROM autopilot_items WHERE run_id IN (SELECT id FROM autopilot_runs WHERE status='RUNNING') GROUP BY state")]
print(json.dumps(result,ensure_ascii=False),flush=True)
with (DATA/'formal-local-progress.jsonl').open('a') as f:f.write(json.dumps(result,ensure_ascii=False)+'\n')

"""Keep observing two genuine due slots, then pause only the rehearsal plan."""
from pathlib import Path
import json
import sqlite3
import time
import httpx

ROOT=Path(__file__).resolve().parents[3]/'state/validation/chain-production-integration-20261118/native'
previous=None
while True:
    with sqlite3.connect((ROOT/'state/schedules.db').resolve().as_uri()+'?mode=ro',uri=True,timeout=5) as db:
        db.row_factory=sqlite3.Row
        runs=[dict(row) for row in db.execute("SELECT id,due_at,started_at,finished_at,status,output_path,context,error FROM schedule_runs WHERE job_name='chains/native_schedule' ORDER BY due_at")]
    summary=[{k:v for k,v in row.items() if k!='context'} for row in runs]
    encoded=json.dumps(summary,ensure_ascii=False)
    if encoded!=previous:
        print(encoded,flush=True)
        (ROOT/'evidence/natural-clock-progress.json').write_text(json.dumps(runs,ensure_ascii=False,indent=2))
        previous=encoded
    completed=[row for row in runs if row['status']=='success']
    if len(completed)>=2:
        with httpx.Client(base_url='http://127.0.0.1:9254',timeout=180,trust_env=False) as client:
            client.post('/api/auth/login',json={'username':'native-admin','password':'native-chain-admin-2026'}).raise_for_status()
            client.headers['x-csrf-token']=client.cookies.get('ai_research_csrf','')
            chain=client.get('/api/chains/native_schedule').json()
            result=client.post('/api/chains/native_schedule/toggle',json={'revision':chain['revision'],'scope':'research','enabled':False})
            result.raise_for_status()
            assert not result.json()['schedules'][0]['enabled']
        assert len({row['due_at'] for row in completed})>=2
        assert all(row['started_at']>=row['due_at'] for row in completed)
        (ROOT/'evidence/natural-clock-acceptance.json').write_text(json.dumps({'status':'PASS','runs':completed,'plan_paused':True},ensure_ascii=False,indent=2))
        print('PASS: two natural research slots completed; rehearsal plan paused',flush=True)
        break
    time.sleep(30)

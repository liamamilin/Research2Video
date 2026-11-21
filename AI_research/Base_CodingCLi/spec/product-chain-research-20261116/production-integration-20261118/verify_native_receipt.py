"""Generate a real report through the chain API and verify its durable run receipt."""
from pathlib import Path
import json
import time
import httpx
SOURCE=Path(__file__).resolve().parents[3]
ROOT=SOURCE/'state/validation/chain-production-integration-20261118/native'
with httpx.Client(base_url='http://127.0.0.1:9254',trust_env=False,timeout=120) as c:
    c.post('/api/auth/login',json={'username':'native-admin','password':'native-chain-admin-2026'}).raise_for_status()
    c.headers['x-csrf-token']=c.cookies.get('ai_research_csrf','')
    started=c.post('/api/chains/native_schedule/run',json={'outputs':[],'limit':1});started.raise_for_status()
    run=started.json();rid=run['id'];print('real chain research',rid,flush=True)
    (ROOT/'evidence/receipt-research-started.json').write_text(json.dumps(run,ensure_ascii=False,indent=2))
    end=time.monotonic()+600
    while time.monotonic()<end:
        r=c.get('/api/chains/runs/'+rid);r.raise_for_status();run=r.json()
        if run['status']!='RUNNING':break
        time.sleep(5)
    assert run['status']=='DONE',run
    records=[json.loads(line) for line in (ROOT/'state/report_meta.jsonl').read_text().splitlines() if line.strip()]
    matching=[r for r in records if r.get('chain_run_id')==rid]
    assert len(matching)==1,matching
    assert Path(matching[0]['path']).name==Path(run['report']).name
    result={'status':'PASS','run_id':rid,'run':run,'report_receipt':matching[0]}
    (ROOT/'evidence/real-report-run-receipt.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print('PASS actual report receipt',rid,matching[0]['path'],flush=True)

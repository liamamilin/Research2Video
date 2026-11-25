from pathlib import Path
import json
import time
import httpx

SOURCE = Path(__file__).resolve().parents[3]
EVIDENCE = SOURCE / 'state/validation/chain-production-integration-20261118/native/evidence'
records = []
with httpx.Client(base_url='http://127.0.0.1:9254', timeout=180, trust_env=False) as client:
    response=client.post('/api/auth/login',json={'username':'native-admin','password':'native-chain-admin-2026'})
    response.raise_for_status()
    for path in ('/api/chains', '/api/chains/native_science', '/api/video/autovideo'):
        start=time.monotonic()
        response=client.get(path)
        duration=round(time.monotonic()-start,3)
        response.raise_for_status()
        body=response.json()
        name='chains' if path=='/api/chains' else 'science' if path.endswith('native_science') else 'renderer'
        (EVIDENCE/(name+'-response.json')).write_text(json.dumps(body,ensure_ascii=False,indent=2))
        records.append({'path':path,'seconds':duration,'status':response.status_code})
        print(path,response.status_code,duration,flush=True)
(EVIDENCE/'response-times.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))

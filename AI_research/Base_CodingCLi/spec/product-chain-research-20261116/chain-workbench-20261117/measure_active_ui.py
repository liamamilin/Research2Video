from pathlib import Path
import httpx,json,time
root=Path(__file__).resolve().parent
records=[]
with httpx.Client(base_url='http://127.0.0.1:9252',timeout=120,trust_env=False) as client:
    client.post('/api/auth/login',json={'username':'isolation-admin','password':'local-isolation-admin-2026'}).raise_for_status()
    for path in ('/api/chains/job-050ab505483c','/api/chains/research_updates','/api/chains/catalog'):
        started=time.monotonic();response=client.get(path);response.raise_for_status()
        records.append({'path':path,'seconds':round(time.monotonic()-started,3),'status':response.status_code})
        print(records[-1],flush=True)
(root/'runtime/evidence/active-ui-response-times.json').write_text(json.dumps(records,indent=2))

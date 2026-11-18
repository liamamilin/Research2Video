import httpx,json
from pathlib import Path
p=Path(__file__).resolve().parent
with httpx.Client(base_url='http://127.0.0.1:9252',timeout=30,trust_env=False) as c:
 r=c.post('/api/auth/login',json={'username':'isolation-admin','password':'local-isolation-admin-2026'});r.raise_for_status()
 c.headers['x-csrf-token']=c.cookies.get('ai_research_csrf','')
 for path in ('/api/chains/job-050ab505483c','/api/jobs/monitoring/ai_flash_discovery/logs'):
  r=c.get(path);data=r.json();(p/'runtime/evidence'/('flash-chain.json' if path.startswith('/api/chains') else 'flash-logs.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2))
  if path.startswith('/api/chains'):print([{k:x.get(k) for k in ('id','status','stage','note')} for x in data.get('runs',[])])
  else:print(str(data)[-4500:])

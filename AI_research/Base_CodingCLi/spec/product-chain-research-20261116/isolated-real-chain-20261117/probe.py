import json
from isolated_app import app, HERE, ROOT
from fastapi.testclient import TestClient
client = TestClient(app)
r = client.post('/api/auth/login',json={'username':'isolation-admin','password':'local-isolation-admin-2026'})
print('login',r.status_code)
client.headers['x-csrf-token']=client.cookies.get('ai_research_csrf','')
result={}
for path in ['/api/jobs/monitoring/ai_flash_discovery','/api/production?job=monitoring/ai_flash_discovery','/api/production/flash_daily/settings','/api/matrices/ai_media?reports=false','/api/sources/cg_openai_news','/api/scheduler?scope=managed']:
    r=client.get(path)
    result[path]={'status':r.status_code,'data':r.json()}
    data=r.json()
    print(path,r.status_code, list(data)[:20] if isinstance(data,dict) else len(data))
(HERE/'probe.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))

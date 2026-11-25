import json
from app import app,ROOT,HERE
from fastapi.testclient import TestClient
from web.services import chains
c=TestClient(app)
r=c.post('/api/auth/login',json={'username':'isolation-admin','password':'local-isolation-admin-2026'});assert r.status_code==200,r.text
c.headers['x-csrf-token']=c.cookies.get('ai_research_csrf','')
def request(method,path,**kw):
 r=c.request(method,path,**kw);print(method,path,r.status_code,flush=True)
 if r.status_code>=400:print(r.text[:1500],flush=True)
 return r
items=request('GET','/api/chains').json()['items'];print('chains',len(items))
flash=next(x for x in items if x['job']=='monitoring/ai_flash_discovery')
d=request('GET','/api/chains/'+flash['key']).json();print('flash',len(d['outputs_detail']),d['script_count'])
body={'key':'science_trial','name':'科研进展','prompt':'面向中文读者解释 AI 推进科学研究的新发现和具体方法。','sources':['source:cg_openai_news'],'output_modes':['individual','episode'],'schedule':'0 10 * * *','video_speed':1.25}
r=request('POST','/api/chains',json=body)
if r.status_code==201:
 data=r.json();print(data['outputs'],data['policy_managed']);(HERE/'smoke.json').write_text(json.dumps({'flash':d,'created':data},ensure_ascii=False,indent=2,default=str))

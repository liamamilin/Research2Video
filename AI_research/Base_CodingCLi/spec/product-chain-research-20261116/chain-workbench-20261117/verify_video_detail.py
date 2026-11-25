from pathlib import Path
import httpx,json,time
root=Path(__file__).resolve().parent
sid='ceaaf17bc5ff41849878b1159971c99e'
results=[]
with httpx.Client(base_url='http://127.0.0.1:9252',timeout=120,trust_env=False) as client:
 client.post('/api/auth/login',json={'username':'isolation-admin','password':'local-isolation-admin-2026'}).raise_for_status()
 for attempt in range(3):
  start=time.monotonic();response=client.get('/api/video/scripts/'+sid);response.raise_for_status();data=response.json()
  assert data['id']==sid and any(r['status']=='COMPLETED' for r in data['renders'])
  results.append({'attempt':attempt+1,'seconds':round(time.monotonic()-start,3),'status':response.status_code,'completed_render':True,'distribution_targets':len(data['distributions'])})
  print(results[-1],flush=True)
(root/'runtime/evidence/video-detail-response-times.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))

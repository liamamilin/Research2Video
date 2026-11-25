from pathlib import Path
import httpx,json,time
root=Path(__file__).resolve().parent
with httpx.Client(base_url='http://127.0.0.1:9252',timeout=120,trust_env=False) as c:
 c.post('/api/auth/login',json={'username':'isolation-admin','password':'local-isolation-admin-2026'}).raise_for_status()
 for key in ('research_updates','job-050ab505483c'):
  r=c.get('/api/chains/'+key);r.raise_for_status();d=r.json()
  for row in d['runs']:
   run=c.get('/api/chains/runs/'+row['id']).json()
   print(key,run['status'],run.get('note'),flush=True)
   for item in (run.get('batch') or {}).get('items',[]):print({k:item.get(k) for k in ('state','note','script_id','render_id','render_progress')},flush=True)
   (root/'runtime/evidence'/f'{key}-run.json').write_text(json.dumps(run,ensure_ascii=False,indent=2))

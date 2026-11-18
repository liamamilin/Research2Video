"""Assert the concrete end-to-end deliverable before completing this task."""
from pathlib import Path
import httpx,json,time
ROOT=Path(__file__).resolve().parent
EVIDENCE=ROOT/'runtime/evidence'
EXPECTED={
 'job-050ab505483c':('751e7cc9b99e477a9c9973197db8d73d',{'ceaaf17bc5ff41849878b1159971c99e','115d4ebe6c1648dc8b2ede59a2e6542a'}),
 'research_updates':('604580ff078347c6815d2468068ed4e1',{'9541c940e2004129ab0a4288edfd822d','4fd059213d4643cf9ea0d39b00e72687'}),
}
media=json.loads((EVIDENCE/'media-validation.json').read_text())
assert set(media)==set.union(*(scripts for _,scripts in EXPECTED.values()))
assert all(v['full_decode']=='PASS' and v['bytes']>0 and Path(v['video']).is_file() for v in media.values())
assert json.loads((EVIDENCE/'flash-browser-media.json').read_text())['identical']
for filename in ('research-single-browser-playback.json','flash-single-browser-playback.json','research-episode-browser-playback.json'):
 rows=json.loads((EVIDENCE/filename).read_text());assert len(rows)==1
 assert rows[0]['time']>1 and rows[0]['readyState']>=3 and not rows[0].get('error')
runs=[]
with httpx.Client(base_url='http://127.0.0.1:9252',timeout=60,trust_env=False) as client:
 client.post('/api/auth/login',json={'username':'isolation-admin','password':'local-isolation-admin-2026'}).raise_for_status()
 for key,(rid,scripts) in EXPECTED.items():
  response=client.get('/api/chains/runs/'+rid);response.raise_for_status();run=response.json()
  assert run['status']=='DONE',run.get('note')
  assert {i['script_id'] for i in run['batch']['items']}==scripts
  assert all(i['state']=='DONE' and i['render_progress']['status']=='COMPLETED' for i in run['batch']['items'])
  (EVIDENCE/(key+'-completed-run.json')).write_text(json.dumps(run,ensure_ascii=False,indent=2))
  runs.append({'chain':key,'run_id':rid,'status':run['status'],'report':run['report'],'scripts':sorted(scripts)})
result={'checked_at':time.strftime('%Y-%m-%d %H:%M:%S'),'scope':'isolated functional workbench','status':'PASS','runs':runs,'media':media,'browser_download_identical':True,'all_four_browser_playback_verified':True}
(EVIDENCE/'final-acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print('PASS: two original runs DONE; four returned MP4s decoded and played; browser download identical')

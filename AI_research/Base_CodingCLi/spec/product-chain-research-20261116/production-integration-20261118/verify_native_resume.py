from pathlib import Path
import json
import httpx
SOURCE=Path(__file__).resolve().parents[3]
with httpx.Client(base_url='http://127.0.0.1:9254',trust_env=False,timeout=120) as c:
    c.post('/api/auth/login',json={'username':'native-admin','password':'native-chain-admin-2026'}).raise_for_status()
    c.headers['x-csrf-token']=c.cookies.get('ai_research_csrf','')
    url='/api/chains/job-050ab505483c'
    detail=c.get(url);detail.raise_for_status()
    response=c.post(url+'/toggle',json={'revision':detail.json()['revision'],'scope':'outputs','enabled':True});response.raise_for_status()
    resumed=response.json();manual=[o for o in resumed['outputs_detail'] if o['split']=='some']
    assert manual and all(o['auto']==0 and not o['errors'] for o in manual)
    assert all(not o['errors'] for o in resumed['outputs_detail'])
    response=c.post(url+'/toggle',json={'revision':resumed['revision'],'scope':'outputs','enabled':False});response.raise_for_status()
    assert all(o['auto']==0 for o in response.json()['outputs_detail'])
    science=c.get('/api/chains/native_science');science.raise_for_status()
    assert science.json()['runs'][0]['status']=='DONE'
    result={'status':'PASS','manual_outputs':[o['key'] for o in manual],'all_outputs_paused_after_test':True,'original_science_run':'DONE'}
    (SOURCE/'state/validation/chain-production-integration-20261118/native/evidence/manual-roundup-resume.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False),flush=True)

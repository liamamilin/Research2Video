"""Exercise the final native HTTP surface and the operator's reversible change tool."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import httpx

SOURCE = Path(__file__).resolve().parents[3]
ROOT = SOURCE / 'state/validation/chain-production-integration-20261118/native'
EVIDENCE = ROOT / 'evidence'
env = {**os.environ, 'AI_RESEARCH_WEB_CONFIG': str(ROOT/'config/web.yaml')}

def hashes():
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in ('config','jobs') for p in (ROOT/folder).rglob('*.yaml')
            if 'backups' not in p.parts}

with httpx.Client(base_url='http://127.0.0.1:9254', timeout=180, trust_env=False) as c:
    def call(method, path, **kwargs):
        r=c.request(method,path,**kwargs); r.raise_for_status(); return r.json()
    call('POST','/api/auth/login',json={'username':'native-admin','password':'native-chain-admin-2026'})
    c.headers['x-csrf-token']=c.cookies.get('ai_research_csrf','')
    result={'version':call('GET','/api/chains/catalog')['access']['implementation_version']}
    science=call('GET','/api/chains/native_science')
    assert science['runs'][0]['status']=='DONE'
    original=call('GET','/api/chains/runs/'+science['runs'][0]['id'])
    assert len(original['batch']['items'])==2
    assert all(x['state']=='DONE' for x in original['batch']['items'])
    result['original_run_survived_restart']=science['runs'][0]['id']
    scheduled=call('GET','/api/chains/native_schedule')
    natural=[r for r in scheduled['runs'] if r.get('native_schedule')]
    assert len(natural)>=2 and all(r['status']=='DONE' for r in natural[:2]), natural
    result['natural_history']=[r['id'] for r in natural]
    # A structurally different legacy chain retains its recipe, Job and pause state.
    listing=call('GET','/api/chains')
    whole=next(x for x in listing['items'] if x['job']=='economy/economy_illustrated')
    url='/api/chains/'+whole['key']; before=hashes(); detail=call('GET',url)
    added=call('PUT',url,json={'revision':detail['revision'],'add_output':{'mode':'whole','name':'整份报告接入复验'}})
    output=next(x for x in added['outputs_detail'] if x['key'] not in detail['outputs'])
    assert output['base_recipe']=='illustrated_explainer' and output['split']=='whole' and output['auto']==0 and not output['errors']
    edited=call('PUT',url,json={'revision':added['revision'],'outputs':[{'key':output['key'],'auto':0,'render':{**output['render'],'video_speed':1.25}}]})
    call('PUT',url,json={'revision':edited['revision'],'remove_outputs':[output['key']]})
    after=hashes(); assert all(after[p]==v for p,v in before.items())
    result['whole_report']={'chain':whole['key'],'added_modified_removed':True,'original_yaml_preserved':True}
    # Shared research remains after removing one independently managed chain entry.
    shared=call('POST','/api/chains',json={'key':'native_shared_probe','name':'共享管理复验','existing_job':science['job'],'output_modes':['individual'],'schedule':''})
    call('DELETE','/api/chains/'+shared['key'],params={'revision':shared['revision']})
    assert (ROOT/'jobs'/ (science['job']+'.yaml')).exists()
    result['shared_deletion_preserved_original']=True
    # The actual operator CLI reverses one new, not-yet-run chain; all unrelated YAML survives.
    before=hashes(); journals=set((ROOT/'state/config_changes').glob('*.json'))
    probe=call('POST','/api/chains',json={'key':'native_undo_probe','name':'逆向变更复验','prompt':'研究AI科学工具','sources':['source:cg_openai_news'],'output_modes':['individual'],'schedule':''})
    created=set((ROOT/'state/config_changes').glob('*.json'))-journals
    committed=[p for p in created if json.loads(p.read_text())['status']=='committed']
    assert len(committed)==1
    undo=subprocess.run([sys.executable,str(ROOT/'scripts/manage_chain_rollout.py'),'undo','--change',committed[0].stem],cwd=ROOT,env=env,capture_output=True,text=True,timeout=180)
    assert undo.returncode==0, undo.stderr
    after=hashes(); assert before==after
    assert c.get('/api/chains/native_undo_probe').status_code==404
    result['rollback']={'change':committed[0].stem,'original_yaml_exact_match':True,'message':undo.stdout.strip()}
    result['status']='PASS'
    (EVIDENCE/'final-native-management.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)

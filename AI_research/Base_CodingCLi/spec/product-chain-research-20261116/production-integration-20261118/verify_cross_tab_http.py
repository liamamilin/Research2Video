"""Use legacy production routes against real rehearsal chains and shared research."""
from pathlib import Path
import hashlib
import json
import httpx
SOURCE=Path(__file__).resolve().parents[3]
ROOT=SOURCE/'state/validation/chain-production-integration-20261118/native'
def hashes():return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'config/production').glob('*.yaml')}
with httpx.Client(base_url='http://127.0.0.1:9254',trust_env=False,timeout=120) as c:
 def call(method,path,**kw):
  r=c.request(method,path,**kw);r.raise_for_status();return r.json()
 call('POST','/api/auth/login',json={'username':'native-admin','password':'native-chain-admin-2026'})
 c.headers['x-csrf-token']=c.cookies.get('ai_research_csrf','')
 before=hashes();science=call('GET','/api/chains/native_science')
 shared=call('POST','/api/chains',json={'key':'native_cross_scope','name':'跨Tab归属复验','existing_job':science['job'],'output_modes':['individual'],'schedule':''})
 made=call('POST','/api/production',json={'job':science['job'],'recipe':science['outputs_detail'][0]['base_recipe'],'reverify':False,'auto':0,'chain_key':shared['key']})
 second=call('GET','/api/chains/'+shared['key']);first=call('GET','/api/chains/native_science')
 assert made['key'] in second['outputs'] and made['key'] not in first['outputs']
 call('DELETE','/api/production/'+made['key'])
 second=call('GET','/api/chains/'+shared['key'])
 assert made['key'] not in second['outputs'] and made['key'] in second['retired_outputs']
 call('DELETE','/api/chains/'+shared['key'],params={'revision':second['revision']})
 economy=next(x for x in call('GET','/api/chains')['items'] if x['job']=='economy/economy_illustrated')
 made=call('POST','/api/production',json={'job':economy['job'],'recipe':'illustrated_explainer','audience':'general','auto':0})
 assert made['key'] in call('GET','/api/chains/'+economy['key'])['outputs']
 call('DELETE','/api/production/'+made['key'])
 assert before==hashes(),'Original production YAML changed'
 result={'status':'PASS','shared_context_owner_correct':True,'removed_output_retired':True,'global_legacy_output_visible_in_imported_chain':True,'original_production_sha256_unchanged':True,'original_science_history':call('GET','/api/chains/native_science')['script_count']}
 assert result['original_science_history']==2
 (ROOT/'evidence/cross-tab-http.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
 print(json.dumps(result,ensure_ascii=False),flush=True)

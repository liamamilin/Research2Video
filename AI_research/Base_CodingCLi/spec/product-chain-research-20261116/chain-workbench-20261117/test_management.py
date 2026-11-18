"""Regression checks against actual HTTP routes; all business changes rolled back."""
import json,hashlib,sqlite3
from unittest.mock import patch
import app as app_module
from app import app,HERE
from validation_env import setup
ROOT=setup(app_module)
from fastapi.testclient import TestClient
from web.services import chains
from core import sources
trace=[]
def client(role):
 c=TestClient(app);r=c.post('/api/auth/login',json={'username':'isolation-'+role,'password':'local-isolation-'+role+'-2026'});assert r.status_code==200
 c.headers['x-csrf-token']=c.cookies.get('ai_research_csrf','');return c
c=client('admin');viewer=client('viewer');editor=client('editor')
def req(method,path,expected=200,actor=c,**kw):
 r=actor.request(method,path,**kw);print(method,path,r.status_code,flush=True);trace.append({'method':method,'path':path,'expected':expected,'actual':r.status_code,'body':r.json()})
 assert r.status_code==expected,(path,r.status_code,r.text[:1200]);return r.json()
class Rollback(Exception):pass
original={p:p.read_bytes() for folder in (ROOT/'config',ROOT/'jobs') for p in folder.rglob('*.yaml')}
backup=sqlite3.connect(':memory:')
with sqlite3.connect(ROOT/'state/schedules.db') as d:d.backup(backup)
try:
 if True:
  flash=next(x for x in req('GET','/api/chains')['items'] if x['job']=='monitoring/ai_flash_discovery');key=flash['key']
  detail=req('GET','/api/chains/'+key);assert len(detail['outputs_detail'])==7
  req('DELETE','/api/jobs/monitoring/ai_flash_discovery',409)
  req('DELETE','/api/matrices/ai_media',409)
  req('POST','/api/chains',403,actor=viewer,json={})
  body={'key':'regression_chain','name':'回归科研链路','prompt':'研究AI推进科学的方法与发现。','sources':['source:cg_openai_news'],'output_modes':['individual','episode'],'schedule':'0 10 * * *'}
  req('POST','/api/chains',403,actor=editor,json=body)
  before=set((ROOT/'config').rglob('*.yaml'))|set((ROOT/'jobs').rglob('*.yaml'))
  req('POST','/api/chains',422,json={**body,'schedule':'bad cron'})
  after=set((ROOT/'config').rglob('*.yaml'))|set((ROOT/'jobs').rglob('*.yaml'));assert before==after,'半链创建残留'
  d=req('POST','/api/chains',201,json=body);assert d['policy_managed'] and len(d['outputs_detail'])==2
  assert {x['content_mode'] for x in d['outputs_detail']}=={'episode','individual'}
  unchanged=req('GET','/api/chains/'+key)
  assert all(not p['errors'] for p in unchanged['outputs_detail']),'新链路破坏已有 Flash 配方'
  assert [(p['key'],p['content_mode']) for p in unchanged['outputs_detail']]==[(p['key'],p['content_mode']) for p in detail['outputs_detail']]
  k=d['key'];rev=d['revision']
  src=req('GET','/api/sources/cg_openai_news')
  req('PATCH','/api/sources/cg_openai_news',json={'expected_revision':src['revision'],'fields':{'name':'科研来源 · 修改'}})
  req('PUT','/api/chains/'+k,409,json={'revision':rev,'name':'不应覆盖'})
  d=req('GET','/api/chains/'+k);assert any(x['name']=='科研来源 · 修改' for x in d['sources'])
  d=req('PUT','/api/chains/'+k,json={'revision':d['revision'],'name':'科研新名称','outputs':[{'key':d['outputs'][0],'render':{'video_speed':1.5}}]})
  assert d['name']=='科研新名称' and d['outputs_detail'][0]['effective_render']['video_speed']==1.5
  d=req('PUT','/api/chains/'+k,json={'revision':d['revision'],'window_mode':'rolling','window_days':3,'outputs':[{'key':d['outputs'][0],'minutes':2.5,'audience':'developer'}]})
  assert d['window_days']==3 and d['window_mode']=='rolling'
  assert d['outputs_detail'][0]['target_minutes']==2.5 and d['outputs_detail'][0]['audience_key']=='developer'
  assert d['outputs_detail'][1]['audience_key']=='non_tech','修改单条观众不应改变合集'
  prior=d['revision'];req('PUT','/api/chains/'+k,422,json={'revision':prior,'outputs':[{'key':d['outputs'][0],'minutes':999}]})
  d=req('GET','/api/chains/'+k);assert d['revision']==prior,'无效时长应完整回滚'
  d=req('POST','/api/chains/'+k+'/toggle',json={'revision':d['revision'],'scope':'outputs','enabled':False});assert not any(x['auto'] for x in d['outputs_detail']);assert d['schedules'][0]['enabled']
  d=req('POST','/api/chains/'+k+'/toggle',json={'revision':d['revision'],'scope':'outputs','enabled':True});assert all(x['auto'] for x in d['outputs_detail'])
  src=req('GET','/api/sources/cg_openai_news');req('PATCH','/api/sources/cg_openai_news',json={'expected_revision':src['revision'],'fields':{'enabled':False}})
  assert sources.resolve(['source:cg_openai_news'],str(ROOT/'config'))==[]
  src=req('GET','/api/sources/cg_openai_news');req('PATCH','/api/sources/cg_openai_news',json={'expected_revision':src['revision'],'fields':{'enabled':True}})
  shared=req('POST','/api/chains',201,json={**body,'key':'regression_shared','name':'共享科研','existing_job':'chains/regression_chain','schedule':''})
  d=req('GET','/api/chains/'+k);assert len(d['shared_chains'])==1
  req('PUT','/api/chains/'+k,409,json={'revision':d['revision'],'prompt':'共享研究发生变化'})
  d=req('PUT','/api/chains/'+k,json={'revision':d['revision'],'prompt':'共享研究发生变化','apply_shared':True})
  s=req('GET','/api/chains/regression_shared');assert s['research']['prompt']=='共享研究发生变化'
  pid=d['outputs'][1];req('DELETE','/api/production/'+pid)
  d=req('GET','/api/chains/'+k);assert pid not in d['outputs']
  assert all(pid not in v.get('production_ids',[]) for v in [chains.read(p) for p in (ROOT/'config/content_columns').glob('*.yaml')])
  assert all(pid not in (p.get('episode_production'),p.get('single_production')) for p in chains.read(ROOT/'config/joint_planning.yaml')['pairs'])
  d=req('PUT','/api/chains/'+k,json={'revision':d['revision'],'add_output':{'mode':'episode','name':'新合集'}});assert len(d['outputs'])==2
  before=req('GET','/api/chains/'+k+'/delete-preview');assert before['keep_job']
  req('DELETE','/api/chains/'+k,params={'revision':before['revision']})
  assert (ROOT/'jobs/chains/regression_chain.yaml').exists()
  s=req('GET','/api/chains/regression_shared');assert all(not o['errors'] for o in s['outputs_detail'])
  req('DELETE','/api/chains/regression_shared',params={'revision':s['revision']})
  assert not (ROOT/'jobs/chains/regression_chain.yaml').exists()
  raise Rollback()
except Rollback:pass
finally:
 for folder in (ROOT/'config',ROOT/'jobs'):
  for p in folder.rglob('*.yaml'):
   if p not in original:p.unlink()
 for p,value in original.items():p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(value)
 with sqlite3.connect(ROOT/'state/schedules.db') as d:backup.backup(d)
 backup.close()
 (HERE/'management-tests.json').write_text(json.dumps(trace,ensure_ascii=False,indent=2,default=str))
print('真实管理回归通过',len(trace),'次请求；副本回归数据已还原')

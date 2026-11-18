import app as module
from validation_env import setup
ROOT=setup(module)
from web.services import chains,chain_runner
from unittest.mock import patch,MagicMock
from concurrent.futures import Future
import time,json
user={'username':'isolated-test','role':'admin'}
chain=chains.create({'key':'scheduler_check','name':'调度验收链路','prompt':'研究科学进展','sources':['source:cg_openai_news'],'output_modes':['individual'],'schedule':'* * * * *'},user)
ss=chains.schedule_store();plan=chain['schedules'][0]
with ss.connect(True) as d:d.execute('update schedules set next_run_at=? where id=?',(time.time()-1,plan['id']))
before={p['id']:p['next_run_at'] for p in ss.list() if p['id']!=plan['id']}
task=MagicMock();task.future=Future();task.task_id='schedule-test';task.status='running'
registry=MagicMock();registry.is_running.return_value=False;registry.get.return_value=task
registry.cancel.side_effect=lambda job:task.future.cancel()
with patch.object(chain_runner,'start_job',return_value=task),patch.object(chain_runner,'TaskRegistry',return_value=registry):
 chain_runner.tick()
 run=chains.runs(chain['key'])[0];assert run['schedule_run_id']
 ledger=ss.run(run['schedule_run_id']);assert ledger['status']=='running' and ledger['attempt']==1
 assert ss.health()['online']
 assert before=={p['id']:p['next_run_at'] for p in ss.list() if p['id']!=plan['id']},'越界执行了复制周期'
 ss.cancel(run['schedule_run_id']);chain_runner.tick();chain_runner.tick()
 assert chain_runner.get(run['id'])['status']=='CANCELLED'
 assert ss.run(run['schedule_run_id'])['status']=='cancelled'
 with ss.connect() as db:assert db.execute('select count(*) from schedule_attempts where run_id=?',(run['schedule_run_id'],)).fetchone()[0]==1
 scope=chains.scope(chain['key']);assert scope['job']=='chains/scheduler_check'
 # Pause future video output independently of the research schedule.
 d=chains.detail(chain['key']);chains.toggle(chain['key'],{'revision':d['revision'],'scope':'outputs','enabled':False},user)
 assert ss.get(plan['id'])['enabled']
result={'scheduled_chain_run':run['id'],'schedule_run':ss.run(run['schedule_run_id']),'unrelated_plans_unchanged':len(before),'scope':scope}
(module.HERE/'runner-tests.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print('周期入队、启动、心跳、跨页取消、运行台账及隔离范围验证通过')

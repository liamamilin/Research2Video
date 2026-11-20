"""Only advance explicitly owned chain runs; never replay copied historic work."""
from __future__ import annotations
import copy,json,logging,threading,time,uuid,os,sqlite3
from pathlib import Path
from core import state
from core.video import autopilot_store as ap,store,production
from web.runner.registry import TaskRegistry
from web.runner.executor import start_job
from web.services import chains,video_autopilot
logger=logging.getLogger(__name__)
STOP=threading.Event()
_last_heartbeat=0.0

def get(rid):
    with chains.db() as d:r=d.execute('SELECT data FROM runs WHERE id=?',(rid,)).fetchone()
    if not r:chains.error('运行记录不存在',404)
    row=json.loads(r['data'])
    if row.get('report'):
        from web.services.chain_reports import relative_path
        row['report_view']=relative_path(row['report'])
    if row.get('batch_id'):
        row['batch']=ap.get_run(row['batch_id'])
        for item in (row['batch'] or {}).get('items',[]):
            render=store.get_render(item['render_id']) if item.get('render_id') else None
            if render:item['render_progress']={k:render.get(k) for k in ('status','active_stage','error_message','created_at')}
    if row.get('task_id'):
        from web.runner.log_store import read_events
        _,events=read_events(chains.paths().logs_dir,row['job'],row['task_id'],limit=8)
        phases={'building_prompt':'准备研究材料','organizing_topics':'整理选题','supplementing_materials':'补充选题材料','saving_output':'保存研究报告'}
        for event in events:
            payload=event['event']
            if payload.get('phase'):payload['message']=phases.get(payload['phase'],payload['phase'])+('：'+str(payload['detail']) if payload.get('detail') else '')
        row['progress']=events
    return row

def put(row):
    row=dict(row);row.pop('batch',None);row.pop('progress',None);chains.save_run(row)

def start(key,body,user):
    with chains.LOCK:
        d=chains.detail(key)
        if d.get('archived'):chains.error('已删除链路不能运行')
        if chains.active_for_job(d['job']):chains.error('共享研究已有运行中的批次，请进入该批次查看')
        if TaskRegistry().is_running(d['job']):chains.error('研究任务正在其他页面运行，请等待完成')
        chosen=body.get('outputs') or [x['key'] for x in d['outputs_detail'] if x['auto']]
        outputs=[x for x in d['outputs_detail'] if x['key'] in chosen]
        if len(outputs)!=len(chosen) or any(x['errors'] for x in outputs):chains.error('本次输出不可用，请检查链路设置',422)
        limit=int(body.get('limit',1))
        if not 0<=limit<=10:chains.error('单条数量为 0（全部）或 1–10',422)
        row={'id':uuid.uuid4().hex,'chain_id':key,'job':d['job'],'outputs':chosen,'limit':limit,'status':'RUNNING','stage':'RESEARCH',
             'started_at':time.time(),'started_by':user['username'],'snapshot':{k:v for k,v in d.items() if k not in ('scripts','runs')},
             'note':'正在获取信息源并生成研究报告','attempt':1,'events':[],'schedule_run_id':body.get('schedule_run_id')}
        put(row)
        try:
            task=start_job(d['job'],user['username'],chains.paths().config_dir,chains.paths().jobs_dir,flash_triggered_at=time.time(),run_context=body.get('run_context'))
            row['task_id']=task.task_id;put(row)
        except Exception as exc:
            row.update(status='FAILED',note=str(exc));put(row)
        return get(row['id'])

def cancel(rid):
    with chains.LOCK:
        row=get(rid)
        if row['status'] not in ('RUNNING','CANCELLING'):return row
        if row['stage']=='RESEARCH':TaskRegistry().cancel(row['job'])
        if row.get('batch_id'):
            batch=ap.get_run(row['batch_id'])
            for item in batch['items']:
                if item['state'] not in ('DONE','FAILED','HELD','CANCELLED'):ap.update_item(item['id'],state='CANCELLED',note='用户停止链路接续')
            ap.update_run(batch['id'],status='CANCELLED')
        _cancel_renders(row)
        row.update(status='CANCELLING',note='已停止接续，并请求取消排队/运行中的渲染；等待当前工作退出，完成产物保留');put(row)
        return get(rid)

def retry(rid,user):
    with chains.LOCK:
        row=get(rid)
        if row['status'] not in ('FAILED','HELD','CANCELLED','INTERRUPTED') and not (row['stage']=='VIDEO' and row['status']=='RUNNING' and any(i['state']=='HELD' for i in (row.get('batch') or {}).get('items',[]))):chains.error('当前运行不需要重试')
        if any(r['id']!=rid for r in chains.active_for_job(row['job'])):chains.error('共享研究还有任务运行中')
        if row.get('batch_id'):
            batch=ap.get_run(row['batch_id'])
            current=chains.detail(row['chain_id'])
            row.setdefault('retry_history',[]).append({'at':time.time(),'by':user['username'],'outputs':[{k:o[k] for k in ('key','name','effective_render')} for o in current['outputs_detail'] if o['key'] in row['outputs']]})
            for item in batch['items']:
                if item['state'] in ('FAILED','HELD','CANCELLED'):
                    history=store.list_renders(item['script_id']) if item.get('script_id') else []
                    latest=history[0] if history else {}
                    if latest.get('status')=='FAILED' and latest.get('error_code') in ('DOWNLOAD_FAILED','SUBMIT_UNCONFIRMED'):
                        from web.services import video_render
                        if latest['error_code']=='DOWNLOAD_FAILED':store.resume_render_download(latest['id'])
                        else:store.update_render(latest['id'],status='SUBMITTING',error_code=None,error_message=None,completed_at=None)
                        video_render.start(latest['id'])
                    cutoff=max((r['created_at'] for r in store.list_renders(item['script_id']) if r['status'] in ('FAILED','CANCELLED')),default='') if item.get('script_id') else ''
                    ap.update_item(item['id'],state='WORKING' if item.get('script_id') else 'PENDING',note='用户从失败步骤继续',
                                   retries={'renders_forgiven_before':cutoff})
            ap.update_run(batch['id'],status='RUNNING')
            row.update(status='RUNNING',stage='VIDEO',note='复用已有报告与讲稿，采用当前出片设置继续未完成视频',attempt=row.get('attempt',1)+1)
            put(row);return get(rid)
        return start(row['chain_id'],{'outputs':row['outputs'],'limit':row['limit']},user)

def _cancel_renders(row):
    from web.services import video_render
    sent=set(row.get('cancelled_render_requests',[]))
    for item in (ap.get_run(row['batch_id']) or {}).get('items',[]) if row.get('batch_id') else []:
        if not item.get('script_id'):continue
        for render in store.list_renders(item['script_id']):
            if render['id'] in sent or render['status'] not in ('QUEUED','RUNNING') or not render.get('autovideo_job_id'):continue
            try:
                video_render.get_client()._request('POST','/api/jobs/'+render['autovideo_job_id']+'/cancel')
                sent.add(render['id'])
            except Exception as exc:logger.warning('render cancel deferred: %s',exc)
    row['cancelled_render_requests']=list(sent)

def _still_working(row):
    task=TaskRegistry().get(row['job'])
    if task and task.future and not task.future.done():return True
    if row.get('batch_id'):
        from web.services import video_render
        for i in (ap.get_run(row['batch_id']) or {}).get('items',[]):
            if i.get('script_id'):
                s=store.get_script(i['script_id']) or {}
                if s.get('status') in ('RESEARCHING','WRITING','AUDITING'):return True
                if any(r['status'] in ('SUBMITTING','QUEUED','RUNNING','DOWNLOADING') for r in store.list_renders(i['script_id'])):return True
    return False

def advance(row):
    if row['status']=='CANCELLING':
        _cancel_renders(row);put(row)
        if not _still_working(row):row.update(status='CANCELLED',note='已停止，完成的产物与运行记录保留',finished_at=time.time());put(row)
        return
    if row['stage']=='RESEARCH':
        task=TaskRegistry().get(row['job'])
        if task and task.future and not task.future.done():return
        if not task:
            row.update(status='INTERRUPTED',note='应用重启时研究中断，可重新运行研究');put(row);return
        result=state.StateManager.get(row['job']) or {}
        if task.status!='success':
            row.update(status='FAILED',note=result.get('last_error') or '研究未完成，请查看运行日志');put(row);return
        report=result.get('last_output')
        if not report:row.update(status='FAILED',note='研究返回成功但未记录报告路径');put(row);return
        if not row['outputs']:
            row.update(status='DONE',report=report,stage='RESEARCH',note='研究报告完成；视频输出已暂停，本次没有制作视频',finished_at=time.time());put(row);return
        ps=production.load_all(chains.paths().config_dir,chains.paths().jobs_dir)
        picks=[]
        for pid in row['outputs']:
            p=ps[pid];pick={'column':p.recipe,'production':pid,'count':row['limit'] or 1,'all':row['limit']==0}
            if p.content_mode=='episode':pick['content_mode']='episode'
            if p.split=='whole':pick['whole']=True
            else:pick['source']=p.source
            picks.append(pick)
        from core.report_identity import describe
        root=str(Path(chains.paths().config_dir).parent)
        b=video_autopilot.start_run({'picks':picks,'refresh':False,'job':row['job'],'report':report,
                                  'report_identity':describe(report,root)['id']},started_by=row['started_by'])
        row.update(stage='VIDEO',report=report,batch_id=b['id'],note='报告完成，正在选题、写稿与生成视频');put(row)
    if row['stage']=='VIDEO':
        batch=ap.get_run(row['batch_id'])
        video_autopilot._tick_run(batch,video_autopilot._in_flight([batch]))
        batch=ap.get_run(row['batch_id'])
        if batch['status']!='RUNNING':
            states={i['state'] for i in batch['items']}
            if states & {'FAILED'}:status='FAILED'
            elif states & {'HELD'}:status='HELD'
            elif batch['status']=='CANCELLED':status='CANCELLED'
            elif not batch['items']:status='NO_CONTENT'
            else:status='DONE'
            row.update(status=status,note=batch.get('note',''),finished_at=time.time());put(row)

def advance_safely(row):
    try:
        advance(row)
    except Exception as exc:
        if isinstance(exc, sqlite3.OperationalError) and any(word in str(exc).lower() for word in ('locked', 'busy')):
            logger.warning('chain run %s waiting for database: %s', row['id'], exc)
            return
        logger.exception('chain run %s failed', row['id'])
        row.update(status='FAILED', note=f'{type(exc).__name__}: {exc}')
        put(row)


def tick():
    global _last_heartbeat
    with chains.LOCK:
        with chains.db() as d:rows=[json.loads(x['data']) for x in d.execute('SELECT data FROM runs')]
        for row in rows:
            if row['status'] not in ('RUNNING','CANCELLING'):continue
            advance_safely(row)
        # Reuse the real schedule ledger, retry policy and heartbeat, scoped to owned chains.
        schedules=chains.schedule_store()
        if time.monotonic()-_last_heartbeat>=15:
            schedules.heartbeat({'checked_at':time.time(),'status':'ok','pid':os.getpid(),'mode':'isolated_chain_worker'})
            _last_heartbeat=time.monotonic()
        for row in rows:
            sid=row.get('schedule_run_id')
            if not sid:continue
            ledger=schedules.run(sid)
            current=get(row['id'])
            if not ledger or ledger['status'] not in ('running','starting'):continue
            if ledger.get('cancel_requested') and current['status']=='RUNNING':cancel(row['id'])
            if current['status'] in ('RUNNING','CANCELLING'):schedules.renew(sid)
            else:
                status={'DONE':'success','NO_CONTENT':'success','CANCELLED':'cancelled','INTERRUPTED':'interrupted'}.get(current['status'],'failed')
                schedules.finish(sid,status,error=current.get('note',''),output_path=current.get('report'))
        armed={m['job']:m for m in chains.all_chains() if m.get('schedule_armed')}
        available={job for job in armed if not chains.active_for_job(job) and not TaskRegistry().is_running(job)}
        schedules.enqueue_due(job_names=available)
        execution=schedules.claim(job_names=available)
        if execution and schedules.start(execution['id'],os.getpid()):
            try:
                previous=next((r for r in reversed(rows) if r.get('schedule_run_id')==execution['id'] and r.get('batch_id')),None)
                if previous:
                    resumed=retry(previous['id'],{'username':'chain-scheduler','role':'admin'})
                else:
                    context=execution.get('context') or {}
                    if isinstance(context,str):context=json.loads(context)
                    resumed=start(armed[execution['job_name']]['key'],{'limit':0,'schedule_run_id':execution['id'],'run_context':context},
                                  {'username':'chain-scheduler','role':'admin'})
            except Exception as exc:
                schedules.finish(execution['id'],'failed',str(exc));logger.exception('scheduled chain failed')

def recover_owned():
    # Only this workbench's batches are resumed, never copied historic work.
    from web.services import video_render
    with chains.db() as d:rows=[json.loads(r['data']) for r in d.execute('SELECT data FROM runs')]
    for row in rows:
        if row['status'] not in ('RUNNING','CANCELLING') or not row.get('batch_id'):continue
        for item in (ap.get_run(row['batch_id']) or {}).get('items',[]):
            sid=item.get('script_id')
            if not sid:continue
            script=store.get_script(sid) or {}
            if script.get('status') in ('RESEARCHING','WRITING'):
                store.update_script(sid,status='FAILED',error='隔离应用重启时写稿中断，正在从该步骤恢复')
            for render in store.list_renders(sid):
                if render['status'] in ('SUBMITTING','QUEUED','RUNNING','DOWNLOADING'):video_render.start(render['id'])

def start_loop():
    recover_owned()
    def work():
        while not STOP.wait(3):
            try:tick()
            except Exception:logger.exception('chain worker tick failed')
    threading.Thread(target=work,name='isolated-chain-worker',daemon=True).start()

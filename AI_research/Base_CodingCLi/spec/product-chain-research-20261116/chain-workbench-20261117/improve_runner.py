from pathlib import Path
root=Path(__file__).parent/'runtime'
p=root/'core/schedule_store.py';s=p.read_text().replace('def enqueue_due(self, now: float | None = None) -> int:', 'def enqueue_due(self, now: float | None = None, *, job_names: set[str] | None = None) -> int:')
s=s.replace("                active = db.execute(\"SELECT 1 FROM schedule_runs", "                if job_names is not None and plan['job_name'] not in job_names:\n                    continue\n                active = db.execute(\"SELECT 1 FROM schedule_runs")
s=s.replace('def claim(self, now: float | None = None, concurrency: int = 2) -> dict | None:', 'def claim(self, now: float | None = None, concurrency: int = 2, *, job_names: set[str] | None = None) -> dict | None:')
a=s.index('            active = db.execute("SELECT count(*)',s.index('    def claim('));b=s.index('            if not row:',a)
s=s[:a]+'''            allowed=list(job_names) if job_names is not None else None
            if allowed==[]:return None
            scope=(" AND job_name IN ("+','.join('?' for _ in allowed)+")") if allowed is not None else ''
            active=db.execute("SELECT count(*) FROM schedule_runs WHERE status IN ('starting','running')"+scope,allowed or []).fetchone()[0]
            if active>=concurrency:return None
            scope=(" AND r.job_name IN ("+','.join('?' for _ in allowed)+")") if allowed is not None else ''
            row=db.execute("""SELECT r.* FROM schedule_runs r JOIN schedules s ON s.id=r.schedule_id
                WHERE r.status IN ('queued','retry') AND r.ready_at<=? AND s.enabled=1 AND s.deleted=0
                AND (r.version=s.version OR json_extract(r.context,'$.issue_id') IS NOT NULL)"""+scope+
                " ORDER BY r.ready_at,r.created_at LIMIT 1",[now]+(allowed or [])).fetchone()
'''+s[b:];p.write_text(s)
p=root/'web/runner/executor.py';s=p.read_text().replace('flash_triggered_at: float | None = None):','flash_triggered_at: float | None = None, run_context: dict | None = None):').replace('flash_triggered_at: float | None = None) -> RunningTask:', 'flash_triggered_at: float | None = None, run_context: dict | None = None) -> RunningTask:')
s=s.replace('engine.run_context = {"flash_triggered_at": flash_triggered_at}', 'engine.run_context = {"flash_triggered_at": flash_triggered_at, **(run_context or {})}')
s=s.replace('verbose, flash_triggered_at=flash_triggered_at)', 'verbose, flash_triggered_at=flash_triggered_at, run_context=run_context)');p.write_text(s)
p=root/'web/services/chain_runner.py';s=p.read_text().replace('import copy,json,logging,threading,time,uuid','import copy,json,logging,threading,time,uuid,os')
s=s.replace("    return row\n\ndef put", "    if row.get('task_id'):\n        from web.runner.log_store import read_events\n        _,events=read_events(chains.paths().logs_dir,row['job'],row['task_id'],limit=8)\n        row['progress']=events\n    return row\n\ndef put",1)
s=s.replace("row.pop('batch',None);chains.save_run(row)", "row.pop('batch',None);row.pop('progress',None);chains.save_run(row)")
s=s.replace("flash_triggered_at=time.time())", "flash_triggered_at=time.time(),run_context=body.get('run_context'))")
s=s.replace("'note':'正在获取信息源并生成研究报告','attempt':1,'events':[]}", "'note':'正在获取信息源并生成研究报告','attempt':1,'events':[],'schedule_run_id':body.get('schedule_run_id')}")
a=s.index('        # Only explicitly armed chains');b=s.index('\ndef start_loop():',a)
s=s[:a]+'''        # Reuse the real schedule ledger, retry policy and heartbeat, scoped to owned chains.
        schedules=chains.schedule_store()
        schedules.heartbeat({'checked_at':time.time(),'status':'ok','pid':os.getpid(),'mode':'isolated_chain_worker'})
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
'''+s[b:];p.write_text(s)
p=Path(__file__).parent/'app.py';s=p.read_text().replace("managed_scheduler.ensure_online=lambda:{'online':True,'mode':'isolated_chain_worker'}", "from core.schedule_store import ScheduleStore\nmanaged_scheduler.ensure_online=lambda:ScheduleStore(str(ROOT/'state')).health()")
s=s.replace("schedule_host.install=lambda:{'online':True,'mode':'isolated_chain_worker'}", "schedule_host.install=managed_scheduler.ensure_online")
s=s.replace("schedule_host.status=lambda *_a,**_k:{'online':True,'installed':False,'detail':'隔离应用内链路执行器；不安装系统调度'}", "schedule_host.status=lambda *_a,**_k:{**managed_scheduler.ensure_online(),'installed':False,'detail':'隔离应用内链路执行器；不安装系统调度'}")
p.write_text(s)

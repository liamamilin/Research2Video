"""Repair only this trial's false local DB-lock failures against live renderer truth."""
from pathlib import Path
import sqlite3,json,httpx,sys
ROOT=Path(__file__).resolve().parent/'runtime';sys.path.insert(0,str(ROOT))
from core.video import store
store.use_state_dir(str(ROOT/'state'))
ids=('62c52c3f58044da6be09ecfe2de81c31','f021f19f491f47cebe1b941599b4b3f7')
changes=[]
with httpx.Client(base_url='http://127.0.0.1:9253',timeout=90,trust_env=False) as c:
 for rid in ids:
  row=store.get_render(rid)
  if row['status']!='FAILED' or 'database is locked' not in (row.get('error_message') or ''):continue
  response=c.get('/api/jobs/'+row['autovideo_job_id']);response.raise_for_status();job=response.json()
  if 'job' in job:job=job['job']
  state=job.get('status');print(rid,state,flush=True)
  if state not in ('QUEUED','RUNNING','COMPLETED'):continue
  # Retain the oldest remote task. Duplicates caused by the false failure are queued only.
  for other in store.list_renders(row['script_id']):
   if other['id']==rid or other['created_at']<=row['created_at'] or other['status'] not in store.ACTIVE_RENDER_STATUSES:continue
   remote=c.get('/api/jobs/'+other['autovideo_job_id']);remote.raise_for_status();actual=remote.json();actual=actual.get('job',actual)
   if actual['status']!='QUEUED':raise RuntimeError('Duplicate no longer queued; inspect before cancelling')
   cancelled=c.post('/api/jobs/'+other['autovideo_job_id']+'/cancel');cancelled.raise_for_status()
   store.update_render(other['id'],status='CANCELLED',error_code='ISOLATED_RECONCILIATION',error_message='保留仍在执行的原任务，撤销本地误报引起的重复排队')
   changes.append({'duplicate_cancelled':other['id'],'remote':other['autovideo_job_id']})
  store.update_render(rid,status='RUNNING' if state in ('RUNNING','COMPLETED') else 'QUEUED',error_code=None,error_message=None,completed_at=None)
  changes.append({'restored':rid,'verified_remote_status':state,'remote':row['autovideo_job_id']})
(ROOT/'evidence/db-lock-render-reconciliation.json').write_text(json.dumps(changes,ensure_ascii=False,indent=2))

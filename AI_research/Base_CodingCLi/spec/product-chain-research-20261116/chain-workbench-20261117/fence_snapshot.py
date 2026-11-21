"""Copied in-flight rows are history, not live work in this isolated process."""
from pathlib import Path
import sqlite3,json,time,datetime
HERE=Path(__file__).resolve().parent
ROOT=HERE/'runtime'
proof=ROOT/'evidence/inherited-state-fence.json'
if not proof.exists():
 cutoff=(HERE/'baseline.json').stat().st_mtime
 changes=[]
 with sqlite3.connect(ROOT/'state/schedules.db') as db:
  rows=db.execute("select id,status from schedule_runs where created_at<? and status in ('queued','starting','running','retry')",(cutoff,)).fetchall()
  for rid,status in rows:
   changes.append({'database':'schedules','id':rid,'from':status,'to':'interrupted'})
   db.execute("update schedule_runs set status='interrupted',finished_at=?,pid=NULL,lease_until=NULL,error='这是隔离快照中的历史运行，不代表本环境正在执行' where id=?",(time.time(),rid))
 with sqlite3.connect(ROOT/'state/video.db') as db:
  for table,active,target in [('scripts',('WRITING','RESEARCHING'),'FAILED'),('renders',('SUBMITTING','QUEUED','RUNNING','DOWNLOADING'),'CANCELLED'),('autopilot_runs',('RUNNING',),'CANCELLED')]:
   rows=db.execute(f"select id,status,created_at from {table} where status in ({','.join('?' for _ in active)})",active).fetchall()
   for rid,status,created in rows:
    try:old=datetime.datetime.fromisoformat(created).timestamp()<cutoff
    except (ValueError,TypeError):old=False
    if not old:continue
    changes.append({'database':'video','table':table,'id':rid,'from':status,'to':target})
    db.execute(f'update {table} set status=? where id=?',(target,rid))
 proof.write_text(json.dumps({'cutoff':cutoff,'changes':changes},ensure_ascii=False,indent=2))
 print('隔离快照中的历史运行已标记',len(changes))

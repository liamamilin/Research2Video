"""Read-only progress monitor for the four explicitly selected acceptance scripts."""
from pathlib import Path
import json,re,sqlite3,time
ROOT=Path(__file__).resolve().parent/'runtime'
SCRIPTS={'ceaaf17bc5ff41849878b1159971c99e':'Flash 合集','115d4ebe6c1648dc8b2ede59a2e6542a':'Flash 单条','9541c940e2004129ab0a4288edfd822d':'新链路单条','4fd059213d4643cf9ea0d39b00e72687':'新链路合集'}
def tail(path):
    if not path.is_file():return ''
    with path.open('rb') as stream:
        stream.seek(0,2);stream.seek(max(0,stream.tell()-4096))
        return stream.read().decode('utf-8',errors='replace')
def inspect():
    result=[]
    with sqlite3.connect(f'file:{ROOT / "state/video.db"}?mode=ro',uri=True,timeout=10) as db:
        db.row_factory=sqlite3.Row
        for sid,name in SCRIPTS.items():
            rows=[dict(r) for r in db.execute('SELECT id,status,active_stage,autovideo_job_id,error_message,created_at FROM renders WHERE script_id=? ORDER BY created_at DESC,id DESC',(sid,))]
            active=[r for r in rows if r['status'] in ('QUEUED','RUNNING','SUBMITTING','DOWNLOADING')]
            done=[r for r in rows if r['status']=='COMPLETED']
            row=(done or active or rows or [{}])[0]
            item={'name':name,'script_id':sid,**row}
            job=row.get('autovideo_job_id')
            if job and row.get('status')=='RUNNING':
                log=ROOT/'renderer/g3/data/jobs'/job/'attempts/1/g2-run/logs/remotion-render.log'
                frames=re.findall(r'Rendered (\d+)/(\d+)(?:, time remaining: ([^\n\r]+))?',tail(log))
                if frames:item['frames'],item['total_frames'],item['remaining']=frames[-1]
                worker=tail(ROOT/'renderer/g3/data/jobs'/job/'attempts/1/worker.log').splitlines()
                if worker:item['worker_last']=worker[-1]
            result.append(item)
    return result
previous=None
while True:
    try:
        data=inspect(); payload=json.dumps(data,ensure_ascii=False)
        if payload!=previous:
            print(time.strftime('%H:%M:%S'),payload,flush=True)
            with (ROOT/'evidence/end-to-end-watch.jsonl').open('a') as out:out.write(json.dumps({'at':time.strftime('%Y-%m-%d %H:%M:%S'),'outputs':data},ensure_ascii=False)+'\n')
            previous=payload
        if all(r.get('status')=='COMPLETED' for r in data):break
    except (OSError,sqlite3.Error) as exc:print(type(exc).__name__,str(exc),flush=True)
    time.sleep(40)

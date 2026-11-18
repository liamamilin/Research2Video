"""Read-only watcher for the two real native-entry acceptance videos."""
from pathlib import Path
import json
import sqlite3
import time

SOURCE=Path(__file__).resolve().parents[3]
ROOT=SOURCE/'state/validation/chain-production-integration-20261118/native'
SCRIPTS=['0047a0e7e1a647cf842d1a11464a1a61','e0772bab9227470494a27eab80b7d2a3']
previous=None
with sqlite3.connect((ROOT/'state/video.db').resolve().as_uri()+'?mode=ro',uri=True,timeout=5) as db:
    db.row_factory=sqlite3.Row
    while True:
        rows=[]
        for script in SCRIPTS:
            source=db.execute('SELECT id,status,stage FROM scripts WHERE id=?',(script,)).fetchone()
            render=db.execute('SELECT id,status,active_stage,autovideo_job_id,error_message FROM renders WHERE script_id=? ORDER BY created_at DESC LIMIT 1',(script,)).fetchone()
            rows.append({'script':dict(source) if source else None,'render':dict(render) if render else None})
        text=json.dumps(rows,ensure_ascii=False)
        if text!=previous:
            row={'time':time.strftime('%Y-%m-%d %H:%M:%S'),'rows':rows}
            print(json.dumps(row,ensure_ascii=False),flush=True)
            with (ROOT/'evidence/native-progress.jsonl').open('a') as stream:stream.write(json.dumps(row,ensure_ascii=False)+'\n')
            previous=text
        if all(item['render'] and item['render']['status']=='COMPLETED' for item in rows):
            break
        time.sleep(40)

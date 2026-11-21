from pathlib import Path
import sqlite3,json,time
root=Path(__file__).resolve().parent/'runtime'
statements=[
 'CREATE INDEX IF NOT EXISTS idx_scripts_chain_summary ON scripts(production,created_at DESC,id,status,current_version,archived)',
 'CREATE INDEX IF NOT EXISTS idx_script_versions_summary ON script_versions(script_id,version,title)',
]
started=time.monotonic()
with sqlite3.connect(root/'state/video.db',timeout=120) as db:
    for statement in statements:
        for attempt in range(15):
            try:db.execute(statement);db.commit();break
            except sqlite3.OperationalError as exc:
                if 'locked' not in str(exc).lower() or attempt==14:raise
                print('Waiting for current isolated schema write',flush=True);time.sleep(2)
        print('Installed',statement.split(' ON ')[0],flush=True)
    for name in ('idx_scripts_chain_summary','idx_script_versions_summary'):
        db.execute('ANALYZE '+name)
    db.commit()
    query='SELECT s.id,s.status,s.current_version,s.archived,v.title FROM scripts s LEFT JOIN script_versions v ON v.script_id=s.id AND v.version=s.current_version WHERE s.production IN (?) ORDER BY s.created_at DESC LIMIT 100'
    plan=db.execute('EXPLAIN QUERY PLAN '+query,('flash_each',)).fetchall()
result={'seconds':round(time.monotonic()-started,3),'plan':plan}
(root/'evidence/chain-summary-indexes.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(result,flush=True)

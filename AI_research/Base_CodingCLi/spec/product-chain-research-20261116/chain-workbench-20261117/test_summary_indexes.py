import sqlite3
with sqlite3.connect(':memory:') as db:
    db.executescript('''CREATE TABLE scripts(id TEXT PRIMARY KEY,production TEXT,created_at TEXT,topic TEXT,status TEXT,current_version INTEGER,archived INTEGER);
CREATE TABLE script_versions(script_id TEXT,version INTEGER,title TEXT,sections TEXT,PRIMARY KEY(script_id,version));
CREATE INDEX idx_scripts_chain_summary ON scripts(production,created_at DESC,id,status,current_version,archived);
CREATE INDEX idx_script_versions_summary ON script_versions(script_id,version,title);''')
    db.executemany('INSERT INTO scripts VALUES (?,?,?,?,?,?,?)',[(str(i),'one' if i%2 else 'two',str(i).zfill(3),'x'*100000,'APPROVED',1,0) for i in range(120)])
    db.executemany('INSERT INTO script_versions VALUES (?,?,?,?)',[(str(i),1,'标题'+str(i),'x'*100000) for i in range(120)])
    query='SELECT s.id,s.status,s.current_version,s.archived,v.title FROM scripts s LEFT JOIN script_versions v ON v.script_id=s.id AND v.version=s.current_version WHERE s.production IN (?,?) ORDER BY s.created_at DESC LIMIT 100'
    db.execute('ANALYZE')
    plan=db.execute('EXPLAIN QUERY PLAN '+query,('one','two')).fetchall()
    rows=db.execute(query,('one','two')).fetchall()
    assert len(rows)==100 and rows[0][0]=='119' and rows[-1][0]=='20'
    assert any('COVERING INDEX idx_scripts_chain_summary' in r[-1] for r in plan),plan
    assert any('COVERING INDEX idx_script_versions_summary' in r[-1] for r in plan),plan
    print('PASS: history list ordering and titles preserved; both sides use covering indexes',plan)

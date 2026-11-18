from pathlib import Path
import sys,sqlite3
sys.path.insert(0,str(Path(__file__).resolve().parent/'runtime'))
from core.video import reports
with sqlite3.connect(':memory:') as db:
    db.execute('CREATE TABLE scripts(id TEXT PRIMARY KEY,report_path TEXT,research_job TEXT,topic TEXT,created_by TEXT,created_at TEXT,report_id TEXT)')
    db.execute('CREATE TABLE topic_imports(report_path TEXT)')
    db.execute("INSERT INTO scripts VALUES ('one','output/one.md','job/one','{}','test','2026-11-17',NULL)")
    reports.init(db)
    first=db.execute("SELECT report_id FROM scripts WHERE id='one'").fetchone()[0]
    assert first
    db.execute("INSERT INTO scripts VALUES ('two','output/two.md','job/two','{}','test','2026-11-18','')")
    reports.init(db)
    assert db.execute('SELECT COUNT(*) FROM research_reports').fetchone()[0]==2
    assert db.execute("SELECT report_id FROM scripts WHERE id='one'").fetchone()[0]==first
    query="SELECT id,report_path,research_job,topic,created_by FROM scripts WHERE report_path != '' AND (report_id IS NULL OR report_id = '') ORDER BY created_at"
    plan=db.execute('EXPLAIN QUERY PLAN '+query).fetchall()
    assert any('idx_scripts_missing_reports' in str(row) for row in plan),plan
    print('PASS: backfill remains correct and later startup uses missing-report partial index',plan)

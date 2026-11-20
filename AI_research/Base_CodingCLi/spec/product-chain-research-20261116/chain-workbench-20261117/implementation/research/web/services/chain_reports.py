"""Keep report lists/search attached to the real producing Job."""
from pathlib import Path
import json,logging
from web.settings import get_settings
from web.indexer import db,scanner
logger=logging.getLogger(__name__)
def relative_path(report):
    root=Path(get_settings().paths.output_dir).resolve()
    path=Path(report)
    if not path.is_absolute():path=root/str(path).removeprefix('output/')
    resolved=path.resolve()
    if not resolved.is_relative_to(root):raise ValueError('报告路径不在输出目录中')
    return str(resolved.relative_to(root))

def record(job,report):
    root=Path(get_settings().paths.output_dir)
    rel=relative_path(report)
    file=root/rel
    if not file.is_file():return
    stat=file.stat()
    db.upsert_report(path=rel,mtime=stat.st_mtime,size_bytes=stat.st_size,job_name=job,
                     title=scanner.extract_title(str(file)),category=Path(rel).parts[0],
                     content=file.read_text(errors='replace')[:scanner.MAX_INDEX_BYTES])
def reconcile():
    from core.video import store,topic_store
    from web.services import chains
    sources=topic_store.sources(get_settings().paths.jobs_dir)
    with store.connect() as connection:
        imported=[dict(r) for r in connection.execute('SELECT report_path,source FROM topic_imports')]
        research=[dict(r) for r in connection.execute("SELECT report_path,job FROM research_reports WHERE job!=''")]
    mapping={str(r['report_path']).removeprefix('output/'):sources[r['source']].job for r in imported if r['source'] in sources}
    mapping.update({str(r['report_path']).removeprefix('output/'):r['job'] for r in research})
    with db.connect() as connection:
        connection.executemany('UPDATE reports SET job_name=? WHERE path=? AND job_name!=?',[(job,path,job) for path,job in mapping.items()])
    with chains.db() as connection:rows=[json.loads(r['data']) for r in connection.execute('SELECT data FROM runs')]
    for row in rows:
        if row.get('report'):record(row['job'],row['report'])

"""Keep report lists/search attached to the real producing Job."""

from pathlib import Path
import logging
from web.settings import get_settings
from web.indexer import db, scanner

logger = logging.getLogger(__name__)


def relative_path(report):
    root = Path(get_settings().paths.output_dir).resolve()
    path = Path(report)
    if not path.is_absolute():
        path = root / str(path).removeprefix("output/")
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("报告路径不在输出目录中")
    return str(resolved.relative_to(root))


def record(job, report):
    root = Path(get_settings().paths.output_dir)
    rel = relative_path(report)
    file = root / rel
    if not file.is_file():
        return
    stat = file.stat()
    db.upsert_report(
        path=rel,
        mtime=stat.st_mtime,
        size_bytes=stat.st_size,
        job_name=job,
        title=scanner.extract_title(str(file)),
        category=Path(rel).parts[0],
        content=file.read_text(errors="replace")[: scanner.MAX_INDEX_BYTES],
    )


def reconcile():
    """Associate only unambiguous producer receipts; preserve user index annotations."""
    from core import report_meta
    from core.config import load_job
    from core.video import store

    candidates = {}
    paths = get_settings().paths

    def add(path, job):
        try:
            rel = relative_path(path)
            canonical = load_job(paths.jobs_dir, job) if job else None
            if canonical:
                candidates.setdefault(rel, set()).add(canonical.get("_file", job))
        except (ValueError, OSError):
            pass

    for meta in report_meta.load_all().values():
        add(meta.get("path", ""), meta.get("job"))
    with store.connect() as connection:
        for row in connection.execute(
            "SELECT report_path,job FROM research_reports WHERE job!=''"
        ):
            add(row["report_path"], row["job"])
    with db.connect() as connection:
        for path, jobs in candidates.items():
            if len(jobs) == 1:
                connection.execute(
                    "UPDATE reports SET job_name=? WHERE path=?",
                    (next(iter(jobs)), path),
                )
    return {
        "updated_candidates": sum(len(j) == 1 for j in candidates.values()),
        "conflicts": [p for p, j in candidates.items() if len(j) > 1],
    }

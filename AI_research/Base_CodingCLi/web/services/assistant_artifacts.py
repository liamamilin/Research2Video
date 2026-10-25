"""Translate verified engine output files into canonical report references."""
from pathlib import Path
import sqlite3
from urllib.parse import quote
from web.settings import get_settings
from web.indexer import db


def report_reference(value: str | None) -> dict | None:
    if not value:
        return None
    settings = get_settings()
    root = Path(settings.paths.output_dir).resolve()
    workspace = Path(settings.paths.config_dir).resolve().parent
    raw = Path(value)
    candidates = [raw] if raw.is_absolute() else [workspace / raw, root / raw]
    for candidate in candidates:
        path = candidate.resolve()
        if path.is_file() and path.is_relative_to(root) and path.suffix in ('.md', '.mdx'):
            relative = path.relative_to(root).as_posix()
            try:
                indexed = db.get_report(relative) is not None
            except sqlite3.Error:
                indexed = False
            return {'path': relative, 'href': '/reports/view?path=' + quote(relative, safe=''),
                    'report_date': db.report_date_for(relative, path.stat().st_mtime),
                    'verified_file': True, 'indexed': indexed}
    return None

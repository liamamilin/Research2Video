"""How much the records hold, and removing the old ones. Nothing is removed unless asked: there is no schedule.

Only ``calls`` and ``spans`` are pruned. ``meta`` stays, so a finished render or an old log already read is not read
again (and counted a second time) after its rows are gone.
"""
from __future__ import annotations

import os
import time
from typing import Optional

from core import sqlite_util
from core.telemetry import store

DAY = 86400.0
MIN_DAYS = 1


def stats() -> dict:
    """Row counts, the time range they cover, and the size of the database file."""
    path = store.db_path()
    with store.connect() as conn:
        if conn is None:
            return {"enabled": False}
        calls = conn.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM calls").fetchone()
        spans = conn.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM spans").fetchone()
    size = sum(os.path.getsize(p) for p in (path, f"{path}-wal") if os.path.exists(p))
    times = [t for t in (calls[1], spans[1]) if t]
    return {"enabled": True, "calls": calls[0], "spans": spans[0], "oldest": min(times) if times else None,
            "newest": max([t for t in (calls[2], spans[2]) if t], default=None), "bytes": size}


def purge(older_than_days: float, *, dry_run: bool = False, now: Optional[float] = None) -> dict:
    """Delete calls and spans that happened more than ``older_than_days`` ago; with ``dry_run`` only count them."""
    if older_than_days < MIN_DAYS:
        raise ValueError(f"keep at least {MIN_DAYS} day of records")
    cutoff = (now if now is not None else time.time()) - older_than_days * DAY
    with store.connect(write=not dry_run) as conn:
        if conn is None:
            return {"enabled": False, "cutoff": cutoff, "calls": 0, "spans": 0, "dry_run": dry_run}
        if dry_run:
            found = {name: conn.execute(f"SELECT COUNT(*) FROM {name} WHERE ts < ?", (cutoff,)).fetchone()[0]
                     for name in ("calls", "spans")}
        else:
            found = {name: conn.execute(f"DELETE FROM {name} WHERE ts < ?", (cutoff,)).rowcount
                     for name in ("calls", "spans")}
    if not dry_run and any(found.values()):
        _shrink()
    return {"enabled": True, "cutoff": cutoff, "dry_run": dry_run, **found}


def _shrink() -> None:
    """Give the freed pages back to the disk. Best effort: the rows are already gone either way."""
    path = store.db_path()
    try:
        conn = sqlite_util.open_db(path)
        try:
            conn.isolation_level = None
            conn.execute("VACUUM")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        pass

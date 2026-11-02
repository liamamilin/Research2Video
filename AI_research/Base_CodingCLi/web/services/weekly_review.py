"""Gathers what the weekly review needs — published workflow videos, their snapshots and the scripts behind them —
and saves each week's report and numbers once they are final (core/weekly_review_store.py)."""
from __future__ import annotations

import logging
import time
from typing import Optional

from core.telemetry import weekly_review
from core.weekly_review_store import STAGES, ReviewStore
from web.services import bilibili, platform_analytics

logger = logging.getLogger(__name__)


def _form(script: dict) -> str:
    # The script records how it was written; the production config may have changed since.
    return "整期节目" if (script.get("topic") or {}).get("content_mode") == "episode" else "单篇"


def _gather(config_dir: str, collector, uid: str) -> list[dict]:
    """Every published workflow video with its snapshots and the attributes the review cuts by."""
    from core.video import content_columns, store as video_store

    links = platform_analytics.workflow_links()
    rows = collector.storage.videos(uid, workflow=links, scope="workflow", size=100000)["items"]
    first = min((r["pubdate"] for r in rows if r.get("pubdate")), default=time.time())
    series = collector.storage.view_series(uid, [r["bvid"] for r in rows], since=weekly_review.week_start(first) - 7 * 86400)
    columns = {key: c.name for key, c in content_columns.load_all(config_dir).items()}
    videos = []
    for row in rows:
        publication = row.get("publication") or {}
        script = video_store.get_script(publication.get("script_id") or "") or {}
        column = script.get("content_column_id") or ""
        fields = (script.get("topic") or {}).get("fields") or {}
        available = bool(row.get("available"))
        videos.append({"bvid": row["bvid"], "title": row.get("title") or "", "pubdate": row["pubdate"],
                       "duration": row.get("duration") or 0,
                       "view": (row.get("metrics") or {}).get("view") if available else None,
                       "metrics": (row.get("metrics") or {}) if available else {},
                       "snapshots": series.get(row["bvid"], []), "form": _form(script),
                       "matrix": publication.get("matrix_name") or publication.get("matrix") or "",
                       # before columns existed: the Job it came from (production config names repeat across Jobs)
                       "column": columns.get(column, column) or publication.get("job_name") or "",
                       "structure": weekly_review.structure(str(fields.get("推荐结构") or ""))})
    return videos


def load(config_dir: str, state_dir: str, start: Optional[float] = None, stage: str = "",
         now: Optional[float] = None) -> dict:
    """The week starting ``start`` (default: the last full week): computed now, or as saved at ``stage``."""
    now = time.time() if now is None else now
    try:
        collector, uid = platform_analytics.current()
    except bilibili.BilibiliError:
        return {"connected": False}
    start = weekly_review.week_start(start) if start is not None else weekly_review.week_start(now) - 7 * 86400
    store = ReviewStore(state_dir)
    saved = [s for s in store.saved() if s["start"] == start]
    weeks = [weekly_review.week_start(now)] + _weeks_since(now)
    if stage:
        kept = store.get(start, stage)
        if kept is None:
            return {"connected": True, "missing": True, "start": start, "weeks": weeks, "saved": saved}
        return {"connected": True, **kept, "weeks": weeks, "saved": saved}
    return {"connected": True, "weeks": weeks, "saved": saved, "stage": "",
            **weekly_review.review(_gather(config_dir, collector, uid), start, now)}


def archive_due(config_dir: str, state_dir: str, now: Optional[float] = None) -> list[tuple[float, str]]:
    """Save every week and stage whose figures are final and that is not saved yet, oldest first; what was saved.

    Run after each collection, so a week is saved soon after it is final; a week missed while the console was off is
    saved the next time, computed from the snapshots then (its file says when)."""
    now = time.time() if now is None else now
    store = ReviewStore(state_dir)
    have = {(s["start"], s["stage"]) for s in store.saved()}
    collector = uid = videos = None
    done = []
    for start in sorted(_weeks_since(now)):
        for stage, _, days in STAGES:
            if now < start + days * 86400 or (start, stage) in have:
                continue
            if videos is None:
                try:
                    collector, uid = platform_analytics.current()
                except bilibili.BilibiliError:
                    return done
                videos = _gather(config_dir, collector, uid)
            # a week with nothing published is saved too: that is part of the history, and it is not looked at again
            if store.save(weekly_review.review(videos, start, now), stage, now):
                done.append((start, stage))
    return done


def _weeks_since(now: float) -> list[float]:
    """Weeks that may need saving: from the first Bilibili publication's week (read cheaply) to last week."""
    import sqlite3
    from datetime import datetime
    from pathlib import Path

    from core.video import store as video_store
    path = Path(video_store.db_path())
    if not path.is_file():
        return []
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2) as db:
            first = db.execute("SELECT min(published_on) FROM publications WHERE platform='bilibili'").fetchone()[0]
        first = datetime.strptime(first, "%Y-%m-%d").timestamp() if first else None
    except (sqlite3.Error, ValueError):
        return []
    if first is None:
        return []
    weeks, week = [], weekly_review.week_start(now) - 7 * 86400
    while week >= weekly_review.week_start(first):
        weeks.append(week)
        week = weekly_review.week_start(week - 86400)
    return weeks


def archive_after_collection(state_dir: str) -> None:
    """Called after a successful collection run; never lets the review break collection."""
    try:
        from web.settings import get_settings
        saved = archive_due(str(get_settings().paths.config_dir), state_dir)
        for start, stage in saved:
            logger.info("Weekly review saved: week of %s, %s", time.strftime("%Y-%m-%d", time.localtime(start)), stage)
    except Exception:  # noqa: BLE001
        logger.exception("Weekly review could not be saved")

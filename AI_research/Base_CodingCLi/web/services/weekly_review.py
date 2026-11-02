"""Gathers what the weekly review needs: published workflow videos, their snapshots and the scripts behind them."""
from __future__ import annotations

import time
from typing import Optional

from core.telemetry import weekly_review
from web.services import bilibili, platform_analytics


def _form(script: dict) -> str:
    # The script records how it was written; the production config may have changed since.
    return "整期节目" if (script.get("topic") or {}).get("content_mode") == "episode" else "单篇"


def load(config_dir: str, start: Optional[float] = None, now: Optional[float] = None) -> dict:
    """The review of the week starting ``start`` (default: the last full week), plus the weeks there is data for."""
    from core.video import content_columns, store as video_store

    now = time.time() if now is None else now
    try:
        collector, uid = platform_analytics.current()
    except bilibili.BilibiliError:
        return {"connected": False}
    this_week = weekly_review.week_start(now)
    start = weekly_review.week_start(start) if start is not None else this_week - 7 * 86400
    links = platform_analytics.workflow_links()
    rows = collector.storage.videos(uid, workflow=links, scope="workflow", size=100000)["items"]
    first = min((r["pubdate"] for r in rows if r.get("pubdate")), default=now)
    rows = [r for r in rows if start - 7 * 86400 <= (r.get("pubdate") or 0) < start + 7 * 86400]
    series = collector.storage.view_series(uid, [r["bvid"] for r in rows], since=start - 7 * 86400)
    columns = {key: c.name for key, c in content_columns.load_all(config_dir).items()}
    videos = []
    for row in rows:
        publication = row.get("publication") or {}
        script = video_store.get_script(publication.get("script_id") or "") or {}
        column = script.get("content_column_id") or ""
        fields = (script.get("topic") or {}).get("fields") or {}
        videos.append({"bvid": row["bvid"], "title": row.get("title") or "", "pubdate": row["pubdate"],
                       "duration": row.get("duration") or 0,
                       "view": (row.get("metrics") or {}).get("view") if row.get("available") else None,
                       "snapshots": series.get(row["bvid"], []), "form": _form(script),
                       "matrix": publication.get("matrix_name") or publication.get("matrix") or "",
                       # before columns existed: the Job it came from (production config names repeat across Jobs)
                       "column": columns.get(column, column) or publication.get("job_name") or "",
                       "structure": weekly_review.structure(str(fields.get("推荐结构") or ""))})
    weeks = []
    week = this_week
    while week >= weekly_review.week_start(first):
        weeks.append(week)
        week = weekly_review.week_start(week - 86400)
    return {"connected": True, "weeks": weeks, **weekly_review.review(videos, start, now)}

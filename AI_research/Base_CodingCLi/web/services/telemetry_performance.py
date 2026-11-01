"""Joins the Bilibili play counts the platform collector keeps with the cost records of the scripts behind them."""
from __future__ import annotations

import time

from core.telemetry import performance
from web.services import bilibili, platform_analytics


def load(config_dir: str, by: str, days: int) -> dict:
    """The cost-versus-plays report for videos published in the last ``days`` days (0 = all)."""
    try:
        collector, uid = platform_analytics.current()
    except bilibili.BilibiliError:
        return {"enabled": True, "connected": False, "by": by, "items": [], "groups": []}
    links = platform_analytics.workflow_links()
    videos = collector.storage.videos(uid, workflow=links, scope="workflow", size=100000)["items"]
    if days > 0:
        since = time.time() - days * 86400
        videos = [v for v in videos if (v.get("pubdate") or 0) >= since]
    return {"connected": True, **performance.report(config_dir, videos, by)}

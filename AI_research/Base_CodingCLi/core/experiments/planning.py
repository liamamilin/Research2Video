"""How long an experiment would run, from the last 14 days of real output (spec §8「产量起伏」「要跑多久」). Read-only."""
from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from pathlib import Path

from core.experiments import hook
from core.experiments.definition import Experiment

WINDOW_DAYS = 14
_BV = re.compile(r"BV[0-9A-Za-z]{10}")
#: which watch reading carries each primary metric
_KEY = {"completion_adjusted": "completion", "completion": "completion", "exit3s": "exit3s", "cover_pass": "cover_pass"}


def _ro(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    return db


def estimate(state_dir: str | Path, experiment: Experiment, now: float | None = None) -> dict:
    """Videos per day the experiment could reach, the share with the primary metric, and the days to the end point."""
    now = now or time.time()
    state_dir = Path(state_dir)
    video_db, analytics_db = state_dir / "video.db", state_dir / "platform_analytics.db"
    if not video_db.is_file():
        return {"per_day": 0.0, "coverage": None, "days": None, "window_days": WINDOW_DAYS}
    since = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now - WINDOW_DAYS * 86400))
    fresh = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now - 3 * 86400))
    with _ro(video_db) as db:
        rows = db.execute("SELECT s.column_key, s.content_context, p.url, p.created_at FROM publications p "
                          "JOIN scripts s ON s.id = p.script_id WHERE p.platform='bilibili' AND s.created_by='autopilot' "
                          "AND p.created_at >= ?", (since,)).fetchall()
    reached, bvids, days = [], [], set()
    for r in rows:
        context = json.loads(r["content_context"] or "{}")
        if not experiment.scope.matches(context, hook.stratum_column(context, r["column_key"])):
            continue
        if experiment.needs_column() and not context.get("column"):
            continue
        reached.append(r)
        days.add((r["created_at"] or "")[:10])
        found = _BV.search(r["url"] or "")
        if found and (r["created_at"] or "") < fresh:  # too new to have any reading yet: not counted for coverage
            bvids.append(found.group())
    per_day = len(reached) / WINDOW_DAYS
    coverage = None
    key = _KEY.get(experiment.metric.primary)
    if bvids and analytics_db.is_file() and key:
        with _ro(analytics_db) as db:
            # per-video readings began on one day (4c926bf): videos published before it were never read in time
            began = db.execute("SELECT MIN(collected_at) FROM analytics_watch").fetchone()[0] or now
            counted = [b for b in bvids if (db.execute("SELECT pubdate FROM analytics_videos WHERE bvid=?", (b,))
                                            .fetchone() or [0])[0] >= began - 86400]
            have = sum(1 for b in counted if any(json.loads(w[0] or "{}").get(key) is not None for w in
                                                 db.execute("SELECT data FROM analytics_watch WHERE bvid=?", (b,))))
        coverage = have / len(counted) if counted else None
    smaller = min(experiment.share_b, 100 - experiment.share_b) / 100
    rate = per_day * smaller * (coverage if coverage is not None else 1.0)
    need = experiment.stop.per_arm
    days_needed = math.ceil(need / rate) if rate > 0 else None
    return {"per_day": round(per_day, 2), "per_arm_per_day": round(rate, 2), "coverage": coverage,
            "days": days_needed, "max_days": experiment.stop.max_days, "window_days": WINDOW_DAYS,
            "stopped_days": WINDOW_DAYS - len(days), "within_max": days_needed is not None and days_needed <= experiment.stop.max_days}

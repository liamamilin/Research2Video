"""Find unprocessed reports without dropping pending work; admission happens downstream."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

from core import report_identity
from . import production, topic_store

SETTLE_SECONDS = 60


def _runs(state_dir: str) -> list[dict]:
    path = Path(state_dir) / "video.db"
    if not path.is_file():
        return []
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2) as db:
        rows = db.execute("SELECT spec,started_at,status FROM autopilot_runs").fetchall()
    result = []
    for raw, started, status in rows:
        try:
            spec = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if isinstance(spec, dict) and spec.get("job") and spec.get("report"):
            result.append({"spec": spec, "started": started, "status": status})
    return result


def _handled(state_dir: str) -> set[tuple[str, str]]:
    """Compatibility view for callers inspecting historical batches."""
    return {(r["spec"]["job"], r["spec"]["report"]) for r in _runs(state_dir)}


def pending(config_dir: str, jobs_dir: str, state_dir: str, root: str, now: float) -> list[dict]:
    """Every report run after opt-in is reconciled separately, including same-day runs.

    Legacy capped batches from today get one uncapped pass to recover omitted
    topics. Earlier completed batches remain historical; new-content revisions
    are always detected. Selection and event memory protect existing videos.
    """
    try:
        runs = _runs(state_dir)
        configs = production.load_all(config_dir, jobs_dir)
    except sqlite3.Error:
        return []
    by_job: dict[str, list] = {}
    for config in configs.values():
        if config.auto and config.usable and config.split in ("each", "whole"):
            by_job.setdefault(config.job, []).append(config)
    out = []
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    for job, mine in by_job.items():
        source = topic_store.resolve(jobs_dir, job) or topic_store.whole_source(jobs_dir, job)
        if source is None:
            continue
        for day, report in reversed(topic_store.all_report_files(source, root)):
            try:
                reported = os.path.getmtime(report)
                revision = report_identity.describe(report, root)["id"]
            except OSError:
                continue
            if now - reported < SETTLE_SECONDS:
                continue
            previous = [r for r in runs if r["spec"]["job"] == job and
                        os.path.abspath(os.path.join(root, r["spec"]["report"])) == os.path.abspath(report)]
            picks = []
            episode_columns = set()
            for config in sorted(mine, key=lambda c: c.key):
                try:
                    enabled_at = config.auto_enabled_at or os.path.getmtime(
                        os.path.join(production.directory(config_dir), f"{config.key}.yaml"))
                except OSError:
                    continue
                if reported <= enabled_at:
                    continue
                handled = False
                for run in previous:
                    spec = run["spec"]
                    selected = spec.get("picks") or []
                    owns = any(p.get("production") == config.key for p in selected)
                    if not owns:
                        continue
                    if spec.get("report_identity") == revision and any(
                            p.get("production") == config.key and p.get("all") for p in selected):
                        handled = True
                    elif not any(p.get("all") for p in selected) and (
                            run["status"] == "RUNNING" or (day < today and reported <= run["started"])):
                        handled = True
                if handled:
                    continue
                if config.content_mode == "episode":
                    if config.content_column_id in episode_columns:
                        continue
                    episode_columns.add(config.content_column_id)
                pick = {"column": config.recipe, "all": True, "production": config.key}
                if config.content_mode == "episode":
                    pick["content_mode"] = "episode"
                if config.split == "whole":
                    pick["whole"] = True
                else:
                    pick["source"] = config.source
                picks.append(pick)
            if picks:
                out.append({"job": job, "report": report, "report_identity": revision, "picks": picks})
    return out


def recommendations_pending(config_dir: str, jobs_dir: str, state_dir: str, now: float | None = None) -> list[dict]:
    """Read-only wakeup/handover for today's recommendations in enabled matrices."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from core import matrices, theme_context
    path = Path(state_dir) / "themes.db"
    if not path.is_file():
        return []
    day = datetime.fromtimestamp(time.time() if now is None else now, ZoneInfo("Asia/Shanghai")).date().isoformat()
    configs = production.load_all(config_dir, jobs_dir)
    owners = matrices.load_all(config_dir)
    from core import production_policy
    policy = production_policy.load(config_dir)
    allowed = {m.key for m in owners.values() if not m.archived and not m.paused and any(
        c.auto and c.usable and c.job in m.jobs for c in configs.values())}
    if policy and not policy.get('automatic', {}).get('recommendations', False):
        allowed -= set(policy.get('matrices', []))
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            rows = [dict(r) for r in db.execute("SELECT id,matrix,category FROM themes WHERE day=? AND script_id=''"
                    " AND used_at=0 AND status IN ('recommended','queued')", (day,))]
        with sqlite3.connect(f"file:{Path(state_dir) / 'video.db'}?mode=ro", uri=True) as db:
            claimed = {r[0] for r in db.execute("SELECT json_extract(topic,'$.theme_task_id') FROM autopilot_items")}
    except sqlite3.Error:
        return []
    categories = {key: {c['key']: c for c in theme_context.categories(config_dir, key, include_legacy=True)}
                  for key in allowed}
    out = []
    for row in rows:
        if row['id'] in claimed or row['matrix'] not in allowed:
            continue
        category = categories[row['matrix']].get(row['category'])
        if not category:
            continue
        bindings = [configs[k] for k in category['production_ids'] if k in configs]
        if bindings and not any(c.auto and c.usable for c in bindings):
            continue
        out.append(row)
    return out

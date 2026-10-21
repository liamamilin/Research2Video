"""Unattended writing by config: which Jobs have a report their auto configs have not written from yet.

A config with ``auto: N`` (core/video/production.py) is written from each new report of its Job, N items, without being
asked. Nothing is stored for "new": a batch of the unattended pipeline records the Job and report it was started for in
its spec, so a report is new while no batch carries its path. Only a report that came out after the config was last
saved counts (the config file's time is when auto was turned on), so turning it on never writes from the report that is
already there, however old; and a report is left alone for a minute after it was last written to, so a Job still
writing its file is not read half done.

This module reads only files and ``video.db`` (stdlib sqlite), so the scheduler process can ask it without the web app.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from . import production, topic_store

#: a report this young may still be being written
SETTLE_SECONDS = 60


def _handled(state_dir: str) -> set[tuple[str, str]]:
    """``(job, report path)`` of every batch the pipeline started for a report. Raises sqlite3.Error when the ledger
    cannot be read: unreadable must never look like nothing was written."""
    path = Path(state_dir) / "video.db"
    if not path.is_file():
        return set()
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2) as db:
        rows = db.execute("SELECT spec FROM autopilot_runs").fetchall()
    out = set()
    for (raw,) in rows:
        try:
            spec = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(spec, dict) and spec.get("job") and spec.get("report"):
            out.add((spec["job"], spec["report"]))
    return out


def pending(config_dir: str, jobs_dir: str, state_dir: str, root: str, now: float) -> list[dict]:
    """One batch per Job whose newest report is new to its auto configs: ``{job, report, picks}`` with a pick per
    config, in key order (what a batch writes first is the config sorted first). A pick of a whole-report config says
    ``whole`` and has no source: it is the report itself."""
    try:
        handled = _handled(state_dir)
        configs = production.load_all(config_dir, jobs_dir)
    except sqlite3.Error:
        return []
    by_job: dict[str, list] = {}
    for config in configs.values():
        if config.auto and config.usable and config.split in ("each", "whole"):
            by_job.setdefault(config.job, []).append(config)
    out = []
    for job, mine in by_job.items():
        # a Job's reports are item lists (its topic source) or whole files; its configs are all of one kind
        source = topic_store.resolve(jobs_dir, job) or topic_store.whole_source(jobs_dir, job)
        files = topic_store.report_files(source, root) if source is not None else []
        if not files:
            continue
        report = files[0][1]
        if (job, report) in handled:
            continue
        try:
            reported = os.path.getmtime(report)
        except OSError:
            continue
        if now - reported < SETTLE_SECONDS:
            continue
        picks = []
        for config in sorted(mine, key=lambda c: c.key):
            try:
                turned_on = os.path.getmtime(os.path.join(production.directory(config_dir), f"{config.key}.yaml"))
            except OSError:
                continue
            if reported > turned_on and config.split == "whole":
                # one video from the report as it is: marked, so the batch never reads a topic source for it
                picks.append({"column": config.recipe, "count": 1, "whole": True, "production": config.key})
            elif reported > turned_on:
                picks.append({"column": config.recipe, "count": config.auto, "source": config.source,
                              "production": config.key})
        if picks:
            out.append({"job": job, "report": report, "picks": picks})
    return out

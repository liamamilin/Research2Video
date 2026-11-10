"""Burst runs: when an AI word reaches B站热搜, run the AI flash research now instead of at the next 07:30/18:30.

Hits win on timeliness ("夜间突发", "深夜突袭"); our flash ran at fixed times, so news broke and waited. Every
``CHECK_EVERY`` seconds between ``ACTIVE_HOURS`` this reads B站热搜 (no login) and picks its AI words
(core/bili_hot.HOT_TERMS). A word not seen today and not already in today's flash reports starts one flash run,
through the same guards as the 「立即运行」 button, at most ``MAX_PER_DAY`` times a day and never within ``MIN_GAP``
of the last flash report. The run's own sources (bili_hot) then bring the word and its news in; heat, angles and
publishing follow as for any flash report. The program decides; a failure only means the next fixed run covers it.
"""
from __future__ import annotations

from datetime import datetime
import glob
import json
import logging
import os
from pathlib import Path
import re
import time
from typing import Callable, Optional

from core.fileio import atomic_write, file_lock

logger = logging.getLogger(__name__)

JOB = "monitoring/ai_flash_discovery"
REPORTS = "output/flash/{day}_ai_flash_discovery*.md"
CHECK_EVERY = 1800
ACTIVE_HOURS = (8, 22)
MAX_PER_DAY = 2
MIN_GAP = 3 * 3600


def _clean(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


class HotTrigger:
    """Daily state in ``state/hot_trigger.json``: when it last looked, the AI words seen, the runs it started."""

    def __init__(self, state_dir: str, workspace: str, *, trending: Optional[Callable[[], list[str]]] = None,
                 start: Optional[Callable[[], str]] = None, clock: Callable[[], float] = time.time):
        self.path = Path(state_dir) / "hot_trigger.json"
        self.workspace = workspace
        self.clock = clock
        self._trending = trending
        self._start = start or start_flash

    def trending(self) -> list[str]:
        if self._trending is not None:
            return self._trending()
        from core.video import heat
        return heat.Search(str(self.path.parent)).trending()

    def _load(self, day: str) -> dict:
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        if state.get("day") != day:
            state = {"day": day, "checked_at": state.get("checked_at", 0), "seen": [], "runs": []}
        return state

    def reports_today(self, day: str) -> list[str]:
        return sorted(glob.glob(os.path.join(self.workspace, REPORTS.format(day=day))))

    def check(self) -> str:
        """One look, at most every ``CHECK_EVERY``: what it did, for the log ("" when it did not look)."""
        now = self.clock()
        local = datetime.fromtimestamp(now)
        day = local.strftime("%Y-%m-%d")
        with file_lock(str(self.path), timeout=5):
            state = self._load(day)
            if now - float(state.get("checked_at") or 0) < CHECK_EVERY or not ACTIVE_HOURS[0] <= local.hour < ACTIVE_HOURS[1]:
                return ""
            state["checked_at"] = now
            self._save(state)
        from core.bili_hot import HOT_TERMS

        try:
            words = [w for w in self.trending() if HOT_TERMS.search(w)]
        except Exception as exc:  # noqa: BLE001 - the fixed runs still cover the day
            return f"热搜取不到（{type(exc).__name__}）"
        reports = self.reports_today(day)
        told = _clean(" ".join(Path(p).read_text(encoding="utf-8", errors="ignore") for p in reports))
        fresh = [w for w in words if w not in state["seen"] and _clean(w) not in told]
        with file_lock(str(self.path), timeout=5):
            state = self._load(day)
            state["seen"] = sorted(set(state["seen"]) | set(words))
            self._save(state)
        if not fresh:
            return ""
        if len(state["runs"]) >= MAX_PER_DAY:
            return f"热搜新词 {'、'.join(fresh)}，今天已加跑 {MAX_PER_DAY} 次，留给下一轮定时快讯"
        last = max((os.path.getmtime(p) for p in reports), default=0)
        if now - last < MIN_GAP:
            return f"热搜新词 {'、'.join(fresh)}，距上一份快讯不到 {MIN_GAP // 3600} 小时，留给下一轮"
        outcome = self._start()
        if outcome == "started":
            with file_lock(str(self.path), timeout=5):
                state = self._load(day)
                state["runs"].append({"at": now, "words": fresh})
                self._save(state)
            return f"热搜新词 {'、'.join(fresh)}：已加跑一轮 AI 快讯"
        return f"热搜新词 {'、'.join(fresh)}，没有加跑：{outcome}"

    def _save(self, state: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(str(self.path), json.dumps(state, ensure_ascii=False, indent=1))


def start_flash() -> str:
    """Start the flash Job the way 「立即运行」 does: "started", or why not."""
    from core import matrices
    from core.budget import month_spend, run_allowed
    from core.config import load_system_config
    from core.config import load_job
    from web.runner.executor import start_job
    from web.runner.registry import TaskRegistry
    from web.services.assistant_maintenance import ensure_accepting_runs, run_admission
    from web.settings import get_settings

    settings = get_settings()
    try:
        ensure_accepting_runs()
        job = load_job(settings.paths.jobs_dir, JOB)
        if not job or not job.get("enabled", True):
            return "快讯 Job 不存在或已停用"
        if TaskRegistry().is_running(JOB) or _scheduled_run_active(settings.paths.state_dir):
            return "快讯正在运行"
        blocked = matrices.cap_block(JOB, settings.paths.config_dir)
        if blocked:
            return blocked
        allowed, reason = run_allowed(month_spend(load_system_config(settings.paths.config_dir)), "AI 快讯")
        if not allowed:
            return reason
        with run_admission():
            if TaskRegistry().is_running(JOB):
                return "快讯正在运行"
            start_job(job_name=JOB, started_by="hot-trigger", config_dir=settings.paths.config_dir,
                      jobs_dir=settings.paths.jobs_dir)
    except Exception as exc:  # noqa: BLE001 - never more than a missed burst
        return f"启动失败（{type(exc).__name__}）"
    return "started"


def _scheduled_run_active(state_dir: str) -> bool:
    import sqlite3
    path = os.path.join(state_dir, "schedules.db")
    if not os.path.exists(path):
        return False
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2) as db:
        return bool(db.execute("SELECT 1 FROM schedule_runs WHERE job_name=? AND status IN "
                               "('queued','starting','running','retry')", (JOB,)).fetchone())


def tick() -> str:
    from web.settings import get_settings
    from web.services import video_workflow

    paths = get_settings().paths
    return HotTrigger(paths.state_dir, video_workflow.workspace_dir()).check()

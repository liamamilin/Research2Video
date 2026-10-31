"""One atomic daily allowance for unattended production, shared by every batch producer."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from core import production_policy

WAIT_NOTE = "今日自动生产名额已用完，保留队列，后续日期自动继续"


class Ledger:
    def __init__(self, state_dir: str, *, now: float | None = None):
        Path(state_dir).mkdir(parents=True, exist_ok=True)
        self.path = Path(state_dir) / "production_admission.db"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS admissions (
                    issue TEXT PRIMARY KEY, day TEXT NOT NULL, accepted_at REAL NOT NULL,
                    policy_revision TEXT NOT NULL, mode TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS admission_day ON admissions(day,mode);
            """)
            db.execute(
                "INSERT OR IGNORE INTO meta VALUES ('enabled_at',?)",
                (time.time() if now is None else now,),
            )

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def enter(
        self,
        issue: str,
        automatic: dict,
        revision: str,
        *,
        accepted_before: float | None = None,
        manual: bool = False,
        now: float | None = None,
    ) -> bool:
        now = time.time() if now is None else now
        local = datetime.fromtimestamp(
            now, ZoneInfo(automatic.get("timezone", "Asia/Shanghai"))
        )
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        day = local.date().isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM admissions WHERE issue=?", (issue,)
            ).fetchone():
                return True
            enabled_at = db.execute(
                "SELECT value FROM meta WHERE key='enabled_at'"
            ).fetchone()[0]
            legacy = accepted_before is not None and accepted_before < enabled_at
            mode = "manual" if manual else "legacy" if legacy else "automatic"
            count = db.execute(
                "SELECT COUNT(*) FROM admissions WHERE accepted_at>=? AND accepted_at<? AND mode='automatic'",
                (midnight.timestamp(), (midnight + timedelta(days=1)).timestamp()),
            ).fetchone()[0]
            if mode == "automatic" and count >= automatic.get("daily_limit", 4):
                return False
            db.execute(
                "INSERT INTO admissions VALUES (?,?,?,?,?)",
                (issue, day, now, revision, mode),
            )
        return True


def enter(
    paths,
    topic: dict,
    *,
    key: str,
    accepted_before: float | None = None,
    manual: bool = False,
) -> bool:
    policy = production_policy.load(paths.config_dir)
    job = (topic.get("episode") or {}).get("job") or (
        topic.get("matrix_dedup") or {}
    ).get("job", "")
    matrix = (topic.get("_content_context") or {}).get("matrix", "")
    if not job and topic.get("_production"):
        from core.video.production import load_all

        config = load_all(paths.config_dir, paths.jobs_dir).get(topic["_production"])
        job = config.job if config else ""
    if not production_policy.manages(policy, paths.config_dir, job=job, matrix=matrix):
        return True
    episode = topic.get("episode") or {}
    # Retries retain the frozen report identity; a newly authored report revision is new production.
    identity = [
        job,
        topic.get("_production", ""),
        episode.get("report_path") or topic.get("from_report"),
        episode.get("report_identity") or topic.get("report_identity", ""),
    ]
    if not identity[2]:
        identity = [
            topic.get("theme_task_id") or topic.get("id") or key,
            topic.get("_production", ""),
        ]
    issue = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return Ledger(paths.state_dir).enter(
        issue,
        policy.get("automatic", {}),
        policy["revision"],
        accepted_before=accepted_before,
        manual=manual,
    )

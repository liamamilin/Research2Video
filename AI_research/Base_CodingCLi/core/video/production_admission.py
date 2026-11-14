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
                    policy_revision TEXT NOT NULL, mode TEXT NOT NULL,
                    job TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS admission_day ON admissions(day,mode);
            """)
            db.execute("BEGIN IMMEDIATE")
            if "job" not in {row[1] for row in db.execute("PRAGMA table_info(admissions)")}:
                db.execute("ALTER TABLE admissions ADD COLUMN job TEXT NOT NULL DEFAULT ''")
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
        job: str = "",
        job_limit: int | None = None,
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
            limit = automatic.get("daily_limit", 4)  # None: every issue is admitted, still once
            if mode == "automatic" and limit is not None and count >= limit:
                return False
            if mode == "automatic" and job_limit is not None:
                job_count = db.execute(
                    "SELECT COUNT(*) FROM admissions WHERE accepted_at>=? AND accepted_at<?"
                    " AND mode='automatic' AND job=?",
                    (midnight.timestamp(), (midnight + timedelta(days=1)).timestamp(), job),
                ).fetchone()[0]
                if job_count >= job_limit:
                    return False
            db.execute(
                "INSERT INTO admissions (issue,day,accepted_at,policy_revision,mode,job) VALUES (?,?,?,?,?,?)",
                (issue, day, now, revision, mode, job),
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
    job = (
        (topic.get("episode") or {}).get("job")
        or (topic.get("matrix_dedup") or {}).get("job", "")
        or topic.get("knowledge_job", "")
    )
    context = topic.get("_content_context") or {}
    job = job or context.get("job", "")
    matrix = context.get("matrix", "")
    if not job and topic.get("_production"):
        from core.video.production import load_all

        config = load_all(paths.config_dir, paths.jobs_dir).get(topic["_production"])
        job = config.job if config else ""
    if not production_policy.manages(policy, paths.config_dir, job=job, matrix=matrix):
        return True
    from . import topic_store

    source = topic_store.resolve(paths.jobs_dir, job) if job else None
    job_limit = source.daily_limit if source and not source.error else None
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
    if job_limit is not None and not episode:
        # One admission per video, shared by writing and rendering.
        identity = [job, topic.get("_production", ""), topic.get("from_item") or topic.get("id") or key]
    issue = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return Ledger(paths.state_dir).enter(
        issue,
        policy.get("automatic", {}),
        policy["revision"],
        accepted_before=accepted_before,
        manual=manual,
        job=job,
        job_limit=job_limit,
    )

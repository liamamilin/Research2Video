"""Editable production policy with optimistic revisions and native schedule synchronization."""

from __future__ import annotations

import os
import time
from pathlib import Path

import yaml

from core import production_policy as policy
from core.fileio import atomic_write, file_lock
from core.schedule_store import ScheduleConflict, ScheduleStore

COLLECTOR = "system/material_collection"


def describe(paths) -> dict:
    value = policy.load(paths.config_dir)
    storage = ScheduleStore(
        paths.state_dir, config_dir=paths.config_dir, jobs_dir=paths.jobs_dir
    )
    plans = [
        p
        for p in storage.list()
        if policy.manages(value, paths.config_dir, job=p["job_name"])
    ]
    return {
        "policy": value,
        "plans": plans,
        "loaded": {
            "pid": os.getpid(),
            "issue_context_version": 1,
            "automatic_admission_version": 1,
        },
        "collector": next(
            (p for p in storage.list() if p["job_name"] == COLLECTOR), None
        ),
    }


def save(paths, value: dict, expected_revision: str) -> dict:
    """Changing settings does not rewrite any existing issue, report or video request."""
    policy.validate(value)
    from core.sources import resolve

    resolve(value.get("sources", {}).get("reference", []), paths.config_dir)
    from core.matrices import load_all

    unknown = set(value.get("matrices", [])) - set(load_all(paths.config_dir))
    if unknown:
        raise ValueError("矩阵不存在：" + ", ".join(sorted(unknown)))
    path = Path(paths.config_dir) / policy.FILE
    with file_lock(str(path)):
        current = policy.load(paths.config_dir)
        if current.get("revision", "") != expected_revision:
            raise ScheduleConflict("生产策略已被修改，请重新读取后保存")
        old = path.read_text() if path.exists() else None
        if old is not None:
            atomic_write(
                str(
                    Path(paths.state_dir) / "policy-history" / f"{time.time_ns()}.yaml"
                ),
                old,
            )
        atomic_write(
            str(path), yaml.safe_dump(value, allow_unicode=True, sort_keys=False)
        )
        try:
            synchronize(paths)
        except Exception:
            if old is not None:
                atomic_write(str(path), old)
            else:
                path.unlink(missing_ok=True)
            raise
    return describe(paths)


def synchronize(paths) -> dict | None:
    """Idempotent explicit migration; reading policy never installs or runs a task."""
    value = policy.load(paths.config_dir)
    if not value:
        return None
    from core.video.production_admission import Ledger

    Ledger(
        paths.state_dir
    )  # Freeze the grandfathering boundary before new automatic work enters.
    storage = ScheduleStore(
        paths.state_dir, config_dir=paths.config_dir, jobs_dir=paths.jobs_dir
    )
    existing = next((p for p in storage.list() if p["job_name"] == COLLECTOR), None)
    settings = value.get("collection", {})
    if not settings.get("enabled"):
        # Do not cancel a collector already running; its next entry observes the disabled policy.
        return existing
    from core.config import load_job

    job = load_job(paths.jobs_dir, COLLECTOR)
    if not job or not job.get("source_collection"):
        raise ValueError("素材积累任务配置不存在或类型不符")
    expression, timezone = (
        settings["schedule"],
        settings.get("timezone", "Asia/Shanghai"),
    )
    if existing and (existing["schedule"], existing["timezone"]) == (
        expression,
        timezone,
    ):
        return existing  # Preserve a user pause and next_run_at on no-op saves.
    return storage.save(
        COLLECTOR,
        expression,
        timezone=timezone,
        schedule_id=existing["id"] if existing else None,
        editing=bool(existing),
        expected_version=existing["version"] if existing else None,
        missed_policy="skip",
        max_retries=0,
    )

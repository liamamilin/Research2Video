"""Periodic retention cleanup for video render outputs and AutoVideo job folders.

Works seamlessly when ``<state>/video_renders`` and ``<autovideo_data>/jobs`` point directly
to external storage via symlinks (where ``core/video/archive.py`` intentionally skips).

Safety invariants:
- Never deletes anything while a render or AutoVideo job is still active, or while a parent
  job folder is referenced by an in-progress revision.
- When the resolved target lives under ``/Volumes/...``, verifies the external volume is
  mounted before touching any directory.
- Only removes on-disk folders; SQLite rows and diagnostics remain intact for history views.
- Serializes with ``video_storage_migration.json.worker`` so cleanup never races a migration.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Optional

from core.config import load_system_config
from core.video import archive as video_archive

logger = logging.getLogger("ai_research.video.cleanup")

TERMINAL_COMPLETED = ("COMPLETED",)
TERMINAL_FAILED = ("FAILED", "CANCELLED")
DEFAULT_CHECK_EVERY_HOURS = 24.0
DEFAULT_COMPLETED_RENDERS_KEEP_DAYS = 30.0
DEFAULT_COMPLETED_JOBS_KEEP_DAYS = 14.0
DEFAULT_FAILED_KEEP_DAYS = 7.0
RECEIPT_NAME = "video_cleanup.json"


def settings(config_dir: str) -> Optional[dict]:
    """Return normalized ``video.cleanup`` settings, or None when disabled/unset."""
    video = load_system_config(config_dir).get("video") or {}
    raw = video.get("cleanup") or {}
    if not raw or not raw.get("enabled"):
        return None
    archive_raw = video.get("archive") or {}
    return {
        "enabled": True,
        "dry_run": bool(raw.get("dry_run", False)),
        "check_every_seconds": max(300.0, float(raw.get("check_every_hours", DEFAULT_CHECK_EVERY_HOURS)) * 3600.0),
        "completed_renders_keep_days": float(raw.get("completed_renders_keep_days", DEFAULT_COMPLETED_RENDERS_KEEP_DAYS)),
        "completed_jobs_keep_days": float(raw.get("completed_jobs_keep_days", DEFAULT_COMPLETED_JOBS_KEEP_DAYS)),
        "failed_keep_days": float(raw.get("failed_keep_days", DEFAULT_FAILED_KEEP_DAYS)),
        "autovideo_data": str(raw.get("autovideo_data") or archive_raw.get("autovideo_data") or ""),
    }


def _target_ready(path: Path) -> tuple[Optional[Path], str]:
    """Resolve ``path`` (following symlinks) and ensure any external volume is mounted."""
    if not path.exists():
        return None, f"目录不存在：{path}"
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        return None, f"目录无法访问：{path} ({exc})"
    if not resolved.is_dir():
        return None, f"不是目录：{resolved}"
    if str(resolved).startswith("/Volumes/") and not video_archive.mounted(str(resolved)):
        return None, f"外接盘未挂载：{resolved}"
    return resolved, ""


def _dir_size(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            fp = os.path.join(root, name)
            try:
                if not os.path.islink(fp):
                    total += os.path.getsize(fp)
            except OSError:
                continue
    return total


def _remove_dir(folder: Path, *, dry_run: bool) -> tuple[bool, int]:
    size = _dir_size(folder)
    if dry_run:
        return True, size
    try:
        shutil.rmtree(folder)
        return True, size
    except OSError as exc:
        logger.warning("cleanup failed to remove %s: %s", folder, exc)
        return False, 0


def cleanup_renders(conf: dict, state_dir: str, renders: list[dict], *, dry_run: bool) -> dict:
    """Remove expired render folders under ``<state>/video_renders``."""
    out = {
        "completed_removed": 0,
        "failed_removed": 0,
        "freed_bytes": 0,
        "kept": 0,
        "failed": 0,
    }
    root, err = _target_ready(Path(state_dir) / "video_renders")
    if err or root is None:
        return {**out, "skipped": err}

    completed_days = conf["completed_renders_keep_days"]
    failed_days = conf["failed_keep_days"]
    by_id = {r["id"]: r for r in renders if r.get("id")}

    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or folder.is_symlink():
            continue
        row = by_id.get(folder.name)
        if row is None:
            # Unknown/orphan folder: only clean when older than completed retention.
            if completed_days <= 0:
                out["kept"] += 1
                continue
            age = video_archive._age_days(None, folder.stat().st_mtime)
            if age < completed_days:
                out["kept"] += 1
                continue
            ok, size = _remove_dir(folder, dry_run=dry_run)
            if ok:
                out["completed_removed"] += 1
                out["freed_bytes"] += size
            else:
                out["failed"] += 1
            continue

        status = str(row.get("status") or "")
        if status in TERMINAL_COMPLETED:
            limit = completed_days
            bucket = "completed_removed"
        elif status in TERMINAL_FAILED:
            limit = failed_days
            bucket = "failed_removed"
        else:
            out["kept"] += 1
            continue

        if limit <= 0:
            out["kept"] += 1
            continue
        age = video_archive._age_days(row.get("completed_at") or row.get("created_at"), folder.stat().st_mtime)
        if age < limit:
            out["kept"] += 1
            continue
        ok, size = _remove_dir(folder, dry_run=dry_run)
        if ok:
            out[bucket] += 1
            out["freed_bytes"] += size
        else:
            out["failed"] += 1
    return out


def cleanup_jobs(conf: dict, *, dry_run: bool) -> dict:
    """Remove expired AutoVideo working folders under ``<autovideo_data>/jobs``."""
    out = {
        "completed_removed": 0,
        "failed_removed": 0,
        "freed_bytes": 0,
        "kept": 0,
        "failed": 0,
    }
    data_str = conf.get("autovideo_data") or ""
    if not data_str:
        return {**out, "skipped": "未配置 autovideo_data"}
    data = Path(data_str)
    root, err = _target_ready(data / "jobs")
    if err or root is None:
        return {**out, "skipped": err}

    known: dict[str, tuple[str, Optional[str]]] = {}
    in_use: set[str] = set()
    db_path = data / "jobs.sqlite3"
    if db_path.is_file():
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5) as db:
            for job_id, status, updated_at, parent in db.execute(
                "SELECT job_id, status, updated_at, parent_job_id FROM jobs"
            ):
                known[job_id] = (str(status or ""), updated_at)
                if status not in (*TERMINAL_COMPLETED, *TERMINAL_FAILED) and parent:
                    in_use.add(parent)

    completed_days = conf["completed_jobs_keep_days"]
    failed_days = conf["failed_keep_days"]

    for folder in sorted(root.iterdir()):
        if not folder.is_dir() or folder.is_symlink():
            continue
        if folder.name in in_use:
            out["kept"] += 1
            continue
        status, updated_at = known.get(folder.name, ("", None))
        if status in TERMINAL_COMPLETED or not status:
            limit = completed_days
            bucket = "completed_removed"
        elif status in TERMINAL_FAILED:
            limit = failed_days
            bucket = "failed_removed"
        else:
            out["kept"] += 1
            continue

        if limit <= 0:
            out["kept"] += 1
            continue
        age = video_archive._age_days(updated_at, folder.stat().st_mtime)
        if age < limit:
            out["kept"] += 1
            continue
        ok, size = _remove_dir(folder, dry_run=dry_run)
        if ok:
            out[bucket] += 1
            out["freed_bytes"] += size
        else:
            out["failed"] += 1
    return out


def _run(config_dir: str, state_dir: str, renders: list[dict], *, dry_run: Optional[bool] = None) -> dict:
    conf = settings(config_dir)
    if conf is None:
        return {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "skipped": "未启用 video.cleanup"}
    effective_dry_run = conf["dry_run"] if dry_run is None else bool(dry_run)
    renders_res = cleanup_renders(conf, state_dir, renders, dry_run=effective_dry_run)
    jobs_res = cleanup_jobs(conf, dry_run=effective_dry_run)
    freed = int(renders_res.get("freed_bytes", 0)) + int(jobs_res.get("freed_bytes", 0))
    result = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "dry_run": effective_dry_run,
        "policy": {
            "completed_renders_keep_days": conf["completed_renders_keep_days"],
            "completed_jobs_keep_days": conf["completed_jobs_keep_days"],
            "failed_keep_days": conf["failed_keep_days"],
        },
        "renders": renders_res,
        "jobs": jobs_res,
        "freed_bytes": freed,
        "freed_gb": round(freed / (1024 ** 3), 2),
    }
    if not effective_dry_run:
        Path(state_dir, RECEIPT_NAME).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def last_run(state_dir: str) -> Optional[dict]:
    try:
        return json.loads(Path(state_dir, RECEIPT_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def due(config_dir: str, state_dir: str) -> bool:
    conf = settings(config_dir)
    if conf is None:
        return False
    receipt = Path(state_dir, RECEIPT_NAME)
    return not receipt.is_file() or time.time() - receipt.stat().st_mtime >= conf["check_every_seconds"]


def run(config_dir: str, state_dir: str, renders: list[dict], *, dry_run: Optional[bool] = None) -> dict:
    """Serialize retention cleanup with any in-flight storage migration."""
    from core.fileio import file_lock

    try:
        with file_lock(str(Path(state_dir) / "video_storage_migration.json") + ".worker", timeout=1):
            return _run(config_dir, state_dir, renders, dry_run=dry_run)
    except TimeoutError:
        return {"skipped": "视频目录正在迁移"}

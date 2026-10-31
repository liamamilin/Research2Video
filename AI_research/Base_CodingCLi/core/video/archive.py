"""Keep finished video data on the external disk and only a week of it on this one.

Two things hold video data here:

* ``<state>/video_renders/<render id>/``: the files downloaded from AutoVideo for a render (video.mp4, captions, ...);
* ``<autovideo data>/jobs/<job id>/``: AutoVideo's working folder for a job (frames, audio, renderer output).

A finished render or job is copied to the archive disk at once (``mirrored``); once it is older than ``keep_days`` the
copy is compared with the original again and only then is the local folder removed. Nothing is removed when the disk is not mounted,
when the copy differs, or while the render or job is still running. The archive copy is never deleted.

A render's files are still served after they are gone from here: ``archived_dir`` is where to look (web/services/
video_render.py). A job folder that was archived is not on this disk any more, so revising that old job (a new version of
its content) is not possible until its folder is put back with ``restore_job``.

Settings (``video.archive`` in config/system.yaml): ``dir`` (the archive root), ``keep_days``, ``autovideo_data``.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.config import load_system_config

logger = logging.getLogger("ai_research.video.archive")

RENDERS_FOLDER = "AREC成片副本"
JOBS_FOLDER = "autovideo/jobs"
TERMINAL_RENDER = ("COMPLETED", "FAILED", "CANCELLED")
TERMINAL_JOB = ("COMPLETED", "FAILED", "CANCELLED")
DEFAULT_KEEP_DAYS = 7
#: how often the server looks (the pass itself only moves what has aged out)
CHECK_EVERY_SECONDS = 6 * 3600


def settings(config_dir: str) -> Optional[dict]:
    """``{"dir", "keep_days", "autovideo_data"}``, or None when no archive is set up."""
    raw = ((load_system_config(config_dir).get("video") or {}).get("archive")) or {}
    if not raw.get("dir"):
        return None
    return {"dir": str(raw["dir"]), "keep_days": float(raw.get("keep_days", DEFAULT_KEEP_DAYS)),
            "autovideo_data": str(raw.get("autovideo_data") or "")}


def mounted(path: str) -> bool:
    """Whether ``path`` is on a mounted volume of its own (an unplugged disk leaves only an empty folder or nothing)."""
    current = Path(path)
    while True:
        if current == current.parent:
            return False  # reached "/": the path is on the internal disk
        if os.path.ismount(current):
            return current.exists()
        current = current.parent


def renders_archive(root: str) -> Path:
    return Path(root) / RENDERS_FOLDER


def jobs_archive(root: str) -> Path:
    return Path(root) / JOBS_FOLDER


def archived_dir(config_dir: str, render_id: str) -> Optional[Path]:
    """Where a render's files are on the archive disk (when it is mounted and has them)."""
    conf = settings(config_dir)
    if conf is None or not mounted(conf["dir"]):
        return None
    path = renders_archive(conf["dir"]) / render_id
    return path if path.is_dir() else None


def _rsync(source: Path, dest: Path, *, check: bool = False, checksum: bool = False) -> bool:
    """Copy ``source`` into ``dest`` (rsync -a). With ``check``: only report whether ``dest`` already equals it."""
    args = ["rsync", "-a"]
    if checksum:
        args.append("-c")
    if check:
        args += ["-n", "--itemize-changes"]
    proc = subprocess.run([*args, f"{source}/", f"{dest}/"], capture_output=True, text=True)
    if proc.returncode != 0:
        logger.warning("rsync %s -> %s failed: %s", source, dest, proc.stderr.strip()[:300])
        return False
    return not proc.stdout.strip() if check else True


def _move(source: Path, dest: Path, *, checksum: bool = False) -> bool:
    """Copy, compare, and only then remove ``source``. False (and ``source`` kept) when anything is off."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not _rsync(source, dest, checksum=checksum):
        return False
    if not _rsync(source, dest, check=True, checksum=checksum):
        logger.warning("copy of %s differs from the original: kept", source)
        return False
    shutil.rmtree(source)
    return True


def _age_days(moment: Optional[str], fallback: float) -> float:
    """Days since an ISO time ("2026-10-22T08:54:21.850+00:00" or "...+0800"); ``fallback`` (an mtime) if unreadable."""
    if moment:
        text = moment.replace("Z", "+00:00")
        if len(text) > 5 and text[-5] in "+-" and ":" not in text[-5:]:
            text = text[:-2] + ":" + text[-2:]
        try:
            then = datetime.fromisoformat(text)
            if then.tzinfo is None:
                then = then.replace(tzinfo=timezone.utc)
            return (time.time() - then.timestamp()) / 86400
        except ValueError:
            pass
    return (time.time() - fallback) / 86400


def _du(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())


def archive_renders(conf: dict, state_dir: str, renders: list[dict], *, dry_run: bool) -> dict:
    """Move the files of finished renders older than ``keep_days``. ``renders``: store.list_all_renders() rows."""
    local = Path(state_dir) / "video_renders"
    out = {"moved": 0, "bytes": 0, "kept": 0, "mirrored": 0, "failed": 0}
    if local.is_symlink():
        return out  # direct external storage already preserves these files
    for render in renders:
        folder = local / render["id"]
        if not folder.is_dir() or render["status"] not in TERMINAL_RENDER:
            continue
        fallback = folder.stat().st_mtime
        if _age_days(render.get("completed_at") or render.get("created_at"), fallback) < conf["keep_days"]:
            out["kept"] += 1
            if not dry_run and _rsync(folder, renders_archive(conf["dir"]) / render["id"], checksum=True):
                out["mirrored"] += 1  # the archive already has it by the time it is a week old
            continue
        size = _du(folder)
        if dry_run:
            out["moved"] += 1
            out["bytes"] += size
        elif _move(folder, renders_archive(conf["dir"]) / render["id"], checksum=True):
            out["moved"] += 1
            out["bytes"] += size
        else:
            out["failed"] += 1
    return out


def archive_jobs(conf: dict, *, dry_run: bool) -> dict:
    """Move AutoVideo job folders of finished jobs older than ``keep_days``; folders AutoVideo does not know are aged
    by their modified time."""
    data = Path(conf["autovideo_data"])
    jobs_dir = data / "jobs"
    out = {"moved": 0, "bytes": 0, "kept": 0, "mirrored": 0, "failed": 0}
    if not conf["autovideo_data"] or not jobs_dir.is_dir() or jobs_dir.is_symlink():
        return out
    known: dict[str, tuple] = {}
    in_use: set[str] = set()
    db_path = data / "jobs.sqlite3"
    if db_path.is_file():
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5) as db:
            for job_id, status, updated_at, parent in db.execute("SELECT job_id, status, updated_at, parent_job_id FROM jobs"):
                known[job_id] = (status, updated_at)
                if status not in TERMINAL_JOB and parent:
                    in_use.add(parent)  # a revision in progress reads its parent's folder
    for folder in sorted(jobs_dir.iterdir()):
        if not folder.is_dir() or folder.is_symlink():
            continue
        status, updated_at = known.get(folder.name, (None, None))
        if folder.name in in_use or (status is not None and status not in TERMINAL_JOB):
            out["kept"] += 1
            continue
        if _age_days(updated_at, folder.stat().st_mtime) < conf["keep_days"]:
            out["kept"] += 1
            if not dry_run and status is not None and _rsync(folder, jobs_archive(conf["dir"]) / folder.name):
                out["mirrored"] += 1
            continue
        size = _du(folder)
        if dry_run:
            out["moved"] += 1
            out["bytes"] += size
        elif _move(folder, jobs_archive(conf["dir"]) / folder.name):
            out["moved"] += 1
            out["bytes"] += size
        else:
            out["failed"] += 1
    return out


def _run(config_dir: str, state_dir: str, renders: list[dict], *, dry_run: bool = False) -> dict:
    """One pass. Returns what it did (also written to ``<state>/video_archive.json``)."""
    conf = settings(config_dir)
    result: dict = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "dry_run": dry_run}
    if conf is None:
        return {**result, "skipped": "没有配置 video.archive"}
    if not mounted(conf["dir"]):
        return {**result, "skipped": f"外接盘没有挂载：{conf['dir']}"}
    result["renders"] = archive_renders(conf, state_dir, renders, dry_run=dry_run)
    result["jobs"] = archive_jobs(conf, dry_run=dry_run)
    if not dry_run:
        Path(state_dir, "video_archive.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def last_run(state_dir: str) -> Optional[dict]:
    try:
        return json.loads(Path(state_dir, "video_archive.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def due(state_dir: str) -> bool:
    last = Path(state_dir, "video_archive.json")
    return not last.is_file() or time.time() - last.stat().st_mtime >= CHECK_EVERY_SECONDS


def restore_job(config_dir: str, job_id: str) -> bool:
    """Put an archived AutoVideo job folder back (to revise that job)."""
    conf = settings(config_dir)
    if conf is None or not mounted(conf["dir"]):
        return False
    source = jobs_archive(conf["dir"]) / job_id
    dest = Path(conf["autovideo_data"]) / "jobs" / job_id
    return source.is_dir() and _rsync(source, dest)


def run(config_dir: str, state_dir: str, renders: list[dict], *, dry_run: bool = False) -> dict:
    """Serialize archive cleanup with a user-requested storage migration."""
    from core.fileio import file_lock
    try:
        with file_lock(str(Path(state_dir) / "video_storage_migration.json") + ".worker", timeout=1):
            return _run(config_dir, state_dir, renders, dry_run=dry_run)
    except TimeoutError:
        return {"skipped": "视频目录正在迁移"}

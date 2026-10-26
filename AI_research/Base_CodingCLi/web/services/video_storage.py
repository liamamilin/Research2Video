"""Selectable video storage; migrate while idle and retain legacy paths as links."""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

from core.config import load_system_config
from core.fileio import atomic_write, file_lock
from web.settings import get_settings

logger = logging.getLogger(__name__)
DEFAULT_ROOT = "/Volumes/个人资料/2026/视频生成工作流/AI创作/视频输出"
_thread: threading.Thread | None = None
_thread_lock = threading.Lock()


def paths() -> tuple[Path, Path]:
    """Return the internal ledger directory and the local AutoVideo data directory."""
    settings = get_settings()
    video = load_system_config(settings.paths.config_dir).get("video") or {}
    data = (video.get("archive") or {}).get("autovideo_data")
    return Path(settings.paths.state_dir).resolve(), (Path(data).expanduser().resolve() if data else Path(settings.paths.config_dir).resolve().parent.parent.parent / "autovideo/g3/data")


def _record() -> Path:
    return paths()[0] / "video_storage_migration.json"


def _read() -> dict:
    try:
        return json.loads(_record().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(record: dict) -> None:
    atomic_write(str(_record()), json.dumps(record, ensure_ascii=False, indent=2))


def migration_pending() -> bool:
    """Whether automatic video creation should wait for storage maintenance."""
    return _read().get("status") in {"waiting", "copying"}


def configured_root() -> str:
    """Selected output root after a completed migration."""
    config = load_system_config(get_settings().paths.config_dir)
    return str(((config.get("video") or {}).get("storage") or {}).get("dir") or "")


def volume(path: Path) -> Path | None:
    """Required macOS volume for a path, following its legacy link."""
    # Resolve existing links before checking a /Volumes path. Never mkdir an unplugged disk.
    parts = path.resolve().parts
    return Path(*parts[:3]) if len(parts) >= 3 and parts[1] == "Volumes" else None


def available(path: Path) -> bool:
    """Whether the external volume containing a path is mounted."""
    mount = volume(path)
    return mount is None or os.path.ismount(mount)


def unavailable_reason() -> str:
    """Explain why new video writes cannot use the current storage."""
    state, data = paths()
    if (data / ".storage-migration").exists():
        return "正在切换视频输出目录，请稍后重试"
    for path in (state / "video_renders", data / "jobs"):
        if not available(path) or (path.is_symlink() and not path.exists()):
            return f"视频输出磁盘未连接：{path.resolve()}"
    return ""


def status() -> dict:
    """Storage settings and a snapshot of the persisted migration progress."""
    root = configured_root()
    record = _read()
    selected = root or record.get("target") or DEFAULT_ROOT
    target = Path(selected)
    online = available(target)
    probe = target
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free = shutil.disk_usage(probe).free if online else None
    return {"directory": root, "suggested_directory": selected, "online": online,
            "free_bytes": free, "migration": record,
            "renders_directory": str((paths()[0] / "video_renders").resolve()),
            "jobs_directory": str((paths()[1] / "jobs").resolve())}


def browse(directory: str = "/Volumes") -> dict:
    """List visible subfolders for the administrator folder picker."""
    path = Path(directory).expanduser()
    if not path.is_absolute() or not available(path) or not path.is_dir():
        raise ValueError("文件夹不存在，或外接硬盘未连接")
    path = path.resolve()
    try:
        folders = sorted((p for p in path.iterdir() if not p.name.startswith(".") and p.is_dir()), key=lambda p: p.name.lower())
        return {"directory": str(path), "parent": str(path.parent),
                "folders": [{"name": p.name, "path": str(p)} for p in folders]}
    except PermissionError as exc:
        raise ValueError("没有权限读取这个文件夹") from exc


def _targets(target: Path) -> list[tuple[Path, Path]]:
    state, data = paths()
    pairs = [(state / "video_renders", target / "研究成片")]
    # SQLite databases, worker locks and launcher logs stay on the fast internal disk.
    if data.is_dir():
        pairs += [(p, target / "制作数据" / p.name) for p in sorted(data.iterdir())
                  if p.is_dir() and p.name not in {"launcher", "music-assets", "music-jobs", "music-curation", "creative-images", "input-documents"} and not p.name.startswith(".")]
    if not any(p.name == "jobs" for p, _ in pairs):
        pairs.append((data / "jobs", target / "制作数据/jobs"))
    return pairs


def _same_location(left: Path, right: Path) -> bool:
    if left.resolve() == right.resolve():
        return True
    try:
        return left.samefile(right)
    except OSError:
        return False


def _within(path: Path, parent: Path) -> bool:
    if path.resolve().is_relative_to(parent.resolve()):
        return True
    return any(_same_location(candidate, parent) for candidate in (path, *path.parents))


def request_migration(directory: str) -> dict:
    """Validate and queue a directory move without blocking the settings page."""
    raw = Path(directory.strip()).expanduser()
    if not directory.strip() or not raw.is_absolute() or ".." in raw.parts:
        raise ValueError("请填写完整的绝对文件夹路径")
    target = raw.resolve()
    if not available(target):
        raise ValueError("外接硬盘未连接，请连接后再保存")
    if configured_root() and _same_location(target, Path(configured_root())) and _read().get("status") == "completed":
        return status()
    # A destination within a source recursively copies itself. Also disallow its ancestors.
    for source, dest in _targets(target):
        if _same_location(source, dest):
            continue  # retry after some folders have already moved
        source = source.resolve()
        if _within(target, source) or _within(source, target):
            raise ValueError("请选择独立的输出文件夹，不能位于现有视频目录内或包含现有目录")
    with file_lock(str(_record())):
        if _read().get("status") in {"waiting", "copying"}:
            raise ValueError("已有目录迁移进行中，请等待完成")
        target.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=target):
            pass
        _save({"status": "waiting", "target": str(target), "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "message": "等待视频任务完成后迁移", "moved_bytes": 0, "moved_folders": 0,
               "folders": [{"source": str(source), "destination": str(dest)} for source, dest in _targets(target)]})
    resume()
    return status()


def _busy(db_path: Path, table: str) -> bool:
    if not db_path.is_file():
        return False
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            return False
        return bool(conn.execute(f"SELECT 1 FROM {table} WHERE status NOT IN ('COMPLETED','FAILED','CANCELLED') LIMIT 1").fetchone())


def _copies_match(source: Path, dest: Path) -> bool:
    check = subprocess.run(["rsync", "-rcln", "--exclude=.DS_Store", "--itemize-changes", f"{source}/", f"{dest}/"], capture_output=True, text=True)
    return check.returncode == 0 and not any(line and not line.startswith(".") for line in check.stdout.splitlines())


def _cleanup_original(path: Path, dest: Path) -> None:
    for attempt in range(3):
        try:
            shutil.rmtree(path)
            return
        except OSError:
            if not path.exists():
                return
            # An early nonempty subdirectory aborts rmtree before it reaches
            # other, already copied files. Recheck those files before retrying.
            if not _copies_match(path, dest):
                raise RuntimeError(f"旧目录出现未迁移的修改，已保留：{path}")
            if attempt < 2:
                continue
            remaining = [p for p in path.rglob("*") if p.is_file() and p.name != ".DS_Store"]
            if remaining:
                raise
            logger.warning("Finder metadata remains in %s; video files already moved", path)


def _copy_and_link(source: Path, dest: Path) -> int:
    if _same_location(source, dest):
        for backup in source.parent.glob(f".{source.name}.storage-old-*"):
            if not _copies_match(backup, dest):
                raise RuntimeError(f"旧目录仍有未迁移的文件：{backup}")
            _cleanup_original(backup, dest)
        return sum(p.stat().st_size for p in dest.rglob("*") if p.is_file() and not p.is_symlink())
    dest.mkdir(parents=True, exist_ok=True)
    # Recover the narrow rename -> link window after an interrupted process.
    backups = sorted(source.parent.glob(f".{source.name}.storage-old-*"))
    if not source.exists() and not source.is_symlink() and backups:
        os.rename(backups[0], source)
    if not source.exists():
        source.parent.mkdir(parents=True, exist_ok=True)
        source.symlink_to(dest, target_is_directory=True)
        return 0
    original = source.resolve()
    size = sum(p.stat().st_size for p in original.rglob("*") if p.is_file() and not p.is_symlink())
    # Existing different files are never overwritten; verification reports a collision.
    proc = subprocess.run(["rsync", "-a", "--exclude=.DS_Store", "--ignore-existing", f"{original}/", f"{dest}/"], capture_output=True, text=True)
    if proc.returncode:
        raise RuntimeError(f"复制失败：{source.name}（原文件保留）")
    if not _copies_match(original, dest):
        raise RuntimeError(f"文件校验未通过或目标存在同名不同内容：{source.name}（原文件保留）")
    if not available(dest):
        raise RuntimeError("外接硬盘已断开，原文件保留")
    temporary = source.with_name(f".{source.name}.storage-link-{uuid.uuid4().hex}")
    temporary.symlink_to(dest, target_is_directory=True)
    if source.is_symlink():
        os.replace(temporary, source)
        if not _same_location(original, dest):
            _cleanup_original(original, dest)
    else:
        backup = source.with_name(f".{source.name}.storage-old-{uuid.uuid4().hex}")
        os.rename(source, backup)
        try:
            os.replace(temporary, source)
        except BaseException:
            os.rename(backup, source)
            temporary.unlink(missing_ok=True)
            raise
        _cleanup_original(backup, dest)
    return size


def _persist_target(target: Path) -> None:
    from web.routes.config import system_config_lock
    from web.services.yaml_io import parse_yaml, dump_yaml, atomic_write as yaml_write
    path = Path(get_settings().paths.config_dir) / "system.yaml"
    with system_config_lock():
        config = parse_yaml(path.read_text(encoding="utf-8"))
        config.setdefault("video", {})["storage"] = {"dir": str(target)}
        yaml_write(str(path), dump_yaml(config))


def run_pending() -> None:
    """Move pending folders once video and music queues are idle."""
    state, data = paths()
    marker = data / ".storage-migration"
    with file_lock(str(_record()) + ".worker", timeout=1):
        record = _read()
        if record.get("status") not in {"waiting", "copying"}:
            return
        target = Path(record["target"])
        try:
            if not available(target):
                raise RuntimeError("外接硬盘未连接；连接后可重新保存并迁移")
            if _busy(data / "jobs.sqlite3", "jobs") or _busy(data / "music-jobs.sqlite3", "music_jobs") or _busy(state / "video.db", "renders"):
                record.update(status="waiting", message="等待正在运行的视频任务完成，完成后自动迁移")
                _save(record)
                return
            data.mkdir(parents=True, exist_ok=True)
            # Serialize the maintenance marker with engine submission and claim transactions.
            with sqlite3.connect(data / "jobs.sqlite3", timeout=10) as conn:
                conn.execute("BEGIN IMMEDIATE")
                marker.touch()
                if _busy(data / "jobs.sqlite3", "jobs") or _busy(data / "music-jobs.sqlite3", "music_jobs") or _busy(state / "video.db", "renders"):
                    marker.unlink(missing_ok=True)
                    return
            record.update(status="copying", message="正在迁移并校验文件，完成后自动释放原磁盘空间")
            _save(record)
            folders = record.get("folders") or [{"source": str(source), "destination": str(dest)} for source, dest in _targets(target)]
            for folder in folders:
                source, dest = Path(folder["source"]), Path(folder["destination"])
                record["current_folder"] = source.name
                _save(record)
                moved = _copy_and_link(source, dest)
                record["moved_bytes"] += moved
                record["moved_folders"] += 1
                _save(record)
            _persist_target(target)
            record.update(status="completed", message="迁移完成，新视频将直接写入所选目录", completed_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
            record.pop("current_folder", None)
            _save(record)
        except Exception as exc:
            logger.exception("video storage migration failed")
            record.update(status="failed", message=str(exc))
            _save(record)
        finally:
            marker.unlink(missing_ok=True)


def _run_with_activity() -> None:
    """Keep the launcher's idle watchdog from stopping a move after its tab closes."""
    from core import activity
    stop = threading.Event()
    state, _ = paths()

    def pulse() -> None:
        while not stop.is_set():
            activity.mark_activity_background(str(state))
            stop.wait(20)

    keeper = threading.Thread(target=pulse, name="video-storage-activity", daemon=True)
    keeper.start()
    try:
        run_pending()
    except TimeoutError:
        pass  # Another migration worker owns the file lock.
    finally:
        stop.set()
        keeper.join(timeout=1)


def resume() -> None:
    """Start one pending migration worker, including after a service restart."""
    global _thread
    with _thread_lock:
        if _read().get("status") not in {"waiting", "copying"} or (_thread and _thread.is_alive()):
            return
        _thread = threading.Thread(target=_run_with_activity, name="video-storage", daemon=True)
        _thread.start()

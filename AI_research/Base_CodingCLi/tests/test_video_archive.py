"""Finished video data moves to the archive disk and leaves the internal one after a week (core/video/archive.py)."""

import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest

from core.video import archive


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.000+00:00")


@pytest.fixture
def disks(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "mounted", lambda path: True)  # a temp folder is not a mount point
    state, data, disk = tmp_path / "state", tmp_path / "av", tmp_path / "disk"
    (state / "video_renders").mkdir(parents=True)
    (data / "jobs").mkdir(parents=True)
    disk.mkdir()
    conf = {"dir": str(disk), "keep_days": 7.0, "autovideo_data": str(data)}
    monkeypatch.setattr(archive, "settings", lambda config_dir: conf)
    return state, data, disk


def _render_files(state, render_id: str, text: str = "video") -> None:
    folder = state / "video_renders" / render_id
    folder.mkdir()
    (folder / "video.mp4").write_text(text)


def test_a_finished_render_older_than_a_week_moves_and_a_young_or_running_one_stays(disks):
    state, _, disk = disks
    for rid in ("old", "young", "busy"):
        _render_files(state, rid)
    renders = [{"id": "old", "status": "COMPLETED", "created_at": _iso(9), "completed_at": _iso(8)},
               {"id": "young", "status": "COMPLETED", "created_at": _iso(2), "completed_at": _iso(2)},
               {"id": "busy", "status": "RUNNING", "created_at": _iso(9), "completed_at": None}]
    result = archive.run("config", str(state), renders)
    assert result["renders"]["moved"] == 1 and result["renders"]["kept"] == 1
    assert not (state / "video_renders" / "old").exists()
    assert (disk / "AREC成片副本" / "old" / "video.mp4").read_text() == "video"  # the archive has it
    assert (state / "video_renders" / "young").is_dir() and (state / "video_renders" / "busy").is_dir()
    assert (disk / "AREC成片副本" / "young" / "video.mp4").is_file()  # copied at once, removed only after a week
    assert not (disk / "AREC成片副本" / "busy").exists()  # a running render is not copied
    assert archive.last_run(str(state))["renders"]["moved"] == 1


def test_nothing_is_removed_when_the_copy_does_not_match(disks, monkeypatch):
    state, _, disk = disks
    _render_files(state, "old")
    real = archive._rsync
    monkeypatch.setattr(archive, "_rsync", lambda s, d, *, check=False, checksum=False: False if check else real(s, d, checksum=checksum))
    result = archive.run("config", str(state), [{"id": "old", "status": "COMPLETED", "created_at": _iso(9), "completed_at": _iso(9)}])
    assert result["renders"]["failed"] == 1 and (state / "video_renders" / "old" / "video.mp4").is_file()


def test_nothing_moves_when_the_disk_is_not_mounted(disks, monkeypatch):
    state, _, _ = disks
    _render_files(state, "old")
    monkeypatch.setattr(archive, "mounted", lambda path: False)
    result = archive.run("config", str(state), [{"id": "old", "status": "COMPLETED", "created_at": _iso(9), "completed_at": _iso(9)}])
    assert "没有挂载" in result["skipped"] and (state / "video_renders" / "old").is_dir()


def test_a_dry_run_changes_nothing_and_says_how_much_would_move(disks):
    state, _, disk = disks
    _render_files(state, "old", "x" * 1000)
    result = archive.run("config", str(state), [{"id": "old", "status": "FAILED", "created_at": _iso(9), "completed_at": None}], dry_run=True)
    assert result["renders"] == {"moved": 1, "bytes": 1000, "kept": 0, "mirrored": 0, "failed": 0}
    assert (state / "video_renders" / "old").is_dir() and not (disk / "AREC成片副本").exists()


def _jobs_db(data, rows):
    with sqlite3.connect(data / "jobs.sqlite3") as db:
        db.execute("CREATE TABLE jobs (job_id TEXT, status TEXT, updated_at TEXT, parent_job_id TEXT)")
        db.executemany("INSERT INTO jobs VALUES (?,?,?,?)", rows)


def test_autovideo_job_folders_follow_the_same_rule_and_a_revisions_parent_stays(disks):
    state, data, disk = disks
    rows = [("done-old", "COMPLETED", _iso(9), None), ("done-new", "COMPLETED", _iso(1), None),
            ("running", "RUNNING", _iso(9), None), ("parent", "COMPLETED", _iso(9), None),
            ("child", "RUNNING", _iso(0), "parent")]
    _jobs_db(data, rows)
    for name in [r[0] for r in rows] + ["unknown-old"]:
        folder = data / "jobs" / name
        (folder / "attempts").mkdir(parents=True)
        (folder / "attempts" / "f").write_text(name)
    stale = time.time() - 9 * 86400
    os.utime(data / "jobs" / "unknown-old", (stale, stale))
    result = archive.run("config", str(state), [])
    assert result["jobs"]["moved"] == 2  # done-old and the folder AutoVideo does not know
    left = {p.name for p in (data / "jobs").iterdir()}
    assert left == {"done-new", "running", "parent", "child"}
    assert (disk / "autovideo" / "jobs" / "done-old" / "attempts" / "f").read_text() == "done-old"
    assert archive.restore_job("config", "done-old") and (data / "jobs" / "done-old" / "attempts" / "f").is_file()


def test_an_archived_renders_file_is_still_served(disks, monkeypatch):
    from web.services import video_render

    state, _, disk = disks
    (disk / "AREC成片副本" / "r1").mkdir(parents=True)
    (disk / "AREC成片副本" / "r1" / "video.mp4").write_text("v")
    monkeypatch.setattr(video_render, "files_dir", lambda rid: state / "video_renders" / rid)
    render = {"id": "r1", "artifacts": [{"name": "video.mp4"}]}
    assert video_render.safe_file(render, "video.mp4") == disk / "AREC成片副本" / "r1" / "video.mp4"
    assert video_render.safe_file({"id": "r1", "artifacts": []}, "video.mp4") is None

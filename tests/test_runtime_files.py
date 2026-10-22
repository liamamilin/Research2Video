"""Per-process runtime files do not pile up in state/."""

from __future__ import annotations

import os
import subprocess
import sys

from web.services import runtime_files


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_files_of_dead_processes_go_and_live_ones_stay(tmp_path):
    dead = tmp_path / f"app_runtime-{_dead_pid()}.json"
    mine = tmp_path / f"app_runtime-{os.getpid()}.json"
    published = tmp_path / "app_runtime.json"
    other = tmp_path / "app_runtime-notes.json"
    for path in (dead, mine, published, other):
        path.write_text("{}", encoding="utf-8")
    assert runtime_files.prune_dead(tmp_path) == 1
    assert not dead.exists() and mine.exists() and published.exists() and other.exists()

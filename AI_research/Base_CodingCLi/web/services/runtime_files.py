"""The per-process ``app_runtime-<pid>.json`` files the launcher writes before the app is serving.

An instance writes its file at start; once it has taken its first request its contents move to ``app_runtime.json`` (the
one the gateway and the maintenance job read) and the per-process file is of no use. Files of processes that died before
that point were never removed, and 100 of them had piled up in ``state/``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_NAME = re.compile(r"^app_runtime-(\d+)\.json$")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # somebody else's process: it exists
    return True


def prune_dead(state_dir: Path) -> int:
    """Remove the per-process runtime files whose process is gone. Returns how many."""
    removed = 0
    for path in Path(state_dir).glob("app_runtime-*.json"):
        match = _NAME.match(path.name)
        if match is None or int(match.group(1)) == os.getpid() or _alive(int(match.group(1))):
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed

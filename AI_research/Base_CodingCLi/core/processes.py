"""Read-only process exit checks shared by maintenance and its detached helper."""
from __future__ import annotations
import os
import subprocess


def process_exited(pid: int) -> bool:
    """A zombie has exited even while its launcher has not reaped its PID."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    try:
        result = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, timeout=2)
        return result.returncode == 1 or result.stdout.strip().startswith('Z')
    except (OSError, subprocess.TimeoutExpired):
        return False

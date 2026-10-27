"""Read duration from an existing local movie without relying on a login-shell PATH."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys


def probe_duration(path: str | Path) -> float | None:
    """Use the installed video engine's ffprobe, then the system installation."""
    movie = Path(path)
    if not movie.is_file():
        return None
    workspace = Path(__file__).resolve().parents[4]
    folder = f'{sys.platform}_{platform.machine().lower()}'
    bundled = workspace / 'autovideo' / 'g0' / folder / ('ffprobe.exe' if sys.platform == 'win32' else 'ffprobe')
    candidates = [str(bundled), shutil.which('ffprobe')]
    for binary in dict.fromkeys(candidates):
        if not binary or not os.access(binary, os.X_OK):
            continue
        try:
            result = subprocess.run([binary, '-v', 'error', '-show_entries', 'format=duration',
                                     '-of', 'json', str(movie.resolve())],
                                    capture_output=True, text=True, timeout=15, check=True)
            duration = float(json.loads(result.stdout)['format']['duration'])
            if math.isfinite(duration) and duration > 0:
                return duration
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            continue
    return None

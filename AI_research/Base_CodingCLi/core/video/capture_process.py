"""Run browser work outside the API process with a wall-clock deadline."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Callable

from core.video.screenshots import MAX_BYTES, PageUnavailable, ScreenshotError

ROOT = Path(__file__).resolve().parents[2]
_slots = threading.BoundedSemaphore(2)


def _command(folder: Path) -> list[str]:
    return [sys.executable, "-m", "core.video.capture_worker", str(folder)]


def _stop(process: subprocess.Popen) -> None:
    """Reap this worker and its browser/driver even if browser cleanup hangs."""
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        elif process.poll() is None:
            process.kill()
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


def run(operation: str, options: dict, *, timeout_seconds: float = 60,
        on_progress: Callable[[dict], None] | None = None) -> tuple[bytes, dict]:
    """Return only picture bytes and metadata; no Playwright objects cross threads."""
    if timeout_seconds <= 0:
        raise ScreenshotError("网页获取已达到时间预算")
    if not _slots.acquire(blocking=False):
        raise ScreenshotError("已有两项截图正在执行，请稍后重试")
    try:
        with tempfile.TemporaryDirectory(prefix="arec-capture-") as temporary:
            folder = Path(temporary)
            request = json.dumps({"operation": operation, "options": options}, ensure_ascii=False).encode()
            process = subprocess.Popen(_command(folder), cwd=ROOT, stdin=subprocess.PIPE,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       start_new_session=True)
            try:
                process.communicate(request, timeout=timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                raise ScreenshotError("网页截图超时，已结束该截图任务，请稍后重试") from exc
            finally:
                _stop(process)
                # Persist model usage even when the page or driver fails later.
                progress = folder / "progress.json"
                if on_progress and progress.is_file():
                    on_progress(json.loads(progress.read_text(encoding="utf-8")))
            result_file = folder / "result.json"
            if process.returncode or not result_file.is_file():
                raise ScreenshotError("网页截图进程未正常完成，请稍后重试")
            result = json.loads(result_file.read_text(encoding="utf-8"))
            if result.get("error"):
                error = PageUnavailable if result.get("failure_scope") == "page" else ScreenshotError
                raise error(result["error"])
            picture = folder / "image.png"
            if not picture.is_file() or not 0 < picture.stat().st_size <= MAX_BYTES:
                raise ScreenshotError("截图为空或超过 20 MB，请缩小截图范围")
            return picture.read_bytes(), result["metadata"]
    except OSError as exc:
        raise ScreenshotError("网页截图进程无法启动，请检查本机截图组件") from exc
    finally:
        _slots.release()

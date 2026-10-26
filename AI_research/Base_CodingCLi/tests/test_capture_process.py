"""A wedged browser must time out without sharing the API's Python runtime."""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.video import capture_process, screenshots
from web.services import video_visuals


def child_program(monkeypatch, source):
    monkeypatch.setattr(capture_process, "_command", lambda folder: [sys.executable, "-c", source, str(folder)])


def test_worker_returns_picture_metadata_and_model_usage(monkeypatch):
    child_program(monkeypatch, """
import json, sys
from pathlib import Path
folder = Path(sys.argv[1])
request = json.load(sys.stdin)
(folder / 'image.png').write_bytes(b'picture-bytes')
(folder / 'progress.json').write_text(json.dumps({'model_calls': 2}))
(folder / 'result.json').write_text(json.dumps({'metadata': {'title': request['options']['title']}}))
""")
    progress = []
    raw, metadata = capture_process.run("automatic", {"title": "网页区域"}, on_progress=progress.append)
    assert raw == b"picture-bytes" and metadata == {"title": "网页区域"}
    assert progress == [{"model_calls": 2}]


def test_busy_worker_is_killed_and_api_remains_responsive(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from web.server import create_app

    pid_file = tmp_path / "busy.pid"
    child_program(monkeypatch, f"""
import json, os, sys
from pathlib import Path
json.load(sys.stdin)
Path({str(pid_file)!r}).write_text(str(os.getpid()))
(Path(sys.argv[1]) / 'progress.json').write_text(json.dumps({{'model_calls': 1}}))
while True:
    pass
""")
    progress = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(capture_process.run, "automatic", {}, timeout_seconds=1.5, on_progress=progress.append)
        deadline = time.monotonic() + 1
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert pid_file.exists()
        # No lifespan: this check must not start any real jobs or workers.
        client = TestClient(create_app())
        assert not future.done()
        assert client.get("/api/live").status_code == 200
        with pytest.raises(screenshots.ScreenshotError, match="超时"):
            future.result(timeout=4)
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
    assert progress == [{"model_calls": 1}]
    # A timeout releases its slot; it must not permanently disable captures.
    test_worker_returns_picture_metadata_and_model_usage(monkeypatch)


def test_worker_failure_preserves_safe_page_error(monkeypatch):
    child_program(monkeypatch, """
import json, sys
from pathlib import Path
json.load(sys.stdin)
(Path(sys.argv[1]) / 'result.json').write_text(json.dumps({'error': '相关区域过长'}))
""")
    with pytest.raises(screenshots.ScreenshotError, match="相关区域过长"):
        capture_process.run("automatic", {})


def test_unexpected_exit_has_no_provider_details(monkeypatch):
    child_program(monkeypatch, "import sys; sys.stderr.write('private provider payload'); sys.exit(1)")
    with pytest.raises(screenshots.ScreenshotError, match="进程未正常完成") as error:
        capture_process.run("automatic", {})
    assert "private" not in str(error.value)


def test_real_worker_entry_point_reports_a_capture_error():
    with pytest.raises(screenshots.ScreenshotError, match="公开 HTTP/HTTPS"):
        screenshots.capture("http://127.0.0.1")


def test_worker_dispatches_automatic_capture_and_records_model_usage(monkeypatch):
    child_program(monkeypatch, """
from web.services import video_visual_acquire
from core.video.capture_worker import main

def acquire(need, url, policy, remaining):
    policy['_model_calls'] += 1
    policy['_on_model_call']()
    return b'captured-image', {'url': url, 'paragraph': need['id']}

video_visual_acquire.acquire = acquire
main()
""")
    progress = []
    raw, metadata = capture_process.run("automatic", {
        "need": {"id": "paragraph"}, "url": "https://example.com", "remaining": 10,
        "policy": {"_model_calls": 1},
    }, on_progress=progress.append)
    assert raw == b"captured-image" and metadata["paragraph"] == "paragraph"
    assert progress == [{"model_calls": 2}]


def test_automatic_capture_uses_process_budget_and_checkpoints_usage(monkeypatch):
    seen = []
    checkpoints = []

    def run(operation, options, **kwargs):
        seen.append((operation, options, kwargs["timeout_seconds"]))
        # The callback and its database connection stay in the API process.
        json.dumps(options)
        kwargs["on_progress"]({"model_calls": 3})
        return b"picture", {"title": "来源页面"}

    monkeypatch.setattr(capture_process, "run", run)
    policy = {"version": 3, "_model_calls": 2, "_on_model_call": lambda: checkpoints.append(policy["_model_calls"])}
    assert video_visuals.acquire({"id": "paragraph"}, "https://example.com", policy, remaining=12) == (
        b"picture", {"title": "来源页面"})
    assert seen[0][0] == "automatic" and seen[0][2] == 12
    assert seen[0][1]["policy"]["_model_calls"] == 2
    assert policy["_model_calls"] == 3 and checkpoints == [3]


def test_manual_capture_also_uses_disposable_worker(monkeypatch):
    calls = []
    monkeypatch.setattr(capture_process, "run", lambda *a, **k: calls.append((a, k)) or (b"image", {}))
    assert screenshots.capture("https://example.com", mode="element", selector="#content") == (b"image", {})
    assert calls[0][0] == ("screenshot", {"url": "https://example.com", "mode": "element", "selector": "#content", "clip": None})

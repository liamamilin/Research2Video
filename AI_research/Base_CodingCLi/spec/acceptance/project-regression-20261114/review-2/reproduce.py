"""Isolated failure-path reproductions; does not use production state/providers.

Run from Base_CodingCLi with ../.AI_research/bin/python <this file>.
The assertions describe the observed bugs, not desired behavior.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

RESEARCH = Path(__file__).resolve().parents[4]
ROOT = RESEARCH.parents[1]
sys.path.insert(0, str(RESEARCH))
sys.path.insert(0, str(ROOT / "autovideo"))

from core.engine import ResearchEngine
from web.runner.executor import run_job_in_thread
from web.runner.log_bus import LogBus
from web.runner.registry import RunningTask
from web.routes import jobs
from starlette.requests import Request
from g3.config import Settings
from g3.store import JobStore
from g3.worker import SingleWorker


def task(bus, engine=None):
    return RunningTask("isolated-review", "review/demo", "review", threading.Event(), bus, engine)


def constructor_failure():
    with tempfile.TemporaryDirectory(prefix="review-init-") as folder:
        root = Path(folder)
        config = root / "config"
        config.mkdir()
        (root / "jobs").mkdir()
        yaml_file = config / "system.yaml"
        yaml_file.write_text("{}\n")
        bus = LogBus("isolated-review")
        run = task(bus, ResearchEngine(str(config), str(root / "jobs")))
        # Emulate the config changing between admission and worker execution.
        release = threading.Event()
        with ThreadPoolExecutor(max_workers=1) as pool:
            blocker = pool.submit(release.wait)
            future = pool.submit(run_job_in_thread, run, str(config), str(root / "jobs"))
            run.future = future
            yaml_file.write_text("ai: [\n")
            release.set()
            blocker.result(timeout=2)
            error = future.exception(timeout=2)
        result = {"exception": type(error).__name__, "future_done": future.done(),
                  "task_status": run.status, "events": bus.replay()}
        assert error is not None and future.done() and run.is_running and not bus.replay()
        return result


async def sse_completion_gap():
    bus = LogBus("isolated-review")
    run = task(bus)
    bus.write({"type": "log", "message": "started"})
    request = Request({"type": "http", "headers": [], "method": "GET", "path": "/"})
    with patch.object(jobs, "_registry", SimpleNamespace(get=lambda name: run)):
        response = await jobs.stream_job(run.job_name, request, user={"role": "viewer"})
    first = await response.body_iterator.__anext__()
    # Worker finishes while the response is sending the replay, before subscribe().
    run.status = "success"
    bus.write({"type": "status", "status": "success"})
    pending = asyncio.create_task(response.body_iterator.__anext__())
    await asyncio.sleep(0.05)
    result = {"replayed_initial_log": "started" in first,
              "terminal_in_bus": bus.replay()[-1],
              "stream_waiting_after_completion": not pending.done()}
    assert not pending.done() and bus.replay()[-1]["status"] == "success"
    pending.cancel()
    try:
        await pending
    except (asyncio.CancelledError, StopAsyncIteration):
        pass
    await response.body_iterator.aclose()
    return result


def worker_failure_write_lock():
    with tempfile.TemporaryDirectory(prefix="review-worker-") as folder:
        settings = Settings(data_dir=Path(folder) / "data", worker_poll_seconds=0.01)
        store = JobStore(settings.data_dir)
        store.initialize()
        ids = [str(uuid4()), str(uuid4())]
        for ident in ids:
            store.create_job(ident, {"title": "isolated review", "source_text": "用于验证任务队列的隔离测试素材。" * 20,
                                    "target_duration_sec": 18, "theme": "minimal_dark", "voice_id": "zf_xiaoxiao"})
        connect = store._connect
        def fast_timeout():
            conn = connect()
            # Same SQLite failure as a lock lasting >15 s; shorten only test waiting.
            conn.execute("PRAGMA busy_timeout=10")
            return conn
        store._connect = fast_timeout
        blocker = sqlite3.connect(store.db_path, isolation_level=None, check_same_thread=False)
        processed, errors = [], []
        def execute(job, stop):
            processed.append(job["job_id"])
            blocker.execute("BEGIN IMMEDIATE")
            raise OSError("simulated execution I/O failure")
        worker = SingleWorker(settings, store, SimpleNamespace(execute=execute))
        try:
            with patch.object(threading, "excepthook", lambda args: errors.append(type(args.exc_value).__name__)):
                assert worker.start()
                worker.thread.join(timeout=3)
            blocker.rollback()
            lock_retained = False
            with (settings.data_dir / "worker.lock").open("a+") as second_lock:
                try:
                    fcntl.flock(second_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    lock_retained = True
            statuses = [store.get_job(ident)["status"] for ident in ids]
            result = {"worker_alive": worker.thread.is_alive(), "uncaught": errors,
                      "job_statuses": statuses, "processed_count": len(processed),
                      "exclusive_lock_retained": lock_retained}
            assert not worker.thread.is_alive() and errors == ["OperationalError"]
            assert sorted(statuses) == ["QUEUED", "RUNNING"] and lock_retained
            return result
        finally:
            blocker.rollback()
            blocker.close()
            worker.stop()


if __name__ == "__main__":
    results = {"research_constructor_failure": constructor_failure(),
               "sse_completion_gap": asyncio.run(sse_completion_gap()),
               "video_worker_failure_write_lock": worker_failure_write_lock()}
    print(json.dumps(results, ensure_ascii=False, indent=2))

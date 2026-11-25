from dataclasses import replace
import fcntl
import sqlite3
from types import SimpleNamespace
import threading
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from g3.app import create_app
from g3.config import Settings
from g3.store import JobStore
from g3.worker import G2ProcessRunner, SingleWorker


class StopEvent:
    def __init__(self):
        self.stopped = False
        self.delays = []

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def wait(self, seconds):
        self.delays.append(seconds)


def test_transient_claim_failure_does_not_stop_the_only_video_worker():
    calls, processed = [], []
    stop = StopEvent()

    def claim():
        calls.append(True)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        return {"job_id": "one", "attempt_no": 1}

    def execute(job, event):
        processed.append(job)
        event.set()

    worker = SingleWorker(Settings(), SimpleNamespace(claim_next=claim), SimpleNamespace(execute=execute))
    worker.stop_event = stop
    worker._loop()
    assert len(calls) == 2 and len(processed) == 1
    assert stop.delays == [1.0]


def test_idle_music_failure_does_not_stop_later_video_jobs():
    calls, processed = [], []
    stop = StopEvent()

    def claim():
        calls.append(True)
        return None if len(calls) == 1 else {"job_id": "one", "attempt_no": 1}

    def idle(_):
        raise OSError("external drive temporarily unavailable")

    def execute(job, event):
        processed.append(job)
        event.set()

    worker = SingleWorker(Settings(), SimpleNamespace(claim_next=claim), SimpleNamespace(execute=execute), idle_task=idle)
    worker.stop_event = stop
    worker._loop()
    assert len(processed) == 1 and stop.delays == [Settings().worker_poll_seconds]


def test_retry_backoff_respects_a_slower_configured_poll_interval():
    stop = StopEvent()

    def claim():
        stop.set()
        raise OSError("temporary I/O error")

    worker = SingleWorker(replace(Settings(), worker_poll_seconds=5), SimpleNamespace(claim_next=claim), None)
    worker.stop_event = stop
    worker._loop()
    assert stop.delays == [5]


def test_shutdown_does_not_release_exclusive_lock_while_worker_is_alive():
    lock = SimpleNamespace(close=lambda: (_ for _ in ()).throw(AssertionError("lock released too soon")))
    joins = []
    worker = SingleWorker(Settings(), None, None)
    worker._lock_file = lock
    worker.thread = SimpleNamespace(join=lambda **kw: joins.append(kw), is_alive=lambda: True)
    worker.stop()
    assert joins == [{"timeout": 12}]
    assert worker._lock_file is lock


def test_failure_write_retries_before_claiming_next_job(tmp_path, monkeypatch):
    store = JobStore(tmp_path / "data")
    store.initialize()
    ids = [str(uuid4()), str(uuid4())]
    for ident in ids:
        store.create_job(ident, {"title": "recovery", "source_text": "用于验证异常恢复的素材。" * 20,
                                "target_duration_sec": 18, "theme": "minimal_dark", "voice_id": "zf_xiaoxiao"})
    original_connect = store._connect

    def connect():
        conn = original_connect()
        conn.execute("PRAGMA busy_timeout=1")
        return conn

    monkeypatch.setattr(store, "_connect", connect)
    blocker = sqlite3.connect(store.db_path, isolation_level=None)
    processed, delays = [], []
    stop = threading.Event()

    def execute(job, event):
        processed.append(job["job_id"])
        if len(processed) == 1:
            blocker.execute("BEGIN IMMEDIATE")
            raise OSError("temporary disk failure")
        assert store.get_job(processed[0])["status"] == "FAILED"
        store.mark_completed(job["job_id"], job["attempt_no"], [])
        event.set()

    def wait(seconds):
        delays.append(seconds)
        # Restore the database after the first failed terminal-state write.
        blocker.rollback()
        return stop.is_set()

    monkeypatch.setattr(stop, "wait", wait)
    worker = SingleWorker(Settings(), store, SimpleNamespace(execute=execute))
    worker.stop_event = stop
    try:
        worker._loop()
        assert len(processed) == 2
        assert delays == [1.0]
        first = store.get_job(processed[0])
        assert first["status"] == "FAILED" and first["retryable"]
        assert first["error_code"] == "WORKER_UNEXPECTED_ERROR"
        assert "temporary disk failure" in first["error_message"]
        assert store.get_job(processed[1])["status"] == "COMPLETED"
    finally:
        blocker.rollback()
        blocker.close()


def test_failure_persistence_retry_stops_on_shutdown():
    calls = []
    stop = StopEvent()

    def fail(*args):
        calls.append(args)
        raise sqlite3.OperationalError("database is locked")

    def wait(seconds):
        stop.set()
        return True

    stop.wait = wait
    store = SimpleNamespace(claim_next=lambda: {"job_id": "one", "attempt_no": 1}, mark_failed=fail)
    worker = SingleWorker(Settings(), store, SimpleNamespace(execute=fail))
    worker.stop_event = stop
    worker._loop()
    assert len(calls) == 2


def test_transaction_closes_connection_when_begin_is_locked(tmp_path, monkeypatch):
    store = JobStore(tmp_path)
    cleanup = []

    def begin(sql):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "_connect", lambda: SimpleNamespace(
        execute=begin, rollback=lambda: cleanup.append("rollback"), close=lambda: cleanup.append("close")))
    with pytest.raises(sqlite3.OperationalError):
        with store._transaction():
            pytest.fail("locked transaction must not run its body")
    assert "close" in cleanup


def test_health_detects_a_primary_worker_that_has_exited(tmp_path, monkeypatch):
    alive = [True]

    def start(self):
        self.thread = SimpleNamespace(is_alive=lambda: alive[0])
        return True

    monkeypatch.setattr(SingleWorker, "start", start)
    monkeypatch.setattr(SingleWorker, "stop", lambda self: None)
    with TestClient(create_app(Settings(data_dir=tmp_path / "data"))) as client:
        assert client.get("/api/health").json() == {"status": "ok", "worker_primary": True}
        alive[0] = False
        assert client.get("/api/health").json() == {"status": "degraded", "worker_primary": False}


@pytest.mark.parametrize("start_worker", [True, False])
def test_secondary_app_never_recovers_the_active_workers_jobs(tmp_path, monkeypatch, start_worker):
    settings = Settings(data_dir=tmp_path / "data")
    store = JobStore(settings.data_dir)
    store.initialize()
    ident = str(uuid4())
    store.create_job(ident, {"title": "active", "source_text": "正在生成的任务素材。" * 20,
                            "target_duration_sec": 18, "theme": "minimal_dark", "voice_id": "zf_xiaoxiao"})
    store.claim_next()
    reaped = []
    monkeypatch.setattr(G2ProcessRunner, "reap_orphan", lambda self, pid: reaped.append(pid))
    # Represent an already-running primary process holding the same worker lock.
    with (settings.data_dir / "worker.lock").open("a+") as primary_lock:
        fcntl.flock(primary_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with TestClient(create_app(settings, start_worker=start_worker)) as client:
            assert client.get("/api/health").json()["worker_primary"] is False
            assert store.get_job(ident)["status"] == "RUNNING"
            assert reaped == []


def test_startup_failure_releases_worker_lock(tmp_path):
    def fail():
        raise OSError("temporary recovery error")

    worker = SingleWorker(Settings(data_dir=tmp_path), None, None, on_start=fail)
    try:
        with pytest.raises(OSError):
            worker.start()
        with (tmp_path / "worker.lock").open("a+") as replacement:
            fcntl.flock(replacement, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        worker.stop()

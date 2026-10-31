from dataclasses import replace
import sqlite3
from types import SimpleNamespace

from g3.config import Settings
from g3.worker import SingleWorker


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

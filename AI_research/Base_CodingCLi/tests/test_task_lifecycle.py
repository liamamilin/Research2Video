"""Cancellation and failed admission, isolated from live tasks and activity files."""
import asyncio
from concurrent.futures import Future
import threading
from types import SimpleNamespace

import pytest

from web.runner import executor
from web.runner.registry import TaskRegistry


@pytest.fixture
def registry(monkeypatch):
    registry = object.__new__(TaskRegistry)
    registry._tasks, registry._retiring = {}, {}
    registry._lock = threading.Lock()
    monkeypatch.setattr(executor, "TaskRegistry", lambda: registry)
    return registry


def test_cancel_notifies_a_subscriber_waiting_for_logs(registry):
    task = registry.start("demo", "test", None)
    task.future = Future()  # Cancellation is cooperative: work has not exited yet.

    async def run():
        q = task.log_bus.subscribe()
        waiting = asyncio.create_task(q.get())
        await asyncio.sleep(0)
        assert registry.cancel("demo")
        try:
            event = await asyncio.wait_for(waiting, 0.2)
            assert event["type"] == "status" and event["status"] == "cancelled"
            assert task.cancel_token.is_set()
            assert task in registry.all_tasks().values() and not task.future.done()
        finally:
            task.log_bus.unsubscribe(q)

    asyncio.run(run())


def test_cancelled_queued_task_never_initializes_engine(registry, monkeypatch):
    task = registry.start("demo", "test", None)
    registry.cancel("demo")
    initialized = []
    monkeypatch.setattr(executor, "ResearchEngine", lambda **kwargs: initialized.append(kwargs))
    executor.run_job_in_thread(task, "unused", "unused")
    assert initialized == []
    assert task.status == "cancelled"


def test_error_during_cooperative_cancel_keeps_cancelled_status(registry, monkeypatch):
    task = registry.start("demo", "test", None)

    def run_job(*args, **kwargs):
        registry.cancel("demo")
        raise OSError("connection closed during cancellation")

    monkeypatch.setattr(executor, "ResearchEngine", lambda **kw: SimpleNamespace(run_job=run_job))
    executor.run_job_in_thread(task, "unused", "unused")
    assert task.status == "cancelled"
    assert task.log_bus.replay()[-1]["status"] == "cancelled"
    assert not any(event["type"] == "error" for event in task.log_bus.replay())


def test_pool_rejection_does_not_leave_a_running_task(registry, monkeypatch):
    from core import matrices
    monkeypatch.setattr(matrices, "cap_block", lambda *args: None)
    monkeypatch.setattr(executor, "ResearchEngine", lambda **kwargs: object())
    monkeypatch.setattr(executor, "_attach_persistence", lambda *args: None)

    def reject(*args, **kwargs):
        raise RuntimeError("cannot schedule new futures after shutdown")

    monkeypatch.setattr(executor, "_executor", SimpleNamespace(submit=reject))
    with pytest.raises(RuntimeError, match="cannot schedule"):
        executor.start_job("demo", "test")
    task = registry.get("demo")
    assert task.status == "failed" and not registry.is_running("demo")
    assert task.log_bus.replay()[-1]["status"] == "failed"


def test_replaced_task_is_tracked_before_future_assignment(registry):
    first = registry.start("demo", "test", None)
    second = registry.start("demo", "test", None)
    assert first.cancel_token.is_set()
    assert set(registry.all_tasks().values()) == {first, second}
    first.future = Future()
    first.status = "cancelled"
    assert first in registry.all_tasks().values()
    first.future.set_result(None)
    assert first not in registry.all_tasks().values()


def test_pool_rejection_cannot_run_a_work_item_already_queued(registry, monkeypatch):
    from core import matrices
    initialized, queued = [], []
    monkeypatch.setattr(matrices, "cap_block", lambda *args: None)
    monkeypatch.setattr(executor, "_attach_persistence", lambda *args: None)
    monkeypatch.setattr(executor, "ResearchEngine", lambda **kwargs: initialized.append(kwargs))

    def reject_after_enqueue(callback, *args, **kwargs):
        queued.append(lambda: callback(*args, **kwargs))
        raise RuntimeError("cannot start new thread")

    monkeypatch.setattr(executor, "_executor", SimpleNamespace(submit=reject_after_enqueue))
    with pytest.raises(RuntimeError, match="cannot start"):
        executor.start_job("demo", "test")
    # ThreadPoolExecutor enqueues before adjusting thread count. Another worker
    # can eventually pick up the item even though submit raised to the caller.
    queued[0]()
    assert len(initialized) == 1  # Only the admission-time engine stub.
    assert registry.get("demo").status == "failed"

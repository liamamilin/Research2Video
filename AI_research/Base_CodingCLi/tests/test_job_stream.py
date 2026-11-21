"""Real LogBus / SSE handoff, without a server or production task registry."""
import asyncio
import json
import threading
from types import SimpleNamespace

from starlette.requests import Request

from web.routes import jobs
from web.runner.log_bus import LogBus
from web.runner.registry import RunningTask


def make_task(monkeypatch):
    bus = LogBus("stream-test")
    task = RunningTask("stream-test", "demo", "tester", threading.Event(), bus, None)
    monkeypatch.setattr(jobs, "_registry", SimpleNamespace(get=lambda name: task))
    return task, bus


async def stream(task, last_id=None):
    headers = [(b"last-event-id", str(last_id).encode())] if last_id is not None else []
    request = Request({"type": "http", "headers": headers, "method": "GET", "path": "/"})
    response = await jobs.stream_job(task.job_name, request, user={"role": "viewer"})
    return response.body_iterator


async def read_event(iterator):
    raw = await asyncio.wait_for(anext(iterator), timeout=1)
    return json.loads(next(line[6:] for line in raw.splitlines() if line.startswith("data: ")))


async def assert_closed(iterator):
    pending = asyncio.create_task(anext(iterator))
    done, _ = await asyncio.wait({pending}, timeout=0.2)
    if not done:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        raise AssertionError("terminal status must close the stream without cancellation")
    try:
        pending.result()
    except StopAsyncIteration:
        return
    raise AssertionError("terminal status must close the stream")


def test_completion_during_replay_includes_final_logs_and_closes(monkeypatch):
    task, bus = make_task(monkeypatch)
    bus.write({"type": "log", "message": "started"})

    async def run():
        iterator = await stream(task)
        assert (await read_event(iterator))["message"] == "started"
        bus.write({"type": "log", "message": "last output"})
        task.status = "success"
        bus.write({"type": "status", "status": "success"})
        assert (await read_event(iterator))["message"] == "last output"
        assert (await read_event(iterator))["status"] == "success"
        await assert_closed(iterator)
        assert not bus._subscribers

    asyncio.run(run())


def test_overlap_between_replay_and_subscription_is_delivered_once(monkeypatch):
    task, bus = make_task(monkeypatch)
    original_replay = bus.replay

    def replay(since_id=0):
        bus.write({"type": "log", "message": "overlap"})
        return original_replay(since_id)

    monkeypatch.setattr(bus, "replay", replay)

    async def run():
        iterator = await stream(task)
        assert (await read_event(iterator))["message"] == "overlap"
        # Keep task running here so the next event uses the live queue.
        bus.write({"type": "status", "status": "success"})
        assert (await read_event(iterator))["status"] == "success"
        await assert_closed(iterator)

    asyncio.run(run())


def test_finished_reconnect_with_no_new_events_returns_current_status(monkeypatch):
    task, bus = make_task(monkeypatch)
    task.status = "success"
    bus.write({"type": "status", "status": "success"})

    async def run():
        iterator = await stream(task, last_id=bus.last_event_id)
        assert (await read_event(iterator))["status"] == "success"
        await assert_closed(iterator)
        assert not bus._subscribers

    asyncio.run(run())


def test_error_does_not_hide_the_final_status(monkeypatch):
    task, bus = make_task(monkeypatch)
    bus.write({"type": "error", "message": "failed to initialize"})
    bus.write({"type": "status", "status": "failed"})
    task.status = "failed"

    async def run():
        iterator = await stream(task)
        assert (await read_event(iterator))["type"] == "error"
        assert (await read_event(iterator))["status"] == "failed"
        await assert_closed(iterator)

    asyncio.run(run())


def test_disconnect_removes_subscription(monkeypatch):
    task, bus = make_task(monkeypatch)
    bus.write({"type": "log", "message": "started"})

    async def run():
        iterator = await stream(task)
        await read_event(iterator)
        await iterator.aclose()
        assert not bus._subscribers

    asyncio.run(run())


def test_subscription_receives_worker_event_before_first_queue_read():
    async def run():
        bus = LogBus("early")
        q = bus.subscribe()
        await asyncio.to_thread(bus.write, {"type": "status", "status": "success"})
        assert (await asyncio.wait_for(q.get(), timeout=1))["status"] == "success"
        bus.unsubscribe(q)

    asyncio.run(run())


def test_slow_subscriber_retains_terminal_event_without_callback_errors():
    async def run():
        bus = LogBus("slow")
        q = bus.subscribe()
        errors = []
        asyncio.get_running_loop().set_exception_handler(lambda loop, context: errors.append(context))
        for number in range(q.maxsize + 10):
            bus.write({"type": "log", "message": str(number)})
        bus.write({"type": "status", "status": "success"})
        await asyncio.sleep(0)
        events = [q.get_nowait() for _ in range(q.qsize())]
        assert events[-1]["status"] == "success"
        assert len(events) == q.maxsize
        assert [event["_id"] for event in events] == sorted(event["_id"] for event in events)
        assert not errors
        bus.unsubscribe(q)

    asyncio.run(run())

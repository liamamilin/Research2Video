"""Per-task ring buffer + fan-out for SSE log streaming.

Thread-safe: write() is called from worker threads, subscribe/unsubscribe
from the asyncio event loop thread. Delivery to subscribers uses
loop.call_soon_threadsafe to avoid corrupting asyncio.Queue internals.
"""

from __future__ import annotations

import asyncio
import threading
from collections import deque

_LOG_BUFFER_SIZE = 5000


class LogBus:
    """Per-task event bus.

    * Maintains a ring buffer of recent events (thread-safe via _lock).
    * Fans out new events to all SSE subscribers via call_soon_threadsafe.
    * Supports Last-Event-ID reconnection via replay().
    """

    def __init__(self, task_id: str):
        self.task_id = task_id
        self._buffer: deque[dict] = deque(maxlen=_LOG_BUFFER_SIZE)
        self._subscribers: dict[asyncio.Queue, asyncio.AbstractEventLoop] = {}
        self._lock = threading.Lock()
        self._counter = 0

    def write(self, event: dict):
        """Write an event and deliver to all subscribers.

        Can be safely called from any thread.
        """
        with self._lock:
            self._counter += 1
            event["_id"] = self._counter
            self._buffer.append(event.copy())
            # Schedule under the lock so concurrent writers preserve event-ID order.
            for q, loop in self._subscribers.items():
                try:
                    loop.call_soon_threadsafe(self._deliver, q, event.copy())
                except RuntimeError:
                    # A disconnected subscriber's event loop may have closed.
                    pass

    @staticmethod
    def _deliver(q: asyncio.Queue, event: dict) -> None:
        # Slow clients retain the newest events, including the terminal status.
        # This runs on the subscriber loop, never on a producer thread.
        if q.full():
            q.get_nowait()
        q.put_nowait(event)

    def replay(self, since_id: int = 0) -> list[dict]:
        """Return all events with _id > since_id (for SSE reconnection)."""
        # Work on a snapshot to avoid holding lock during copy
        with self._lock:
            events = list(self._buffer)
        return [e for e in events if e.get("_id", 0) > since_id]

    def subscribe(self) -> asyncio.Queue:
        """Add a subscriber queue. Returns the queue."""
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        loop = asyncio.get_running_loop()
        with self._lock:
            self._subscribers[q] = loop
        return q

    def unsubscribe(self, q: asyncio.Queue):
        with self._lock:
            self._subscribers.pop(q, None)

    @property
    def last_event_id(self) -> int:
        return self._counter

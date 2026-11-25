"""Short-lived, revision-keyed in-process cache for expensive read-only list endpoints."""
from __future__ import annotations

import glob
import os
import threading
import time
from collections.abc import Callable
from typing import Any, TypeVar

from core.video import store

T = TypeVar("T")

_lock = threading.Lock()
_entries: dict[str, dict[Any, tuple[float, Any]]] = {}
_MAX_KEYS_PER_NAMESPACE = 16


def _stat_pair(path: str) -> tuple[int, int]:
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return (0, 0)


def state_revision(state_dir: str) -> tuple:
    """Fast (~0.1 ms) composite revision of video.db and platform SQLite stores."""
    vrev = store.db_revision()
    db_files = [
        os.path.join(state_dir, "bilibili.db"),
        os.path.join(state_dir, "douyin.db"),
        *sorted(glob.glob(os.path.join(state_dir, "social_*.db"))),
    ]
    extra = tuple((_stat_pair(p), _stat_pair(p + "-wal")) for p in db_files)
    return (vrev, extra)


def cached(namespace: str, key: Any, ttl_seconds: float, compute: Callable[[], T]) -> T:
    now = time.monotonic()
    with _lock:
        bucket = _entries.get(namespace)
        if bucket and key in bucket:
            expires_at, value = bucket[key]
            if now < expires_at:
                return value
    value = compute()
    with _lock:
        bucket = _entries.setdefault(namespace, {})
        bucket[key] = (time.monotonic() + ttl_seconds, value)
        if len(bucket) > _MAX_KEYS_PER_NAMESPACE:
            oldest = next(iter(bucket))
            bucket.pop(oldest, None)
    return value


def invalidate(namespace: str | None = None) -> None:
    with _lock:
        if namespace is None:
            _entries.clear()
        else:
            _entries.pop(namespace, None)

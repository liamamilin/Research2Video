"""Usage and timing records: ``<state>/telemetry.db``, append only.

Two tables: ``calls`` (one model or search request per row) and ``spans`` (one production stage per row). Both carry
the same attribution columns (which script, render, matrix, column, recipe, form...), so a question like "what did the
write stage cost for this column" needs no join.

Attribution is ambient: ``track(stage, script_id=...)`` puts it in a ``contextvars`` context and every call recorded
inside inherits it, so the ~20 places that build an ``LLMClient`` stay untouched. ``contextvars`` do not follow a new
``threading.Thread``; enter ``track`` inside the thread function.

Nothing is recorded until ``use_state_dir`` names a directory (``core.state`` does this), so tests and one-off scripts
never write a shared file. Every write is best effort: a failure is logged and the caller carries on.
"""
from __future__ import annotations

import contextvars
import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator, Optional
from urllib.parse import urlparse

from core import sqlite_util

logger = logging.getLogger(__name__)

FILE_NAME = "telemetry.db"
#: Attribution carried from ``track`` into every span and call recorded inside it.
ATTRS = ("stage", "script_id", "render_id", "matrix", "column_key", "recipe", "form", "path",
         "batch_id", "item_id", "job", "run_ts")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS spans (
    id TEXT PRIMARY KEY,
    source_key TEXT UNIQUE,
    {attrs},
    start REAL, end REAL, duration_s REAL,
    status TEXT NOT NULL DEFAULT 'ok',
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_spans_script ON spans(script_id);
CREATE INDEX IF NOT EXISTS idx_spans_render ON spans(render_id);
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key TEXT UNIQUE,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,                      -- llm | search
    provider TEXT NOT NULL DEFAULT '',       -- host of the endpoint, or the search provider
    model TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',         -- the caller's own name for the step (a chapter id, a job)
    span_id TEXT,
    {attrs},
    requests INTEGER NOT NULL DEFAULT 1,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    estimated INTEGER NOT NULL DEFAULT 0,    -- the endpoint sent no usage; tokens are a guess
    error_code TEXT NOT NULL DEFAULT '',
    fell_back_from TEXT NOT NULL DEFAULT '',
    reported_cost_usd REAL,                  -- what the provider itself charged, when it said
    snapshot_in_per_m REAL,                  -- the unit price recorded at call time, when the run kept one
    snapshot_out_per_m REAL,
    duration_s REAL
);
CREATE INDEX IF NOT EXISTS idx_calls_ts ON calls(ts);
CREATE INDEX IF NOT EXISTS idx_calls_script ON calls(script_id);
CREATE INDEX IF NOT EXISTS idx_calls_render ON calls(render_id);
""".format(attrs=",\n    ".join(f"{name} TEXT NOT NULL DEFAULT ''" for name in ATTRS))

_dir: Optional[str] = None
_ready: set[str] = set()
_lock = threading.Lock()
_context: contextvars.ContextVar[dict] = contextvars.ContextVar("telemetry_context", default={})


def use_state_dir(state_dir: Optional[str]) -> None:
    global _dir
    _dir = state_dir


def db_path() -> Optional[str]:
    return os.path.join(_dir, FILE_NAME) if _dir else None


def enabled() -> bool:
    return bool(_dir)


def _prepare(path: str) -> None:
    with _lock:
        if path in _ready:
            return
        with sqlite_util.connect(path, write=True) as conn:
            conn.executescript(_SCHEMA)
            # Rows older than this were never seen live; backfill may add them, and nothing newer, so a record that
            # exists both live and in an older log is never counted twice.
            conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('live_since', ?)", (str(time.time()),))
        _ready.add(path)


@contextmanager
def connect(write: bool = False) -> Iterator[Any]:
    """The database as a connection, or ``None`` when no state directory was named."""
    path = db_path()
    if not path:
        yield None
        return
    _prepare(path)
    with sqlite_util.connect(path, write=write) as conn:
        yield conn


def live_since() -> float:
    with connect() as conn:
        if conn is None:
            return 0.0
        row = conn.execute("SELECT value FROM meta WHERE key = 'live_since'").fetchone()
    return float(row[0]) if row else 0.0


def current() -> dict:
    return dict(_context.get())


@contextmanager
def bind(**attrs: str) -> Iterator[dict]:
    """Attribute every call inside the block, without recording a span."""
    merged = {**_context.get(), **{k: str(v) for k, v in attrs.items() if k in ATTRS and v not in (None, "")}}
    token = _context.set(merged)
    try:
        yield merged
    finally:
        _context.reset(token)


@contextmanager
def track(stage: str, **attrs: str) -> Iterator[dict]:
    """Time one production stage and attribute every call inside it; the span is written on exit.

    Set ``span["status"] = "failed"`` inside the block when the work fails without raising."""
    span_id = uuid.uuid4().hex
    merged = {**_context.get(), **{k: str(v) for k, v in attrs.items() if k in ATTRS and v not in (None, "")},
              "stage": stage, "span_id": span_id}
    token = _context.set(merged)
    start, status = time.time(), "ok"
    try:
        yield merged
        status = merged.get("status") or status  # the block may judge its own outcome without raising
    except BaseException as exc:
        status = "cancelled" if type(exc).__name__ == "CancelledError" else "failed"
        raise
    finally:
        _context.reset(token)
        end = time.time()
        _insert("spans", {"id": span_id, "start": start, "end": end, "duration_s": round(end - start, 3),
                          "status": status, **{k: merged.get(k, "") for k in ATTRS}})


def script_attrs(script: Optional[dict]) -> dict:
    """Which video a call belongs to, from its script record."""
    script = script or {}
    return {"script_id": script.get("id", ""), "column_key": script.get("column_key", ""),
            "matrix": script.get("content_matrix_id", "") or "", "recipe": script.get("production", "") or ""}


class Phases:
    """Consecutive stages reported by a callback: entering one ends the one before, and ``labels`` renames them.

    For a pipeline that announces "now writing", "now auditing" as it goes (the adapt pipeline's ``on_stage``)
    instead of letting the caller wrap each part. Use as a context manager; the open stage is closed on exit.
    """

    def __init__(self, labels: Optional[dict] = None, **attrs: str):
        self._labels, self._attrs, self._open = labels or {}, attrs, None

    def enter(self, label: str) -> None:
        self.close()
        manager = track(self._labels.get(label, label), **self._attrs)
        manager.__enter__()
        self._open = manager

    def close(self, error: Optional[BaseException] = None) -> None:
        manager, self._open = self._open, None
        if manager is not None:
            if error is None:
                manager.__exit__(None, None, None)
            else:
                manager.__exit__(type(error), error, error.__traceback__)

    def __enter__(self) -> "Phases":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close(exc)
        return False


def _insert(table: str, row: dict) -> None:
    try:
        with connect(write=True) as conn:
            if conn is None:
                return
            names = list(row)
            conn.execute(f"INSERT OR IGNORE INTO {table} ({','.join(names)}) VALUES ({','.join('?' * len(names))})",
                         [row[name] for name in names])
    except Exception as exc:  # noqa: BLE001 - recording must never break the work it describes
        logger.debug("telemetry %s skipped: %s", table, exc)


def provider_of(base_url: str) -> str:
    """The endpoint's host: it names the account that bills, which a model id alone does not."""
    return (urlparse(base_url or "").hostname or "").lower()


def _number(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def record_call(kind: str, *, provider: str = "", model: str = "", usage: Optional[dict] = None,
                duration_s: Optional[float] = None, error_code: str = "", fell_back_from: str = "",
                detail: str = "", ts: Optional[float] = None, source_key: Optional[str] = None,
                requests: int = 1, snapshot: Optional[tuple] = None, **attrs: str) -> None:
    """One request (or ``requests`` of them, for a backfilled run total) to a model or a search provider."""
    if not enabled():
        return
    usage = usage or {}
    ambient = _context.get()
    row = {
        "source_key": source_key, "ts": ts if ts is not None else time.time(), "kind": kind, "provider": provider,
        "model": model, "detail": detail, "span_id": ambient.get("span_id"), "requests": requests,
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "reasoning_tokens": int(usage.get("reasoning_tokens") or 0),
        "cache_read_tokens": int(usage.get("cache_read_tokens") or 0),
        "estimated": 1 if usage.get("estimated") else 0, "error_code": error_code, "fell_back_from": fell_back_from,
        "reported_cost_usd": _number(usage.get("cost_usd")), "duration_s": duration_s,
        "snapshot_in_per_m": snapshot[0] if snapshot else None,
        "snapshot_out_per_m": snapshot[1] if snapshot else None,
        **{name: str(attrs.get(name) or ambient.get(name) or "") for name in ATTRS},
    }
    _insert("calls", row)


def record_span(stage: str, *, start: Optional[float] = None, end: Optional[float] = None,
                duration_s: Optional[float] = None, status: str = "ok", note: str = "",
                source_key: Optional[str] = None, **attrs: str) -> None:
    """A span measured elsewhere (a render's step durations read from its log)."""
    if not enabled():
        return
    if duration_s is None and start is not None and end is not None:
        duration_s = round(end - start, 3)
    ambient = _context.get()
    _insert("spans", {"id": uuid.uuid4().hex, "source_key": source_key, "start": start, "end": end,
                      "duration_s": duration_s, "status": status, "note": note,
                      **{name: str(attrs.get(name) or ambient.get(name) or "") for name in ATTRS},
                      "stage": stage})


def record_llm(client: Any, usage: dict, *, error_code: str = "") -> None:
    """Called by ``LLMClient`` for every request it makes; reads only what the client already knows."""
    if not enabled():
        return
    started = getattr(client, "_call_started", None)
    switches = getattr(client, "model_switches", None) or []
    record_call(
        "llm", provider=provider_of(getattr(getattr(client, "config", None), "base_url", "")),
        model=client._active_model(), usage=usage, error_code=error_code,
        duration_s=round(time.monotonic() - started, 3) if started else None,
        fell_back_from=switches[-1]["from"] if switches and getattr(client, "_model_idx", 0) else "")


def record_search(provider: str, error: str, started: float) -> None:
    """One attempt on one search provider: ``error`` is empty when it returned results."""
    record_call("search", provider=provider, error_code=error[:200], duration_s=round(time.monotonic() - started, 3))

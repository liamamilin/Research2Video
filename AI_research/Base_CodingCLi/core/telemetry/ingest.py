"""Bring records that already exist into ``telemetry.db``: finished renders and the older usage logs.

Every row carries a ``source_key`` and goes in with ``INSERT OR IGNORE``, so reading the same source twice changes
nothing. Backfill takes only records older than ``live_since``: anything newer is recorded live by ``LLMClient`` and
``track``, and reading it again here would count it twice.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Optional

from core.telemetry import store

logger = logging.getLogger(__name__)

TERMINAL_RENDER = ("COMPLETED", "FAILED")
_STEP = re.compile(r"^STEP (\S+): (PASS|FAIL\w*) in ([\d.]+)s", re.M)
_CHAPTER = re.compile(r"^(?:chapter-)?c\d+-(explanation|diagram|information)$")


def normalize_stage(raw: str) -> str:
    """Per-chapter call names (``c3-explanation``, ``chapter-c5-diagram``) collapse to their kind."""
    match = _CHAPTER.match(raw or "")
    return match[1] if match else (raw or "unknown")


def _epoch(text: Optional[str]) -> Optional[float]:
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text or "", fmt).timestamp()
        except ValueError:
            continue
    return None


def _done(conn, key: str) -> bool:
    return conn.execute("SELECT 1 FROM meta WHERE key = ?", (key,)).fetchone() is not None


def _mark(key: str) -> None:
    with store.connect(write=True) as conn:
        if conn is not None:
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, str(time.time())))


def _script_attrs(script: Optional[dict]) -> dict:
    script = script or {}
    return {"script_id": script.get("id", ""), "column_key": script.get("column_key", ""),
            "matrix": script.get("content_matrix_id", "") or "", "recipe": script.get("production", "") or ""}


def ingest_render(render: dict, script: Optional[dict], render_dir: str) -> int:
    """Read one finished render's cost file and step log; the number of rows offered (existing ones are ignored)."""
    attrs = {**_script_attrs(script), "render_id": render["id"], "form": render.get("resolved_video_form") or ""}
    finished = _epoch(render.get("completed_at")) or _epoch(render.get("updated_at")) or time.time()
    rows = 0
    try:
        with open(os.path.join(render_dir, "model-cost.json"), encoding="utf-8") as handle:
            cost = json.load(handle)
    except (OSError, ValueError):
        cost = {}
    host = store.provider_of(cost.get("endpoint", ""))
    for number, call in enumerate(cost.get("calls") or []):
        snapshot = call.get("pricing_snapshot") or {}
        prices = (snapshot.get("input_uncached_peak_usd_per_million"), snapshot.get("output_peak_usd_per_million"))
        call_id = str(call.get("call_id") or number)
        store.record_call(
            "llm", provider=host, model=call.get("model") or cost.get("model", ""), detail=call_id, ts=finished,
            usage={key: call.get(key) for key in ("prompt_tokens", "completion_tokens", "reasoning_tokens")},
            duration_s=call.get("elapsed_seconds"), source_key=f"render:{render['id']}:{call_id}",
            snapshot=prices if all(isinstance(p, (int, float)) for p in prices) else None,
            **{**attrs, "stage": f"render/{normalize_stage(call.get('stage', call_id))}"})
        rows += 1
    try:
        with open(os.path.join(render_dir, "execution-log.txt"), encoding="utf-8", errors="replace") as handle:
            log = handle.read()
    except OSError:
        log = ""
    for name, outcome, seconds in _STEP.findall(log):
        store.record_span(f"render/{name}", duration_s=float(seconds), status="ok" if outcome == "PASS" else "failed",
                          source_key=f"render:{render['id']}:step:{name}", **attrs)
        rows += 1
    start = _epoch(render.get("submitted_at"))
    if start:
        store.record_span("render", start=start, end=finished, status="ok" if render["status"] == "COMPLETED" else "failed",
                          source_key=f"render:{render['id']}:total", **attrs)
        rows += 1
    return rows


def sweep_renders(limit: int = 500) -> int:
    """Ingest every finished render not seen before. Cheap when nothing is new: one set lookup per render."""
    if not store.enabled():
        return 0
    from core.video import store as video_store
    try:
        with video_store.connect() as vconn:
            renders = [dict(r) for r in vconn.execute(
                "SELECT id, script_id, status, resolved_video_form, submitted_at, completed_at, updated_at FROM renders"
                f" WHERE status IN ({','.join('?' * len(TERMINAL_RENDER))}) ORDER BY created_at DESC",
                TERMINAL_RENDER).fetchall()]
        with store.connect() as conn:
            seen = {r[0] for r in conn.execute("SELECT key FROM meta WHERE key LIKE 'render:%'")}
    except Exception as exc:  # noqa: BLE001
        logger.warning("render ingest skipped: %s", exc)
        return 0
    base = os.path.join(store._dir or "", "video_renders")
    count = 0
    for render in renders:
        if f"render:{render['id']}" in seen or count >= limit:
            continue
        directory = os.path.join(base, render["id"])
        if not os.path.isdir(directory):
            continue
        ingest_render(render, video_store.get_script(render["script_id"]), directory)
        _mark(f"render:{render['id']}")
        count += 1
    return count


def backfill_history(history_dir: str) -> int:
    """Job runs and assistant calls from ``state/history/*.jsonl`` that predate live recording. Runs once."""
    if not store.enabled():
        return 0
    with store.connect() as conn:
        if _done(conn, "backfill:history"):
            return 0
    cutoff, rows = store.live_since(), 0
    for name in sorted(os.listdir(history_dir)) if os.path.isdir(history_dir) else []:
        if not name.endswith(".jsonl"):
            continue
        try:
            with open(os.path.join(history_dir, name), encoding="utf-8") as handle:
                lines = handle.readlines()
        except OSError:
            continue
        for number, line in enumerate(lines):
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            ts = _epoch(str(entry.get("ts", "")))
            usage = entry.get("usage") or {}
            if ts is None or ts >= cutoff:
                continue
            job = str(entry.get("job_name") or name[:-6])
            stage = job.strip("_") if job.startswith("__") else "research"
            key = f"history:{name}:{entry.get('ts')}:{number}"
            attrs = {"job": job, "run_ts": str(entry.get("ts", ""))}
            if usage.get("requests"):
                store.record_call("llm", model=str(usage.get("model") or ""), usage=usage, ts=ts, stage=stage,
                                  requests=int(usage.get("requests") or 1), source_key=key, **attrs)
                rows += 1
            if usage.get("searches"):
                store.record_call("search", ts=ts, stage=stage, requests=int(usage["searches"]),
                                  source_key=f"{key}:search", **attrs)
                rows += 1
            if isinstance(entry.get("duration_seconds"), (int, float)):
                store.record_span(stage, start=ts - entry["duration_seconds"], end=ts,
                                  duration_s=float(entry["duration_seconds"]),
                                  status="ok" if entry.get("status") == "success" else "failed",
                                  source_key=f"{key}:span", **attrs)
                rows += 1
    _mark("backfill:history")
    return rows


def backfill_versions() -> int:
    """Writing and audit usage stored in script version meta, before live recording. Runs once."""
    if not store.enabled():
        return 0
    with store.connect() as conn:
        if _done(conn, "backfill:versions"):
            return 0
    from core.video import store as video_store
    cutoff, rows = store.live_since(), 0
    try:
        with video_store.connect() as vconn:
            versions = [dict(r) for r in vconn.execute(
                "SELECT v.script_id, v.version, v.meta, v.created_at, s.column_key, s.content_matrix_id, s.production"
                " FROM script_versions v JOIN scripts s ON s.id = v.script_id").fetchall()]
    except Exception as exc:  # noqa: BLE001
        logger.warning("version backfill skipped: %s", exc)
        return 0
    for version in versions:
        ts = _epoch(version["created_at"])
        if ts is None or ts >= cutoff:
            continue
        try:
            meta = json.loads(version["meta"] or "{}")
        except ValueError:
            continue
        attrs = {"script_id": version["script_id"], "column_key": version["column_key"],
                 "matrix": version["content_matrix_id"] or "", "recipe": version["production"] or ""}
        for field, stage in (("usage", "write"), ("audit_usage", "audit"), ("review_usage", "audit")):
            usage = meta.get(field) or {}
            if usage.get("requests"):
                store.record_call("llm", model=str(usage.get("model") or ""), usage=usage, ts=ts,
                                  requests=int(usage["requests"]), detail=field,
                                  source_key=f"version:{version['script_id']}:{version['version']}:{field}",
                                  **attrs, stage=stage)
                rows += 1
    _mark("backfill:versions")
    return rows


def run_all(history_dir: str) -> dict:
    """Everything that can be read without a provider: safe to call on every start and from the API."""
    return {"renders": sweep_renders(), "history": backfill_history(history_dir), "versions": backfill_versions()}

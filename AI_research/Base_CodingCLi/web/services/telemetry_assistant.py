"""The assistant breakdown with what the user asked next to each request (the question lives in the assistant's own store)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from core.telemetry import assistant

PREVIEW = 80


def _questions(state_dir: str, ids: list[str]) -> dict:
    path = Path(state_dir) / "assistant.db"
    if not ids or not path.is_file():
        return {}
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2) as db:
            rows = db.execute(f"SELECT id, message FROM requests WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
    except sqlite3.Error:
        return {}
    return {request_id: " ".join(str(message or "").split())[:PREVIEW] for request_id, message in rows}


def report(config_dir: str, state_dir: str, since, until, limit: int, offset: int, origin=None) -> dict:
    result = assistant.report(config_dir, since, until, limit, offset, origin)
    if result.get("enabled"):
        asked = _questions(state_dir, [r["request_id"] for r in result["requests"]])
        for request in result["requests"]:
            request["question"] = asked.get(request["request_id"], "")
    return result

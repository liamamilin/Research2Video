"""The assistant, request by request and turn by turn: what each model turn was sent, asked for, and cost.

A request is one user message; the assistant answers it over several model turns, calling tools in between. The
tokens of each turn are ordinary ``calls`` rows (attributed with ``request_id`` and ``turn``). What the calls cannot
say is kept in ``assistant_rounds``: how large the conversation and the tool list were, and how large each tool's
answer was. Only sizes are kept, never message text.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

from core.telemetry import query, store

logger = logging.getLogger(__name__)


class Recorder:
    """Writes one ``assistant_rounds`` row per model turn; a no-op without a request id or a state directory."""

    def __init__(self, request_id: Optional[str], conversation_id: Optional[str], user: str, origin: str = ""):
        self.request_id, self.conversation_id, self.user = request_id or "", conversation_id or "", user or ""
        self.origin = origin or ""
        self._rows: dict[int, dict] = {}

    def round(self, turn: int, metrics: dict, tool_calls: list) -> None:
        """A turn was answered: what it was sent (``metrics``) and which tools the model asked for."""
        if not self.request_id:
            return
        self._rows[turn] = {
            "request_id": self.request_id, "turn": turn, "ts": _now(), "conversation_id": self.conversation_id,
            "user": self.user, "origin": self.origin, "message_chars": int(metrics.get("message_chars") or 0),
            "tool_schema_chars": int(metrics.get("tool_schema_chars") or 0), "tool_count": int(metrics.get("tool_count") or 0),
            "retired": int(metrics.get("context_retired") or 0), "saved_chars": int(metrics.get("context_saved_chars") or 0),
            "tools": json.dumps([{"id": getattr(c, "id", ""), "name": getattr(c, "name", ""), "chars": None}
                                 for c in tool_calls], ensure_ascii=False)}
        self._write(turn)

    def settle(self, messages: list) -> None:
        """The tool answers of earlier turns are in ``messages`` now: record how large each one is."""
        sizes = {m.get("tool_call_id"): len(str(m.get("content") or "")) for m in messages if m.get("role") == "tool"}
        for turn, row in self._rows.items():
            tools = json.loads(row["tools"])
            changed = False
            for tool in tools:
                if tool["chars"] is None and tool["id"] in sizes:
                    tool["chars"], changed = sizes[tool["id"]], True
            if changed:
                row["tools"] = json.dumps(tools, ensure_ascii=False)
                self._write(turn)

    def _write(self, turn: int) -> None:
        row = self._rows[turn]
        try:
            with store.connect(write=True) as conn:
                if conn is None:
                    return
                names = list(row)
                conn.execute(f"INSERT OR REPLACE INTO assistant_rounds ({','.join(names)})"
                             f" VALUES ({','.join('?' * len(names))})", [row[n] for n in names])
        except Exception as exc:  # noqa: BLE001 - recording must never break the assistant
            logger.debug("assistant round skipped: %s", exc)


def _now() -> float:
    return time.time()


def _tools(row) -> list[dict]:
    try:
        return json.loads(row["tools"])
    except (TypeError, ValueError):
        return []


def _turn_bills(conn, config_dir: str, where: str, params: list) -> dict:
    """The model calls of assistant requests, priced and added up per (request, turn)."""
    from core.telemetry import pricing
    rows = query._priced(conn, ["request_id", "turn"], where + " AND request_id != ''", params,  # noqa: SLF001
                         pricing.load(config_dir))
    grouped: dict = {}
    for row in rows:
        if row["kind"] == "llm":
            grouped.setdefault((row["request_id"], row["turn"]), []).append(row)
    return {key: query._fold(parts, {"ts": max(p["ts"] or 0 for p in parts)})  # noqa: SLF001
            for key, parts in grouped.items()}


ORIGINS = ("app", "script", "")


def report(config_dir: str, since: Optional[float] = None, until: Optional[float] = None,
           limit: int = 25, offset: int = 0, origin: Optional[str] = None) -> dict:
    """Totals, the requests that cost the most recently, the tools whose answers are largest, and context growth by turn.

    ``origin`` keeps only requests sent from the app's page ("app"), from another program ("script"), or recorded
    before the origin was kept (""); ``by_origin`` always counts all three."""
    with store.connect() as conn:
        if conn is None:
            return {"enabled": False}
        where, params = query._where(since, until, {})  # noqa: SLF001
        bills = _turn_bills(conn, config_dir, where, params)
        rounds = [dict(r) for r in conn.execute(
            "SELECT * FROM assistant_rounds WHERE ts >= ? AND ts < ?",
            (since if since is not None else 0, until if until is not None else 1e18))]
        spans = {r["request_id"]: dict(r) for r in conn.execute(
            "SELECT request_id, SUM(duration_s) AS seconds, MAX(status) AS status FROM spans"
            " WHERE stage = 'assistant' AND request_id != '' GROUP BY request_id")}
        earlier = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(prompt_tokens), 0), COALESCE(SUM(completion_tokens), 0) FROM calls"
            f" WHERE kind = 'llm' AND stage = 'assistant' AND request_id = '' AND {where}", params).fetchone()
    by_request: dict = {}
    for (request_id, _turn), bill in bills.items():
        by_request.setdefault(request_id, []).append(bill)
    layout: dict = {}
    for row in rounds:
        layout.setdefault(row["request_id"], []).append(row)
    requests = []
    for request_id, parts in by_request.items():
        turns = layout.get(request_id, [])
        requests.append({
            "request_id": request_id, "conversation_id": next((t["conversation_id"] for t in turns), ""),
            "user": next((t["user"] for t in turns), ""), "origin": next((t.get("origin") or "" for t in turns), ""),
            "ts": max(p["ts"] for p in parts), "turns": len(parts),
            "tools": [t["name"] for turn in turns for t in _tools(turn)],
            "prompt_tokens": sum(p["prompt_tokens"] for p in parts), "completion_tokens": sum(p["completion_tokens"] for p in parts),
            "cache_read_tokens": sum(p["cache_read_tokens"] for p in parts), "cost_usd": round(sum(p["cost_usd"] for p in parts), 6),
            "partial": any(p["partial"] for p in parts), "failed": sum(p["failed"] for p in parts),
            "seconds": round((spans.get(request_id) or {}).get("seconds") or sum(p["seconds"] for p in parts), 1),
            "peak_prompt_tokens": max(p["prompt_tokens"] for p in parts)})
    requests.sort(key=lambda r: -r["ts"])
    by_origin = {key: {"requests": 0, "turns": 0, "prompt_tokens": 0, "cost_usd": 0.0} for key in ORIGINS}
    for request in requests:
        group = by_origin[request["origin"] if request["origin"] in by_origin else ""]
        group["requests"] += 1
        group["turns"] += request["turns"]
        group["prompt_tokens"] += request["prompt_tokens"]
        group["cost_usd"] = round(group["cost_usd"] + request["cost_usd"], 6)
    if origin is not None:
        requests = [r for r in requests if r["origin"] == origin]
        kept = {r["request_id"] for r in requests}
        rounds = [r for r in rounds if r["request_id"] in kept]
        bills = {key: bill for key, bill in bills.items() if key[0] in kept}
    totals = {**_totals(requests, rounds, earlier), "by_origin": by_origin}
    return {"enabled": True, "totals": totals, "tools": _tool_sizes(rounds),
            "turns": _by_turn(bills, rounds), "requests": requests[offset:offset + limit], "request_total": len(requests)}


def _totals(requests: list[dict], rounds: list[dict], earlier) -> dict:
    prompt = sum(r["prompt_tokens"] for r in requests)
    cached = sum(r["cache_read_tokens"] for r in requests)
    schema = sum(r["tool_schema_chars"] for r in rounds)
    messages = sum(r["message_chars"] for r in rounds)
    n = len(requests)
    return {"requests": n, "turns": sum(r["turns"] for r in requests),
            "turns_per_request": round(sum(r["turns"] for r in requests) / n, 2) if n else None,
            "prompt_tokens": prompt, "completion_tokens": sum(r["completion_tokens"] for r in requests),
            "cache_read_tokens": cached, "cache_rate": round(cached / prompt, 4) if prompt else None,
            "cost_usd": round(sum(r["cost_usd"] for r in requests), 6), "partial": any(r["partial"] for r in requests),
            "tool_schema_share": round(schema / messages, 4) if messages else None,
            "tool_calls": sum(len(r["tools"]) for r in requests), "failed": sum(r["failed"] for r in requests),
            "earlier_calls": earlier[0], "earlier_prompt_tokens": earlier[1], "earlier_completion_tokens": earlier[2]}


def _tool_sizes(rounds: list[dict]) -> list[dict]:
    """Each tool the model called: how often, and how large its answers were (they go back into every later turn)."""
    seen: dict = {}
    for row in rounds:
        for tool in _tools(row):
            slot = seen.setdefault(tool["name"], {"name": tool["name"], "calls": 0, "sized": 0, "chars": 0, "max_chars": 0, "requests": set()})
            slot["calls"] += 1
            slot["requests"].add(row["request_id"])
            if tool["chars"] is not None:
                slot["sized"] += 1
                slot["chars"] += tool["chars"]
                slot["max_chars"] = max(slot["max_chars"], tool["chars"])
    out = [{"name": s["name"], "calls": s["calls"], "requests": len(s["requests"]),
            "avg_chars": round(s["chars"] / s["sized"]) if s["sized"] else None, "max_chars": s["max_chars"],
            "total_chars": s["chars"]} for s in seen.values()]
    out.sort(key=lambda t: (-t["total_chars"], -t["calls"], t["name"]))
    return out


def _by_turn(bills: dict, rounds: list[dict]) -> list[dict]:
    """How the prompt grows from the first turn of a request to the later ones."""
    shape: dict = {}
    for row in rounds:
        slot = shape.setdefault(row["turn"], {"turn": row["turn"], "requests": 0, "prompt": 0, "completion": 0, "billed": 0,
                                               "message_chars": 0, "tool_schema_chars": 0, "tool_count": 0, "retired": 0})
        slot["requests"] += 1
        slot["message_chars"] += row["message_chars"]
        slot["tool_schema_chars"] += row["tool_schema_chars"]
        slot["tool_count"] += row["tool_count"]
        slot["retired"] += row["retired"]
        bill = bills.get((row["request_id"], str(row["turn"])))
        if bill:
            slot["billed"] += 1
            slot["prompt"] += bill["prompt_tokens"]
            slot["completion"] += bill["completion_tokens"]
    return [{"turn": s["turn"], "requests": s["requests"],
             "prompt_tokens_avg": round(s["prompt"] / s["billed"]) if s["billed"] else None,
             "completion_tokens_avg": round(s["completion"] / s["billed"]) if s["billed"] else None,
             "message_chars_avg": round(s["message_chars"] / s["requests"]),
             "tool_schema_chars_avg": round(s["tool_schema_chars"] / s["requests"]),
             "tool_count_avg": round(s["tool_count"] / s["requests"], 1), "retired": s["retired"]}
            for _, s in sorted(shape.items())]


def request_detail(config_dir: str, request_id: str) -> dict:
    """One request turn by turn: tokens, cost, the size of what was sent, and each tool asked for with its answer's size."""
    with store.connect() as conn:
        if conn is None:
            return {"enabled": False, "request_id": request_id, "turns": []}
        bills = _turn_bills(conn, config_dir, "request_id = ?", [request_id])
        rounds = {r["turn"]: dict(r) for r in conn.execute(
            "SELECT * FROM assistant_rounds WHERE request_id = ? ORDER BY turn", (request_id,))}
    turns = []
    for turn in sorted(set(rounds) | {int(t) for (_r, t) in bills if str(t).isdigit()}):
        row, bill = rounds.get(turn), bills.get((request_id, str(turn)))
        turns.append({
            "turn": turn, "message_chars": row["message_chars"] if row else None,
            "tool_schema_chars": row["tool_schema_chars"] if row else None, "tool_count": row["tool_count"] if row else None,
            "retired": row["retired"] if row else None, "saved_chars": row["saved_chars"] if row else None,
            "tools": [{"name": t["name"], "chars": t["chars"]} for t in _tools(row)] if row else [],
            "prompt_tokens": bill["prompt_tokens"] if bill else None, "completion_tokens": bill["completion_tokens"] if bill else None,
            "cache_read_tokens": bill["cache_read_tokens"] if bill else None, "cost_usd": bill["cost_usd"] if bill else None,
            "partial": bill["partial"] if bill else False, "seconds": bill["seconds"] if bill else None,
            "failed": bill["failed"] if bill else 0})
    return {"enabled": True, "request_id": request_id, "turns": turns}

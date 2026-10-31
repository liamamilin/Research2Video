"""Read side: sums over ``calls`` and ``spans``, priced on the way out. Read only; never calls a provider."""
from __future__ import annotations

import json
from typing import Optional

from core.telemetry import pricing, store

FILTERS = ("matrix", "column_key", "script_id", "form", "recipe")
_GROUP_SQL = (
    "SELECT {keys}, kind, provider, model, COALESCE(snapshot_in_per_m, -1) AS si, COALESCE(snapshot_out_per_m, -1) AS so,"
    " (reported_cost_usd IS NOT NULL) AS rep, COUNT(*) AS n, SUM(requests) AS requests,"
    " SUM(prompt_tokens) AS prompt, SUM(completion_tokens) AS completion, SUM(reasoning_tokens) AS reasoning,"
    " SUM(cache_read_tokens) AS cached, SUM(reported_cost_usd) AS reported, SUM(error_code != '') AS failed,"
    " SUM(estimated) AS estimated, SUM(COALESCE(duration_s, 0)) AS seconds, MAX(ts) AS ts"
    " FROM calls WHERE {where} GROUP BY {keys}, kind, provider, model, si, so, rep")


def _where(since: Optional[float], until: Optional[float], filters: dict, when: str = "ts") -> tuple[str, list]:
    clauses, params = ["1 = 1"], []
    if since is not None:
        clauses.append(f"{when} >= ?")
        params.append(since)
    if until is not None:
        clauses.append(f"{when} < ?")
        params.append(until)
    for name in FILTERS:
        if filters.get(name):
            clauses.append(f"{name} = ?")
            params.append(filters[name])
    return " AND ".join(clauses), params


def _priced(conn, keys: list[str], where: str, params: list, table: list) -> list[dict]:
    rows = conn.execute(_GROUP_SQL.format(keys=", ".join(keys), where=where), params).fetchall()
    out = []
    for row in rows:
        group = dict(row)
        group.update(snap_in=None if group["si"] < 0 else group["si"], snap_out=None if group["so"] < 0 else group["so"],
                     reported_n=group["n"] if group["rep"] else 0)
        group["cost"], group["basis"] = pricing.cost_of(group, table)
        out.append(group)
    return out


def _fold(rows: list[dict], key) -> dict:
    """Add priced groups together; a total is partial when any part of it could not be priced."""
    total = {"calls": 0, "requests": 0, "failed": 0, "prompt_tokens": 0, "completion_tokens": 0,
             "reasoning_tokens": 0, "cache_read_tokens": 0, "seconds": 0.0, "cost_usd": 0.0, "unpriced_calls": 0,
             "estimated_calls": 0, "bases": set()}
    for row in rows:
        total["calls"] += row["n"]
        total["requests"] += row["requests"] or 0
        total["failed"] += row["failed"] or 0
        total["prompt_tokens"] += row["prompt"] or 0
        total["completion_tokens"] += row["completion"] or 0
        total["reasoning_tokens"] += row["reasoning"] or 0
        total["cache_read_tokens"] += row["cached"] or 0
        total["seconds"] += row["seconds"] or 0
        total["estimated_calls"] += row["estimated"] or 0
        if row["kind"] == "llm":
            total["bases"].add(row["basis"])
            if row["cost"] is None:
                total["unpriced_calls"] += row["n"]
            else:
                total["cost_usd"] += row["cost"]
    total["bases"] = sorted(total["bases"])
    total["seconds"] = round(total["seconds"], 1)
    total["cost_usd"] = round(total["cost_usd"], 6)
    total["total_tokens"] = total["prompt_tokens"] + total["completion_tokens"]
    total["partial"] = total["unpriced_calls"] > 0
    return {**total, **key}


def summary(config_dir: str, since: Optional[float] = None, until: Optional[float] = None,
            filters: Optional[dict] = None) -> dict:
    table = pricing.load(config_dir)
    where, params = _where(since, until, filters or {})
    span_where, span_params = _where(since, until, filters or {}, "COALESCE(end, start)")
    with store.connect() as conn:
        if conn is None:
            return {"enabled": False}
        by_model = _priced(conn, ["provider", "model"], where, params, table)
        by_stage = _priced(conn, ["stage"], where, params, table)
        spans = {r["stage"]: r for r in conn.execute(
            "SELECT stage, COUNT(*) AS n, SUM(duration_s) AS seconds, SUM(status != 'ok') AS failed FROM spans"
            f" WHERE {span_where} GROUP BY stage", span_params).fetchall()}
    models: dict = {}
    for row in by_model:
        models.setdefault((row["kind"], row["provider"], row["model"]), []).append(row)
    stages: dict = {}
    for row in by_stage:
        stages.setdefault(row["stage"], []).append(row)
    return {
        "enabled": True,
        "totals": _fold([r for r in by_model], {}),
        "llm": _fold([r for r in by_model if r["kind"] == "llm"], {}),
        "search": _fold([r for r in by_model if r["kind"] == "search"], {}),
        "models": sorted((_fold(rows, {"kind": k[0], "provider": k[1], "model": k[2]}) for k, rows in models.items()),
                         key=lambda m: -(m["total_tokens"] or 0)),
        "stages": sorted((_fold(rows, {"stage": s, "span_count": spans[s]["n"] if s in spans else 0,
                                       "span_seconds": round(spans[s]["seconds"] or 0, 1) if s in spans else 0.0})
                          for s, rows in stages.items()), key=lambda s: -s["total_tokens"]),
        "unpriced_models": sorted({f"{r['provider']} {r['model']}".strip() or "(未记录模型)" for r in by_model
                                   if r["kind"] == "llm" and r["basis"] == "unpriced"}),
    }


def _topic_title(script: Optional[dict]) -> str:
    topic = (script or {}).get("topic")
    if isinstance(topic, str):
        try:
            topic = json.loads(topic)
        except ValueError:
            return topic
    return str((topic or {}).get("title") or (topic or {}).get("topic") or "") if isinstance(topic, dict) else ""


def scripts(config_dir: str, since: Optional[float] = None, until: Optional[float] = None,
            filters: Optional[dict] = None, limit: int = 50, offset: int = 0) -> dict:
    """One bill per script, newest activity first."""
    table = pricing.load(config_dir)
    where, params = _where(since, until, filters or {})
    where += " AND script_id != ''"
    with store.connect() as conn:
        if conn is None:
            return {"enabled": False, "items": [], "total": 0}
        rows = _priced(conn, ["script_id", "column_key", "matrix", "form"], where, params, table)
        spans = {r["script_id"]: r["seconds"] for r in conn.execute(
            "SELECT script_id, SUM(duration_s) AS seconds FROM spans WHERE script_id != '' AND stage != 'render'"
            " GROUP BY script_id").fetchall()}
        totals = {r["script_id"]: r["seconds"] for r in conn.execute(
            "SELECT script_id, SUM(duration_s) AS seconds FROM spans WHERE stage = 'render' GROUP BY script_id").fetchall()}
    grouped: dict = {}
    for row in rows:
        grouped.setdefault(row["script_id"], []).append(row)
    items = []
    for script_id, parts in grouped.items():
        first = parts[0]
        items.append(_fold(parts, {
            "script_id": script_id, "column_key": first["column_key"], "matrix": first["matrix"],
            "form": next((p["form"] for p in parts if p["form"]), ""), "last_ts": max(p["ts"] or 0 for p in parts),
            "render_seconds": round(totals.get(script_id) or 0, 1), "stage_seconds": round(spans.get(script_id) or 0, 1)}))
    items.sort(key=lambda i: -i["last_ts"])
    page = items[offset:offset + limit]
    from core.video import store as video_store
    for item in page:
        try:
            item["title"] = _topic_title(video_store.get_script(item["script_id"]))
        except Exception:  # noqa: BLE001 - a missing script still has a bill
            item["title"] = ""
    return {"enabled": True, "items": page, "total": len(items)}


def script_detail(config_dir: str, script_id: str) -> dict:
    """Stages (time and tokens side by side) and models for one script."""
    table = pricing.load(config_dir)
    where, params = "script_id = ?", [script_id]
    with store.connect() as conn:
        if conn is None:
            return {"enabled": False}
        by_stage = _priced(conn, ["stage"], where, params, table)
        by_model = _priced(conn, ["provider", "model"], where, params, table)
        spans = conn.execute("SELECT stage, render_id, duration_s, status, start FROM spans WHERE script_id = ?"
                             " ORDER BY COALESCE(start, 0)", (script_id,)).fetchall()
    stages: dict = {}
    for row in by_stage:
        stages.setdefault(row["stage"], []).append(row)
    for span in spans:
        stages.setdefault(span["stage"], [])
    rows = []
    for stage, parts in stages.items():
        mine = [s for s in spans if s["stage"] == stage]
        rows.append(_fold(parts, {"stage": stage, "span_count": len(mine),
                                  "span_seconds": round(sum(s["duration_s"] or 0 for s in mine), 1),
                                  "failed_spans": sum(1 for s in mine if s["status"] != "ok")}))
    models: dict = {}
    for row in by_model:
        models.setdefault((row["kind"], row["provider"], row["model"]), []).append(row)
    return {"enabled": True, "script_id": script_id,
            "totals": _fold(by_model, {}),
            "stages": sorted(rows, key=lambda r: r["stage"]),
            "models": [_fold(v, {"kind": k[0], "provider": k[1], "model": k[2]}) for k, v in models.items()]}

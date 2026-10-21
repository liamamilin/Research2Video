"""The unattended pipeline's ledger: plans (weekly), runs (one batch) and items (one video each).

An item records only what is needed to find its place again after a restart — the script id, the
render id, how many times each stage was retried. Where the work actually is comes from the script
and render rows, so a restart resumes from the last finished stage instead of starting over.

An item also records the production config it was written for ("" for the weekly plan's), so its script says which
config made it. Item states: PENDING (no script yet), WORKING, HELD (needs the user: the gate said no or a retry
ran out), DONE (render completed), FAILED, CANCELLED.
Run states: RUNNING, DONE, CANCELLED.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Optional

from core.video.store import connect, _now

SCHEMA = """
CREATE TABLE IF NOT EXISTS autopilot_plans (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    expression TEXT NOT NULL,
    spec TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    next_fire REAL,
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS autopilot_runs (
    id TEXT PRIMARY KEY,
    plan_id TEXT,
    spec TEXT NOT NULL,
    status TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT 'TOPICS',
    note TEXT NOT NULL DEFAULT '',
    started_by TEXT NOT NULL DEFAULT '',
    started_at REAL NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS autopilot_items (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    slot INTEGER NOT NULL,
    column_key TEXT NOT NULL,
    topic TEXT NOT NULL,
    script_id TEXT,
    render_id TEXT,
    state TEXT NOT NULL DEFAULT 'PENDING',
    note TEXT NOT NULL DEFAULT '',
    gate TEXT NOT NULL DEFAULT '{}',
    retries TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_autopilot_items_run ON autopilot_items(run_id);
CREATE TABLE IF NOT EXISTS alert_acks (
    key TEXT PRIMARY KEY,
    acked_by TEXT NOT NULL DEFAULT '',
    acked_at TEXT NOT NULL
);
"""

_ITEM_JSON = ("topic", "gate", "retries")
_PLAN_JSON = ("spec",)
_RUN_JSON = ("spec",)


def init(conn) -> None:
    conn.executescript(SCHEMA)
    columns = {r[1] for r in conn.execute("PRAGMA table_info(autopilot_runs)")}
    if "discovery_at" not in columns:
        conn.execute("ALTER TABLE autopilot_runs ADD COLUMN discovery_at REAL")
    if "discovery_tries" not in columns:
        conn.execute("ALTER TABLE autopilot_runs ADD COLUMN discovery_tries INTEGER NOT NULL DEFAULT 0")
    if "production" not in {r[1] for r in conn.execute("PRAGMA table_info(autopilot_items)")}:
        conn.execute("ALTER TABLE autopilot_items ADD COLUMN production TEXT NOT NULL DEFAULT ''")


def _dec(row, fields) -> dict | None:
    if row is None:
        return None
    out = dict(row)
    for f in fields:
        out[f] = json.loads(out[f]) if out.get(f) else {}
    return out


def _update(table: str, row_id: str, json_fields: tuple, **fields: Any) -> None:
    if not fields:
        return
    fields = {k: (json.dumps(v, ensure_ascii=False) if k in json_fields else v) for k, v in fields.items()}
    if table != "autopilot_plans":
        fields["updated_at"] = _now()
    sets = ", ".join(f"{k} = ?" for k in fields)
    with connect(write=True) as conn:
        conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", (*fields.values(), row_id))


# --- plans -----------------------------------------------------------------

def save_plan(name: str, expression: str, spec: dict, *, next_fire: Optional[float], created_by: str = "",
              plan_id: Optional[str] = None) -> dict:
    plan_id = plan_id or uuid.uuid4().hex
    with connect(write=True) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO autopilot_plans (id, name, expression, spec, enabled, next_fire, created_by,"
            " created_at) VALUES (?, ?, ?, ?, 1, ?, ?, ?)",
            (plan_id, name, expression, json.dumps(spec, ensure_ascii=False), next_fire, created_by, _now()))
    return get_plan(plan_id)


def get_plan(plan_id: str) -> dict | None:
    with connect() as conn:
        return _dec(conn.execute("SELECT * FROM autopilot_plans WHERE id = ?", (plan_id,)).fetchone(), _PLAN_JSON)


def list_plans() -> list[dict]:
    with connect() as conn:
        return [_dec(r, _PLAN_JSON) for r in conn.execute("SELECT * FROM autopilot_plans ORDER BY created_at")]


def update_plan(plan_id: str, **fields: Any) -> None:
    _update("autopilot_plans", plan_id, _PLAN_JSON, **fields)


def delete_plan(plan_id: str) -> bool:
    with connect(write=True) as conn:
        return conn.execute("DELETE FROM autopilot_plans WHERE id = ?", (plan_id,)).rowcount > 0


def claim_plan(plan_id: str, expected: float, new_next: Optional[float]) -> bool:
    """Move ``next_fire`` forward only if nobody else did: one winner per slot, across processes."""
    with connect(write=True) as conn:
        return conn.execute(
            "UPDATE autopilot_plans SET next_fire = ? WHERE id = ? AND enabled = 1 AND next_fire = ?",
            (new_next, plan_id, expected)).rowcount == 1


# --- runs and items --------------------------------------------------------

def create_run(spec: dict, items: list[dict], *, plan_id: Optional[str] = None, started_by: str = "") -> dict:
    run_id, now = uuid.uuid4().hex, _now()
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO autopilot_runs (id, plan_id, spec, status, stage, started_by, started_at, created_at,"
            " updated_at) VALUES (?, ?, ?, 'RUNNING', 'TOPICS', ?, ?, ?, ?)",
            (run_id, plan_id, json.dumps(spec, ensure_ascii=False), started_by, time.time(), now, now))
        for i, item in enumerate(items):
            conn.execute(
                "INSERT INTO autopilot_items (id, run_id, slot, column_key, topic, production, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, run_id, i, item["column_key"], json.dumps(item["topic"], ensure_ascii=False),
                 item.get("production", ""), now))
    return get_run(run_id)


def get_run(run_id: str) -> dict | None:
    with connect() as conn:
        run = _dec(conn.execute("SELECT * FROM autopilot_runs WHERE id = ?", (run_id,)).fetchone(), _RUN_JSON)
        if run:
            run["items"] = [_dec(r, _ITEM_JSON) for r in conn.execute(
                "SELECT * FROM autopilot_items WHERE run_id = ? ORDER BY slot", (run_id,))]
    return run


def list_runs(limit: int = 20, status: Optional[str] = None) -> list[dict]:
    q, args = "SELECT id FROM autopilot_runs", ()
    if status:
        q, args = q + " WHERE status = ?", (status,)
    with connect() as conn:
        ids = [r["id"] for r in conn.execute(q + " ORDER BY started_at DESC LIMIT ?", (*args, limit))]
    return [get_run(i) for i in ids]


def update_run(run_id: str, **fields: Any) -> None:
    _update("autopilot_runs", run_id, _RUN_JSON, **fields)


def update_item(item_id: str, **fields: Any) -> None:
    _update("autopilot_items", item_id, _ITEM_JSON, **fields)


def finished_run(spec: dict, note: str, *, plan_id: Optional[str] = None, started_by: str = "") -> dict:
    """A batch that did nothing, recorded so it shows (a plan slot that was skipped, say)."""
    run = create_run(spec, [], plan_id=plan_id, started_by=started_by)
    update_run(run["id"], status="DONE", note=note)
    return get_run(run["id"])


def ack_alert(key: str, by: str = "") -> None:
    """The user has seen this automation alert: it leaves the board."""
    with connect(write=True) as conn:
        conn.execute("INSERT OR REPLACE INTO alert_acks (key, acked_by, acked_at) VALUES (?, ?, ?)", (key, by, _now()))


def acked_alerts() -> set[str]:
    with connect() as conn:
        return {r["key"] for r in conn.execute("SELECT key FROM alert_acks")}


def used_topic_ids() -> set[str]:
    """Topics already given to a pending or running item, so two batches never pick the same one."""
    with connect() as conn:
        rows = conn.execute("SELECT topic FROM autopilot_items WHERE state NOT IN ('FAILED', 'CANCELLED')").fetchall()
    return {json.loads(r["topic"]).get("id") for r in rows} - {None}


def held_items(limit: int = 50) -> list[dict]:
    """Items waiting for the user, newest first (the user may have approved their script since)."""
    with connect() as conn:
        rows = conn.execute("SELECT * FROM autopilot_items WHERE state = 'HELD' ORDER BY updated_at DESC LIMIT ?",
                            (limit,)).fetchall()
    return [_dec(r, _ITEM_JSON) for r in rows]

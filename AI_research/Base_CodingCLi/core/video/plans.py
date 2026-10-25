"""Plans waiting for the user: the five-step adaptation paused after its plan (``writing.pause_after_plan``).

A recipe can ask that the adaptation stop once the plan is made, so the user reads it before any draft is written.
The plan is kept here until the user confirms it (as made, or with its words edited), asks for it to be redone with a
note, or discards it. A confirmed plan is written from exactly as stored, so a plan confirmed unchanged gives the
same draft request as the unpaused pipeline. See spec/video-steps.md.

States: PENDING (waiting for the user) → CONFIRMED (the draft is being written from it) → USED (a version was
written from it); or SUPERSEDED (redone) / DISCARDED.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Optional

from core.video import store

SCHEMA = """
CREATE TABLE IF NOT EXISTS script_plans (
    id TEXT PRIMARY KEY,
    script_id TEXT NOT NULL,
    state TEXT NOT NULL,                    -- PENDING | CONFIRMED | USED | SUPERSEDED | DISCARDED
    plan TEXT NOT NULL,                     -- the plan as the pipeline made it (then as edited, once confirmed)
    problems TEXT NOT NULL DEFAULT '[]',    -- the fact sheet's and the plan's problems, carried to the version
    usage TEXT NOT NULL DEFAULT '{}',       -- the model usage of making it, added to the version's
    sheet_id TEXT NOT NULL,
    style TEXT NOT NULL DEFAULT '{}',       -- {"depth", "tone"} the plan was made for
    note TEXT NOT NULL DEFAULT '',          -- the user's note this plan was redone with
    previous_status TEXT NOT NULL,          -- what the script goes back to when the plan is discarded
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    confirmed_by TEXT NOT NULL DEFAULT '',
    confirmed_at TEXT NOT NULL DEFAULT '',
    edits TEXT NOT NULL DEFAULT '[]',       -- the fields the user changed on confirming
    version INTEGER                         -- the version written from it
);
CREATE INDEX IF NOT EXISTS idx_script_plans_script ON script_plans(script_id);
"""

#: Fields of a plan the user may reword on confirming. The structure (beats, facts, budgets) stays the model's:
#: the audits check the draft against it.
EDITABLE_TOP = ("question", "core_message")
EDITABLE_BEAT = ("title", "point")


def init(conn) -> None:
    conn.executescript(SCHEMA)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _row(r) -> dict:
    return {"id": r[0], "script_id": r[1], "state": r[2], "plan": json.loads(r[3]), "problems": json.loads(r[4]),
            "usage": json.loads(r[5]), "sheet_id": r[6], "style": json.loads(r[7]), "note": r[8],
            "previous_status": r[9], "created_by": r[10], "created_at": r[11], "confirmed_by": r[12],
            "confirmed_at": r[13], "edits": json.loads(r[14]), "version": r[15]}


_COLS = ("id, script_id, state, plan, problems, usage, sheet_id, style, note, previous_status, created_by,"
         " created_at, confirmed_by, confirmed_at, edits, version")


def save(script_id: str, *, plan: dict, problems: list[str], usage: dict, sheet_id: str, style: dict, note: str,
         previous_status: str, created_by: str) -> dict:
    """A new PENDING plan; the script's earlier waiting plans are superseded."""
    plan_id = uuid.uuid4().hex
    with store.connect(write=True) as conn:
        conn.execute("UPDATE script_plans SET state = 'SUPERSEDED' WHERE script_id = ? AND state = 'PENDING'",
                     (script_id,))
        conn.execute(
            f"INSERT INTO script_plans ({_COLS}) VALUES (?, ?, 'PENDING', ?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', '[]', NULL)",
            (plan_id, script_id, json.dumps(plan, ensure_ascii=False), json.dumps(problems, ensure_ascii=False),
             json.dumps(usage, ensure_ascii=False), sheet_id, json.dumps(style, ensure_ascii=False), note,
             previous_status, created_by, _now()))
    return get(plan_id)


def get(plan_id: str) -> Optional[dict]:
    with store.connect() as conn:
        r = conn.execute(f"SELECT {_COLS} FROM script_plans WHERE id = ?", (plan_id,)).fetchone()
    return _row(r) if r else None


def open_plan(script_id: str) -> Optional[dict]:
    """The script's plan not yet written from: PENDING (waiting for the user) or CONFIRMED (its draft was cut off)."""
    with store.connect() as conn:
        r = conn.execute(f"SELECT {_COLS} FROM script_plans WHERE script_id = ? AND state IN ('PENDING', 'CONFIRMED')"
                         " ORDER BY created_at DESC, rowid DESC LIMIT 1", (script_id,)).fetchone()
    return _row(r) if r else None


def pending(script_id: str) -> Optional[dict]:
    plan = open_plan(script_id)
    return plan if plan and plan["state"] == "PENDING" else None


def apply_edits(plan: dict, edits: dict) -> list[str]:
    """Change the plan's words in place (keys keep their order). Returns the fields that really changed.

    ``edits``: ``question``, ``core_message``, ``hook_idea``, and ``beats`` — one ``{"title", "point"}`` per beat,
    in order. Raises ValueError on a wrong shape."""
    changed = []
    for key in EDITABLE_TOP:
        if key in edits and edits[key] is not None:
            value = str(edits[key]).strip()
            if not value:
                raise ValueError(f"{key} 不能为空")
            if value != plan.get(key):
                plan[key] = value
                changed.append(key)
    if edits.get("hook_idea") is not None:
        value = str(edits["hook_idea"]).strip()
        if not value:
            raise ValueError("开场钩子不能为空")
        if value != plan["hook"].get("idea"):
            plan["hook"]["idea"] = value
            changed.append("hook.idea")
    beats = edits.get("beats")
    if beats is not None:
        if not isinstance(beats, list) or len(beats) != len(plan["beats"]):
            raise ValueError(f"方案有 {len(plan['beats'])} 节，改的时候节数不能变")
        for i, (beat, edit) in enumerate(zip(plan["beats"], beats), start=1):
            for key in EDITABLE_BEAT:
                if (edit or {}).get(key) is None:
                    continue
                value = str(edit[key]).strip()
                if not value and key == "title":
                    raise ValueError(f"第 {i} 节的标题不能为空")
                if value != beat.get(key):
                    beat[key] = value
                    changed.append(f"beats[{i}].{key}")
    return changed


def confirm(plan_id: str, *, plan: dict, edits: list[str], by: str) -> bool:
    """PENDING → CONFIRMED, once: False when it was already confirmed, redone or discarded (a second click)."""
    with store.connect(write=True) as conn:
        return conn.execute(
            "UPDATE script_plans SET state = 'CONFIRMED', plan = ?, edits = ?, confirmed_by = ?, confirmed_at = ?"
            " WHERE id = ? AND state = 'PENDING'",
            (json.dumps(plan, ensure_ascii=False), json.dumps(edits, ensure_ascii=False), by, _now(), plan_id),
        ).rowcount == 1


def mark(plan_id: str, state: str, *, version: Optional[int] = None, only_from: tuple[str, ...] = ()) -> bool:
    where = " AND state IN (" + ", ".join("?" * len(only_from)) + ")" if only_from else ""
    with store.connect(write=True) as conn:
        return conn.execute(f"UPDATE script_plans SET state = ?, version = COALESCE(?, version) WHERE id = ?{where}",
                            (state, version, plan_id, *only_from)).rowcount == 1


def history(script_id: str) -> list[dict]:
    with store.connect() as conn:
        rows = conn.execute(f"SELECT {_COLS} FROM script_plans WHERE script_id = ? ORDER BY created_at, rowid",
                            (script_id,)).fetchall()
    return [_row(r) for r in rows]

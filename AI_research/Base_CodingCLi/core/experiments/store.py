"""``state/experiments.db``: the module's own database (spec §11). Nothing else writes it, and it writes nothing else.

Writers take the lock up front (``BEGIN IMMEDIATE``) and wait at most two seconds for it; a caller on the script
creation path treats any error, including a busy database, as arm A (§2 rule 4).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Iterator

from core import sqlite_util
from core.experiments.definition import Experiment

BUSY_SECONDS = 2.0
MASTER = "enabled"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
    id TEXT PRIMARY KEY, definition TEXT NOT NULL, status TEXT NOT NULL,
    created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS assignments (
    token TEXT PRIMARY KEY, experiment_id TEXT NOT NULL, arm TEXT NOT NULL, column_key TEXT NOT NULL,
    block INTEGER NOT NULL, assigned_at REAL NOT NULL, code_version TEXT NOT NULL, script_id TEXT
);
CREATE INDEX IF NOT EXISTS assignments_experiment ON assignments(experiment_id, column_key, block);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS applied (
    script_id TEXT NOT NULL, experiment_id TEXT NOT NULL, switch TEXT NOT NULL, applied INTEGER,
    reason TEXT NOT NULL DEFAULT '', at REAL NOT NULL, PRIMARY KEY (script_id, experiment_id, switch)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, experiment_id TEXT NOT NULL, kind TEXT NOT NULL,
    at REAL NOT NULL, by TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS results (experiment_id TEXT PRIMARY KEY, computed_at REAL NOT NULL, data TEXT NOT NULL);
"""


class ExperimentStore:
    def __init__(self, state_dir: str | Path):
        self.path = str(Path(state_dir) / "experiments.db")
        self._ready = False

    def connect(self, write: bool = False) -> Iterator:
        if not self._ready:
            with sqlite_util.connect(self.path, timeout=BUSY_SECONDS) as db:
                db.executescript(_SCHEMA)
            self._ready = True
        return sqlite_util.connect(self.path, timeout=BUSY_SECONDS, write=write)

    # ---- master switch: off unless explicitly on (§2, §11) ------------------------------------------------------

    def enabled(self) -> bool:
        try:
            with self.connect() as db:
                row = db.execute("SELECT value FROM settings WHERE key=?", (MASTER,)).fetchone()
        except Exception:  # noqa: BLE001 - an unreadable switch means off
            return False
        return bool(row) and row[0] == "1"

    def set_enabled(self, on: bool, by: str) -> None:
        with self.connect(write=True) as db:
            db.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (MASTER, "1" if on else "0"))
            self._event(db, "", "master_on" if on else "master_off", by, {})

    # ---- experiments ---------------------------------------------------------------------------------------------

    def save(self, experiment: Experiment, by: str) -> None:
        """Save a draft. A started experiment is locked (§7): copy it into a new one instead."""
        now = time.time()
        with self.connect(write=True) as db:
            row = db.execute("SELECT status FROM experiments WHERE id=?", (experiment.id,)).fetchone()
            if row and row[0] != "draft":
                raise ValueError("实验开始后不能修改，请复制成新实验")
            db.execute("INSERT INTO experiments VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                       "definition=excluded.definition, updated_at=excluded.updated_at",
                       (experiment.id, experiment.model_dump_json(), "draft", now, now))
            self._event(db, experiment.id, "saved" if row else "created", by, {})

    def get(self, experiment_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM experiments WHERE id=?", (experiment_id,)).fetchone()
        return self._row(row) if row else None

    def list(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM experiments ORDER BY created_at DESC").fetchall()
        return [self._row(r) for r in rows]

    def events(self, experiment_id: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM events WHERE experiment_id=? ORDER BY id", (experiment_id,)).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"] or "{}")} for r in rows]

    def assignments(self, experiment_id: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM assignments WHERE experiment_id=? ORDER BY assigned_at",
                              (experiment_id,)).fetchall()
        return [dict(r) for r in rows]

    def bind_scripts(self, tokens: dict[str, str]) -> int:
        """Fill ``script_id`` for assignments whose token was found on a script (the reconciliation, §7)."""
        if not tokens:
            return 0
        with self.connect(write=True) as db:
            changed = 0
            for token, script_id in tokens.items():
                changed += db.execute("UPDATE assignments SET script_id=? WHERE token=? AND script_id IS NULL",
                                      (script_id, token)).rowcount
        return changed

    def unbound_tokens(self, experiment_id: str) -> list[str]:
        with self.connect() as db:
            rows = db.execute("SELECT token FROM assignments WHERE experiment_id=? AND script_id IS NULL",
                              (experiment_id,)).fetchall()
        return [r[0] for r in rows]

    def running(self) -> list[dict]:
        return [e for e in self.list() if e["status"] == "running"]

    # ---- helpers -------------------------------------------------------------------------------------------------

    @staticmethod
    def _event(db, experiment_id: str, kind: str, by: str, detail: dict) -> None:
        db.execute("INSERT INTO events (experiment_id, kind, at, by, detail) VALUES (?,?,?,?,?)",
                   (experiment_id, kind, time.time(), by, json.dumps(detail, ensure_ascii=False)))

    @staticmethod
    def _row(row) -> dict:
        return {"id": row["id"], "status": row["status"], "created_at": row["created_at"],
                "updated_at": row["updated_at"], "definition": json.loads(row["definition"])}

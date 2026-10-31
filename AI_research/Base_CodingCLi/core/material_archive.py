"""Source observations shared across columns; publication and observation dates stay separate."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from core.search import SearchResult


class MaterialArchive:
    def __init__(self, state_dir: str | Path):
        root = Path(state_dir)
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "materials.db"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS observations (
                    id INTEGER PRIMARY KEY, spec TEXT NOT NULL, item_key TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, first_seen REAL NOT NULL, last_seen REAL NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS material_source ON observations(spec,item_key,first_seen);
                CREATE TABLE IF NOT EXISTS fetches (
                    id INTEGER PRIMARY KEY, spec TEXT NOT NULL, observed_at REAL NOT NULL,
                    count INTEGER NOT NULL, error TEXT NOT NULL, coverage TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS material_fetch ON fetches(spec,observed_at);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def record(
        self, spec: str, items: list, *, error: str = "", now: float | None = None
    ) -> None:
        from core.source_supply import fingerprint, section_key

        now = time.time() if now is None else now
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for item in items:
                key, digest = section_key(item), fingerprint(item)
                prior = db.execute(
                    "SELECT id,fingerprint FROM observations WHERE spec=? AND item_key=? "
                    "ORDER BY first_seen DESC,id DESC LIMIT 1",
                    (spec, key),
                ).fetchone()
                if prior and prior["fingerprint"] == digest:
                    db.execute(
                        "UPDATE observations SET last_seen=? WHERE id=?",
                        (now, prior["id"]),
                    )
                else:
                    db.execute(
                        "INSERT INTO observations(spec,item_key,fingerprint,first_seen,last_seen,payload) "
                        "VALUES (?,?,?,?,?,?)",
                        (
                            spec,
                            key,
                            digest,
                            now,
                            now,
                            json.dumps(asdict(item), ensure_ascii=False),
                        ),
                    )
            db.execute(
                "INSERT INTO fetches(spec,observed_at,count,error,coverage) VALUES (?,?,?,?,?)",
                (
                    spec,
                    now,
                    len(items),
                    error[:1000],
                    json.dumps(getattr(items, "coverage", {}), ensure_ascii=False),
                ),
            )

    def read(self, spec: str, *, before: float | None = None) -> tuple[list, dict]:
        before = time.time() if before is None else before
        with self.connect() as db:
            rows = db.execute(
                """SELECT payload FROM (
                SELECT payload,ROW_NUMBER() OVER (PARTITION BY item_key ORDER BY first_seen DESC,id DESC) AS rank
                FROM observations WHERE spec=? AND first_seen<=?) WHERE rank=1""",
                (spec, before),
            ).fetchall()
            first = db.execute(
                "SELECT MIN(observed_at),MAX(observed_at) FROM fetches WHERE spec=? AND observed_at<=?",
                (spec, before),
            ).fetchone()
        return [SearchResult(**json.loads(r["payload"])) for r in rows], {
            "first_observation": first[0],
            "last_observation": first[1],
            "scope": "已观察条目；来源历史列表及首次归档前的缺口无法保证回溯",
        }

    def prune(self, retention_days: int, *, now: float | None = None) -> None:
        """Prune only this cache; reports, attempts and video artifacts are untouched."""
        cutoff = (time.time() if now is None else now) - retention_days * 86400
        with self.connect() as db:
            db.execute("DELETE FROM observations WHERE last_seen<?", (cutoff,))
            db.execute("DELETE FROM fetches WHERE observed_at<?", (cutoff,))


def merge(archived: list, fresh: list) -> list:
    from core.source_supply import section_key

    return list({section_key(item): item for item in [*archived, *fresh]}.values())

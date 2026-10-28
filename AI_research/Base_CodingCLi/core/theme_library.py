"""Persistent reference examples and reusable topic formats, scoped to a matrix."""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import yaml

from . import sqlite_util


def reference_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("参考链接需要使用 http 或 https")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def identity(*parts: str) -> str:
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:24]


class ThemeLibrary:
    def __init__(self, state_dir: str):
        self.path = str(Path(state_dir) / "theme_library.db")
        with sqlite_util.connect(self.path) as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS examples (
                    id TEXT PRIMARY KEY, matrix TEXT NOT NULL, category TEXT NOT NULL,
                    title TEXT NOT NULL, url TEXT NOT NULL, summary TEXT NOT NULL,
                    created_at REAL NOT NULL, seen_at REAL NOT NULL,
                    UNIQUE(matrix,category,url)
                );
                CREATE TABLE IF NOT EXISTS patterns (
                    id TEXT PRIMARY KEY, matrix TEXT NOT NULL, category TEXT NOT NULL,
                    name TEXT NOT NULL, title_pattern TEXT NOT NULL, outline TEXT NOT NULL,
                    example_ids TEXT NOT NULL, created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS idea_runs (
                    id TEXT PRIMARY KEY, matrix TEXT NOT NULL, status TEXT NOT NULL,
                    brief TEXT NOT NULL, category TEXT NOT NULL, pattern_id TEXT NOT NULL,
                    created_at REAL NOT NULL, finished_at REAL, result TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS examples_matrix ON examples(matrix,category,seen_at);
                CREATE INDEX IF NOT EXISTS idea_runs_matrix ON idea_runs(matrix,created_at);
                CREATE TABLE IF NOT EXISTS seed_versions (matrix TEXT PRIMARY KEY, digest TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS daily_requests (
                    matrix TEXT NOT NULL, day TEXT NOT NULL, request_id TEXT NOT NULL,
                    PRIMARY KEY(matrix,day)
                );
            ''')

    def ingest(self, matrix: str, signals: list[dict]) -> int:
        """Retain discovered references, including those not chosen for a recommendation."""
        added = 0
        with sqlite_util.connect(self.path, write=True) as db:
            for row in signals:
                try:
                    url = reference_url(row["url"])
                except (ValueError, KeyError):
                    continue
                category, title = row.get("category", ""), str(row.get("title") or "")[:300]
                if not category or not title:
                    continue
                key, now = identity(matrix, category, url), time.time()
                exists = db.execute("SELECT 1 FROM examples WHERE id=?", (key,)).fetchone()
                db.execute("INSERT INTO examples VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                           "title=excluded.title,summary=excluded.summary,seen_at=excluded.seen_at",
                           (key, matrix, category, title, url, str(row.get("excerpt") or row.get("summary") or "")[:1200], now, now))
                added += not bool(exists)
        return added

    def add_pattern(self, matrix: str, category: str, *, name: str, title_pattern: str,
                    outline: list[str], example_ids: list[str]) -> str:
        key = identity(matrix, category, title_pattern)
        with sqlite_util.connect(self.path, write=True) as db:
            allowed = {r[0] for r in db.execute("SELECT id FROM examples WHERE matrix=? AND category=?", (matrix, category))}
            refs = [ref for ref in dict.fromkeys(example_ids) if ref in allowed]
            db.execute("INSERT OR IGNORE INTO patterns VALUES (?,?,?,?,?,?,?,?)",
                       (key, matrix, category, name[:100], title_pattern[:300], json.dumps(outline[:8], ensure_ascii=False),
                        json.dumps(refs), time.time()))
        return key

    def seed(self, config_dir: str, matrix: str) -> None:
        path = Path(config_dir) / "theme_library" / f"{matrix}.yaml"
        if not path.exists():
            return
        source = path.read_text()
        digest = identity(source)
        with sqlite_util.connect(self.path) as db:
            old = db.execute("SELECT digest FROM seed_versions WHERE matrix=?", (matrix,)).fetchone()
        if old and old[0] == digest:
            return
        for row in (yaml.safe_load(source) or {}).get("patterns", []):
            category = row["category"]
            refs = [{**ref, "category": category} for ref in row.get("examples", [])]
            self.ingest(matrix, refs)
            self.add_pattern(matrix, category, name=row["name"], title_pattern=row["title_pattern"],
                             outline=row["outline"], example_ids=[identity(matrix, category, reference_url(r["url"])) for r in refs])
        with sqlite_util.connect(self.path, write=True) as db:
            db.execute("INSERT OR REPLACE INTO seed_versions VALUES (?,?)", (matrix, digest))

    def snapshot(self, matrix: str) -> dict:
        with sqlite_util.connect(self.path) as db:
            examples = [dict(r) for r in db.execute("SELECT * FROM examples WHERE matrix=? ORDER BY seen_at DESC,id", (matrix,))]
            patterns = [{**dict(r), "outline": json.loads(r["outline"]), "example_ids": json.loads(r["example_ids"])}
                        for r in db.execute("SELECT * FROM patterns WHERE matrix=? ORDER BY category,created_at", (matrix,))]
            runs = [{**dict(r), "result": json.loads(r["result"])} for r in db.execute(
                "SELECT * FROM idea_runs WHERE matrix=? ORDER BY created_at DESC LIMIT 10", (matrix,))]
        return {"examples": examples, "patterns": patterns, "runs": runs}

    def queue(self, matrix: str, brief: str, category: str = "", pattern_id: str = "") -> str:
        key = uuid.uuid4().hex
        with sqlite_util.connect(self.path, write=True) as db:
            db.execute("UPDATE idea_runs SET status='interrupted',finished_at=?,error=? WHERE matrix=? AND status IN ('queued','running') "
                       "AND id NOT IN (SELECT request_id FROM daily_requests)",
                       (time.time(), "上次生成已中断，保留原有主题", matrix))
            db.execute("INSERT INTO idea_runs(id,matrix,status,brief,category,pattern_id,created_at) VALUES (?,?,'queued',?,?,?,?)",
                       (key, matrix, brief, category, pattern_id, time.time()))
        return key

    def daily_request(self, matrix: str, day: str, brief: str) -> str:
        """Reuse a day's successful or unfinished batch; retain every failed attempt."""
        with sqlite_util.connect(self.path, write=True) as db:
            old = db.execute("SELECT r.id,r.status FROM daily_requests d JOIN idea_runs r ON r.id=d.request_id "
                             "WHERE d.matrix=? AND d.day=?", (matrix, day)).fetchone()
            if old and old["status"] in {"queued", "running", "success"}:
                return old["id"]
            key = uuid.uuid4().hex
            db.execute("INSERT INTO idea_runs(id,matrix,status,brief,category,pattern_id,created_at) VALUES (?,?,'queued',?,'','',?)",
                       (key, matrix, brief, time.time()))
            db.execute("INSERT OR REPLACE INTO daily_requests VALUES (?,?,?)", (matrix, day, key))
            return key

    def for_theme(self, matrix: str, theme_id: str, generation_id: str = "") -> dict | None:
        if generation_id:
            row = self.run(generation_id)
            if row and row["matrix"] == matrix:
                return row
        with sqlite_util.connect(self.path) as db:
            row = db.execute("SELECT * FROM idea_runs WHERE matrix=? AND EXISTS "
                             "(SELECT 1 FROM json_each(result,'$.theme_ids') WHERE value=?) ORDER BY created_at DESC LIMIT 1",
                             (matrix, theme_id)).fetchone()
        return {**dict(row), "result": json.loads(row["result"])} if row else None

    def run(self, key: str) -> dict | None:
        with sqlite_util.connect(self.path) as db:
            row = db.execute("SELECT * FROM idea_runs WHERE id=?", (key,)).fetchone()
        return {**dict(row), "result": json.loads(row["result"])} if row else None

    def finish(self, key: str, status: str, result: dict, error: str = "") -> None:
        with sqlite_util.connect(self.path, write=True) as db:
            db.execute("UPDATE idea_runs SET status=?,finished_at=?,result=?,error=? WHERE id=?",
                       (status, None if status == "running" else time.time(), json.dumps(result, ensure_ascii=False), error, key))

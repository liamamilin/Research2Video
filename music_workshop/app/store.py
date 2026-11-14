"""Small isolated store with revision checks and durable project history."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .models import Project


class Conflict(Exception):
    pass


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL, document TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS revisions (
                    project_id TEXT NOT NULL, version INTEGER NOT NULL,
                    saved_at TEXT NOT NULL, document TEXT NOT NULL,
                    PRIMARY KEY (project_id, version)
                );
            """)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def unpack(row):
        if row is None:
            raise KeyError("作品不存在")
        return {"id": row["id"], "version": row["version"],
                "updated_at": row["updated_at"], "project": json.loads(row["document"])}

    def list(self):
        with self.connect() as db:
            return [self.unpack(r) for r in db.execute("SELECT * FROM projects ORDER BY updated_at DESC")]

    def get(self, project_id: str):
        with self.connect() as db:
            return self.unpack(db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone())

    def save(self, project: Project, project_id: str | None = None, version: int | None = None):
        now = datetime.now(timezone.utc).isoformat()
        document = project.model_dump_json()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if project_id is None:
                project_id, next_version = str(uuid4()), 1
                db.execute("INSERT INTO projects VALUES (?,?,?,?)", (project_id, next_version, now, document))
            else:
                row = db.execute("SELECT version FROM projects WHERE id=?", (project_id,)).fetchone()
                if row is None:
                    raise KeyError("作品不存在")
                if row["version"] != version:
                    raise Conflict("作品已在另一处更新。请重新打开作品后继续，当前输入暂时保留。")
                next_version = version + 1
                db.execute("UPDATE projects SET version=?,updated_at=?,document=? WHERE id=?",
                           (next_version, now, document, project_id))
            db.execute("INSERT INTO revisions VALUES (?,?,?,?)", (project_id, next_version, now, document))
        return self.get(project_id)

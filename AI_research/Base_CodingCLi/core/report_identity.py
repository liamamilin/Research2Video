"""Content identities for immutable research report runs."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from core.fileio import atomic_write


def describe(path: str, root: str) -> dict:
    relative = os.path.relpath(path, root).replace(os.sep, "/")
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    identity = hashlib.sha256((relative + "\n" + digest).encode()).hexdigest()
    return {"schema": "research-report/v1", "id": identity, "path": relative, "sha256": digest}


def publish(path: str, root: str) -> dict:
    record = describe(path, root)
    atomic_write(path + ".report.json", json.dumps(record, ensure_ascii=False))
    return record


def version(path: str, root: str) -> str:
    return describe(path, root)["id"] if Path(path + ".report.json").is_file() else ""

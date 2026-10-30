"""Portable report-run identities; completed report files remain immutable."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from core.fileio import atomic_write


def describe(path: str, root: str) -> dict:
    data = Path(path).read_bytes()
    relative = os.path.relpath(path, root).replace(os.sep, "/")
    digest = hashlib.sha256(data).hexdigest()
    key = hashlib.sha256((relative + "\n" + digest).encode()).hexdigest()
    return {"schema": "research-report/v1", "id": key, "path": relative, "sha256": digest}


def publish(path: str, root: str) -> dict:
    record = describe(path, root)
    atomic_write(path + ".report.json", json.dumps(record, ensure_ascii=False))
    return record


def version(path: str, root: str) -> str:
    """Legacy reports keep their IDs until explicitly migrated or rerun.

    A changed file with an older manifest still gets a new content identity;
    reading never updates its manifest or any historical script.
    """
    if not Path(path + ".report.json").is_file():
        return ""
    return describe(path, root)["id"]

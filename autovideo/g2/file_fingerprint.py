"""Reuse file digests while filesystem identity, size and nanosecond dates match."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4


def signature(path: Path) -> list[int]:
    stat = path.stat()
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def digest(path: Path, cache: Path) -> str:
    path = path.resolve()
    before = signature(path)
    record = cache / (hashlib.sha256(str(path).encode()).hexdigest() + ".json")
    try:
        saved = json.loads(record.read_text())
        if saved["signature"] == before and re.fullmatch(r"[a-f0-9]{64}", saved["sha256"]):
            return saved["sha256"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    if signature(path) != before:
        raise OSError("Voice dependency changed while calculating its fingerprint")
    result = value.hexdigest()
    temporary = record.with_suffix("." + uuid4().hex + ".tmp")
    try:
        cache.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps({"signature": before, "sha256": result}))
        temporary.replace(record)
    except OSError:
        pass  # An unwritable cache does not prevent normal voice generation.
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return result

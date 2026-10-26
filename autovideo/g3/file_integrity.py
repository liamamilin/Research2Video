"""Bounded streaming hashes for unchanged, immutable task artifacts."""
from functools import lru_cache
import hashlib
from pathlib import Path


def _identity(path: Path) -> tuple[int, ...]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


@lru_cache(maxsize=256)
def _digest(path: Path, identity: tuple[int, ...]) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    if _identity(path) != identity:
        raise OSError("Artifact changed during integrity verification")
    return value.hexdigest()


def artifact_digest(path: Path) -> str:
    """Reuse a digest only while file identity, size and both timestamps match."""
    return _digest(path, _identity(path))

import hashlib
import json
import os
from pathlib import Path

from g2.file_fingerprint import digest


def test_warm_digest_skips_file_reads_and_same_size_edit_invalidates_even_with_restored_mtime(tmp_path, monkeypatch):
    model = tmp_path / "weights.bin"
    model.write_bytes(b"first")
    before = model.stat()
    reads = []
    original = Path.open

    def opened(path, *args, **kwargs):
        if path == model and args and args[0] == "rb":
            reads.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", opened)
    cache = tmp_path / "fingerprints"
    assert digest(model, cache) == hashlib.sha256(b"first").hexdigest()
    assert digest(model, cache) == hashlib.sha256(b"first").hexdigest()
    assert len(reads) == 1
    model.write_bytes(b"other")
    os.utime(model, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert digest(model, cache) == hashlib.sha256(b"other").hexdigest()
    assert len(reads) == 2
    json_file = next(cache.glob("*.json"))
    json_file.write_text(json.dumps({"signature": [], "sha256": "invalid"}))
    assert digest(model, cache) == hashlib.sha256(b"other").hexdigest()
    assert len(reads) == 3


def test_unwritable_fingerprint_cache_still_returns_verified_digest(tmp_path):
    model = tmp_path / "model"
    model.write_bytes(b"weights")
    cache = tmp_path / "not-a-folder"
    cache.write_text("occupied")
    assert digest(model, cache) == hashlib.sha256(b"weights").hexdigest()

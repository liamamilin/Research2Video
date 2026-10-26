import hashlib
import os
from pathlib import Path

from g3.file_integrity import artifact_digest


def test_repeated_video_ranges_reuse_hash_without_loading_the_whole_mp4(tmp_path, monkeypatch):
    path = tmp_path / "video.mp4"
    content = b"media" * 400000
    path.write_bytes(content)
    opened = []
    original = Path.open

    def track(self, *args, **kwargs):
        opened.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", track)
    monkeypatch.setattr(Path, "read_bytes", lambda _: (_ for _ in ()).throw(AssertionError("whole-file read")))
    expected = hashlib.sha256(content).hexdigest()
    assert artifact_digest(path) == expected
    assert artifact_digest(path) == expected
    assert opened == [path]


def test_same_size_edit_with_restored_mtime_invalidates_digest(tmp_path):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"original")
    before = path.stat()
    original = artifact_digest(path)
    path.write_bytes(b"modified")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert artifact_digest(path) != original
    assert artifact_digest(path) == hashlib.sha256(b"modified").hexdigest()


def test_atomic_replacement_invalidates_digest(tmp_path):
    path, replacement = tmp_path / "video.mp4", tmp_path / "replacement.mp4"
    path.write_bytes(b"original")
    original = artifact_digest(path)
    replacement.write_bytes(b"replaced")
    replacement.replace(path)
    assert artifact_digest(path) != original


def test_mutation_during_hash_is_rejected_and_never_cached(tmp_path, monkeypatch):
    import pytest
    import g3.file_integrity as integrity
    path = tmp_path / "video.mp4"
    path.write_bytes(b"original")
    real = integrity._identity
    calls = []

    def changing(candidate):
        calls.append(candidate)
        result = real(candidate)
        return (*result[:-1], result[-1] + 1) if len(calls) == 2 else result

    monkeypatch.setattr(integrity, "_identity", changing)
    with pytest.raises(OSError, match="changed"):
        artifact_digest(path)
    monkeypatch.setattr(integrity, "_identity", real)
    assert artifact_digest(path) == hashlib.sha256(b"original").hexdigest()

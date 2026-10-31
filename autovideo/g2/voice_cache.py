"""Verified chapter audio reuse; chapter boundaries reset VoxCPM continuation."""
from __future__ import annotations

import fcntl
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import uuid
import wave

MAX_BYTES = 512 * 1024 * 1024


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def identity(project: Path, engine: dict, voice: str, settings: dict) -> dict:
    """Invalidate on model, anchor, tokenizer, implementation or sampling changes."""
    from g2.voices import voxcpm_paths
    _, model, anchor = voxcpm_paths(project)
    paths = [model / name for name in ("model.safetensors", "config.json", "tokenizer.json",
                                      "tokenizer_config.json", "special_tokens_map.json")]
    paths += [anchor, engine["entry"], project / "g2/voices.py", project / "g2/tts_text.py",
              project / "g2/voice_performance.py", Path(__file__)]
    from g2.file_fingerprint import digest, __file__ as fingerprint_source
    paths.append(Path(fingerprint_source))
    runtime = engine["python"].parent.parent
    paths += sorted(runtime.glob("lib/python*/site-packages/mlx_audio/tts/models/voxcpm2/*.py"))
    paths += sorted(runtime.glob("lib/python*/site-packages/mlx_audio-*.dist-info/METADATA"))
    files = {str(path.relative_to(project)): digest(path, project / "g3/data/voice-cache/fingerprints") for path in paths}
    return {"version": 1, "voice": voice, "settings": settings, "files": files,
            "model_sha256": files[str((model / "model.safetensors").relative_to(project))]}


class VoiceChapterCache:
    def __init__(self, root: Path, fingerprint: dict, segments: list[dict]):
        self.root = root
        self.fingerprint = fingerprint
        self.groups = []
        # Do not join separated chapters that happen to share an ID.
        for segment in segments:
            if not self.groups or self.groups[-1][0]["chapter_id"] != segment.get("chapter_id"):
                self.groups.append([])
            self.groups[-1].append({**segment, "chapter_id": segment.get("chapter_id")})
        self.entries = [(self._key(group), group) for group in self.groups]
        self.header = {}
        self.hits = []
        self.misses = []
        self.write_errors = []
        self.evicted = 0

    def _key(self, group: list[dict]) -> str:
        # IDs/locations do not affect the voice; order and all spoken text do.
        value = {"engine": self.fingerprint, "text": [s["text"] for s in group]}
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                         separators=(",", ":")).encode()).hexdigest()

    def restore(self, output: Path) -> dict:
        cached = {}
        output.mkdir(parents=True, exist_ok=True)
        for key, group in self.entries:
            entry = self.root / key
            try:
                if entry.is_symlink():
                    raise ValueError("linked cache entry")
                manifest = json.loads((entry / "entry.json").read_text())
                assert manifest["key"] == key and len(manifest["segments"]) == len(group)
                for index, row in enumerate(manifest["segments"]):
                    path = entry / f"{index:04d}.wav"
                    assert not path.is_symlink() and path.stat().st_size == row["bytes"]
                    assert sha256(path) == row["sha256"]
                    with wave.open(str(path)) as wav:
                        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 24000)
                        assert wav.getnframes() > 0
                    assert row["report"]["in_range"] is True
                restored = {}
                for index, (segment, row) in enumerate(zip(group, manifest["segments"])):
                    shutil.copyfile(entry / f"{index:04d}.wav", output / f"{segment['id']}.wav")
                    restored[segment["id"]] = {**row["report"], "id": segment["id"], "chapter": segment.get("chapter_id"),
                        "cache_hit": True, "source_generation_seconds": row["report"].get("generation_seconds"),
                        "generation_seconds": 0}
                cached.update(restored)
                self.header = manifest["header"]
                self.hits.append(key)
                try:
                    (entry / ".access").touch()
                except OSError:
                    pass
            except (OSError, ValueError, KeyError, TypeError, AssertionError, wave.Error):
                self.misses.append((key, group))
        return cached

    def publish(self, output: Path, report: dict) -> None:
        reports = {row["id"]: row for row in report.get("segments", [])}
        for key, group in self.misses:
            if any(s["id"] not in reports or reports[s["id"]].get("in_range") is not True for s in group):
                continue
            staging = None
            try:
                self.root.mkdir(parents=True, exist_ok=True)
                staging = Path(tempfile.mkdtemp(prefix=".tmp-", dir=self.root))
                rows = []
                for index, segment in enumerate(group):
                    path = staging / f"{index:04d}.wav"
                    shutil.copyfile(output / f"{segment['id']}.wav", path)
                    rows.append({"sha256": sha256(path), "bytes": path.stat().st_size,
                                 "report": reports[segment["id"]]})
                header = {k: report[k] for k in ("voice", "steps", "chain", "anchor_units_per_second")}
                (staging / "entry.json").write_text(json.dumps({"key": key, "header": header, "segments": rows}))
                with (self.root / ".publish.lock").open("a+") as lock:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                    def size_of(folder: Path) -> int:
                        return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file() and not p.is_symlink())
                    incoming = size_of(staging)
                    if incoming > MAX_BYTES:
                        self.write_errors.append("cache budget reached; audio still generated normally")
                        continue
                    entries = [p for p in self.root.iterdir() if p.is_dir() and not p.is_symlink()
                               and re.fullmatch(r"[a-f0-9]{64}(?:\.prior-[a-f0-9]{32})?", p.name)]
                    size = sum(size_of(p) for p in entries)
                    def used(folder: Path) -> int:
                        marker = folder / ".access"
                        receipt = folder / "entry.json"
                        return (marker if marker.exists() else receipt if receipt.exists() else folder).stat().st_mtime_ns
                    for entry in sorted(entries, key=used):
                        if size + incoming <= MAX_BYTES:
                            break
                        released = size_of(entry)
                        shutil.rmtree(entry)  # Cache copies only; original attempt WAVs are retained.
                        size -= released
                        self.evicted += 1
                    target = self.root / key
                    if target.exists():
                        target.rename(self.root / f"{key}.prior-{uuid.uuid4().hex}")
                    staging.rename(target)
            except (OSError, ValueError, KeyError) as exc:
                self.write_errors.append(type(exc).__name__)
            finally:
                if staging is not None and staging.exists():
                    shutil.rmtree(staging)

    def report(self, cached: dict) -> dict:
        return {"enabled": True, "chapter_hits": len(self.hits), "chapter_misses": len(self.misses),
                "segment_hits": len(cached), "segment_misses": sum(len(g) for _, g in self.misses),
                "entry_keys": self.hits, "write_errors": self.write_errors, "evicted_chapters": self.evicted,
                "rule": "complete chapters only; model/voice/settings/text-bound, WAV SHA verified"}

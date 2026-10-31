import copy
import json
import struct
import wave

import pytest

from g2.voice_cache import VoiceChapterCache
from g2.voice_performance import ReferenceEncoderCache


SEGMENTS = [{"id": "c1_n1", "chapter_id": "c1", "text": "缓存有条件。"},
            {"id": "c1_n2", "chapter_id": "c1", "text": "条件改变需要重做。"},
            {"id": "c2_n1", "chapter_id": "c2", "text": "另一章独立生成。"}]


def populate(tmp_path):
    output = tmp_path / "fresh"
    output.mkdir()
    report = {"voice": "voxcpm_anchor", "steps": 10, "chain": "guarded", "anchor_units_per_second": 4.8,
              "segments": []}
    for index, segment in enumerate(SEGMENTS):
        with wave.open(str(output / f"{segment['id']}.wav"), "wb") as wav:
            wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            wav.writeframes(struct.pack("<240h", *([index + 1] * 240)))
        report["segments"].append({"id": segment["id"], "chapter": segment["chapter_id"],
            "seconds": .01, "in_range": True, "generation_seconds": 7.0})
    cache = VoiceChapterCache(tmp_path / "cache", {"model": "one", "settings": {"steps": 10}}, SEGMENTS)
    assert not cache.restore(tmp_path / "first")
    cache.publish(output, report)
    return cache, output


def test_chapter_reuse_verifies_audio_and_remaps_ids_without_cross_chapter_state(tmp_path):
    cache, output = populate(tmp_path)
    moved = [{**s, "id": f"x{i}", "chapter_id": "other" + s["chapter_id"]} for i, s in enumerate(SEGMENTS)]
    second = VoiceChapterCache(cache.root, cache.fingerprint, moved)
    restored = tmp_path / "second"
    rows = second.restore(restored)
    assert len(rows) == 3 and second.report(rows)["chapter_hits"] == 2
    assert all(row["cache_hit"] and row["generation_seconds"] == 0 for row in rows.values())
    for before, after in zip(SEGMENTS, moved):
        assert (restored / f"{after['id']}.wav").read_bytes() == (output / f"{before['id']}.wav").read_bytes()


@pytest.mark.parametrize("change", ["text", "model", "settings", "wave"])
def test_input_or_audio_changes_never_reuse_a_stale_chapter(tmp_path, change):
    cache, output = populate(tmp_path)
    segments, identity = copy.deepcopy(SEGMENTS), copy.deepcopy(cache.fingerprint)
    if change == "text":
        segments[0]["text"] += "新条件。"
    elif change == "model":
        identity["model"] = "two"
    elif change == "settings":
        identity["settings"]["steps"] = 8
    else:
        path = cache.root / cache.entries[0][0] / "0001.wav"
        path.write_bytes(b"x" * path.stat().st_size)
    second = VoiceChapterCache(cache.root, identity, segments)
    rows = second.restore(tmp_path / "restore")
    assert "c1_n1" not in rows and "c1_n2" not in rows
    assert ("c2_n1" in rows) == (change in {"text", "wave"})


def test_cache_write_failure_does_not_remove_the_generated_audio(tmp_path, monkeypatch):
    cache, output = populate(tmp_path)
    monkeypatch.setattr("g2.voice_cache.MAX_BYTES", 0)
    before = (output / "c1_n1.wav").read_bytes()
    report = json.loads((cache.root / cache.entries[0][0] / "entry.json").read_text())
    cache.publish(output, {**report["header"], "segments": [r["report"] for r in report["segments"]]})
    assert cache.write_errors and (output / "c1_n1.wav").read_bytes() == before
    assert not list(cache.root.glob(".tmp-*"))


def test_full_cache_evicts_oldest_copies_and_preserves_original_attempt_audio(tmp_path, monkeypatch):
    cache, output = populate(tmp_path)
    old = cache.root / cache.entries[0][0]
    recent = cache.root / cache.entries[1][0]
    (recent / ".access").touch()
    source = {p.name: p.read_bytes() for p in output.glob("*.wav")}
    size = sum(p.stat().st_size for p in cache.root.rglob("*") if p.is_file())
    monkeypatch.setattr("g2.voice_cache.MAX_BYTES", size + 100)
    changed = [{**SEGMENTS[-1], "text": "一个新的独立章节。"}]
    incoming = VoiceChapterCache(cache.root, cache.fingerprint, changed)
    incoming.restore(tmp_path / "missing")
    receipt = json.loads((recent / "entry.json").read_text())
    incoming.publish(output, {**receipt["header"], "segments": [row["report"] for row in receipt["segments"]]})
    assert incoming.evicted == 1 and not old.exists() and recent.exists()
    assert not incoming.write_errors
    assert {p.name: p.read_bytes() for p in output.glob("*.wav")} == source
    assert sum(p.stat().st_size for p in cache.root.rglob("*") if p.is_file()) <= size + 100
    restored = incoming.restore(tmp_path / "new-restored")
    assert SEGMENTS[-1]["id"] in restored


def test_reference_encoding_cache_distinguishes_padding_and_never_caches_continuation():
    class Model:
        def __init__(self):
            self.calls = []
        def _encode_wav(self, audio, **kwargs):
            result = object()
            self.calls.append((audio, kwargs))
            return result
    anchor, continuation, model = object(), object(), Model()
    evaluated = []
    cache = ReferenceEncoderCache(model, anchor, evaluated.append)
    right = model._encode_wav(anchor, padding_mode="right")
    assert model._encode_wav(anchor, padding_mode="right") is right
    assert model._encode_wav(anchor, padding_mode="left") is not right
    assert model._encode_wav(continuation) is not model._encode_wav(continuation)
    assert len(model.calls) == 4 and len(evaluated) == 2
    assert cache.report()["hits"] == 1

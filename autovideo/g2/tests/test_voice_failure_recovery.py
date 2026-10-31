"""A failed local model keeps complete chapters and redoes an incomplete chapter."""
import json
import struct
import wave
from pathlib import Path

import pytest

from g2 import run_g2, voice_cache


def test_model_failure_publishes_only_complete_chapters_for_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(run_g2, "PROJECT", tmp_path)
    monkeypatch.setattr(run_g2, "ARTIFACTS", tmp_path / "artifacts")
    output = tmp_path / "segments"
    output.mkdir()
    monkeypatch.setattr(run_g2, "SEGMENTS_DIR", output)
    monkeypatch.setattr(run_g2, "VOICE", "voxcpm_anchor")
    monkeypatch.setattr(voice_cache, "identity", lambda *a: {"model_sha256": "model", "voice": "anchor"})
    segments = [{"id": "n1", "chapter_id": "c1", "text": "第一章完成。"},
                {"id": "n2", "chapter_id": "c2", "text": "第二章开头。"},
                {"id": "n3", "chapter_id": "c2", "text": "第二章结尾。"}]
    calls = []

    def model(*args, **kwargs):
        work = json.loads((tmp_path / "artifacts/voice-work-input.json").read_text())
        calls.append([s["id"] for s in work["segments"]])
        selected = work["segments"][:2] if len(calls) == 1 else work["segments"]
        receipt = {"voice": "anchor", "steps": 10, "chain": "anchor", "anchor_units_per_second": 4.8, "segments": []}
        for segment in selected:
            with wave.open(str(output / f"{segment['id']}.wav"), "wb") as wav:
                wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
                wav.writeframes(struct.pack("<240h", *([1] * 240)))
            receipt["segments"].append({"id": segment["id"], "in_range": True})
        (output / "voice-report.json").write_text(json.dumps(receipt))
        if len(calls) == 1:
            raise run_g2.G2Fault("COMMAND_TIMEOUT", "model stalled")

    monkeypatch.setattr(run_g2, "_run_fixed_command", model)
    engine = {"python": Path("python"), "entry": Path("voice.py")}
    with pytest.raises(run_g2.G2Fault, match="model stalled"):
        run_g2.synthesize_mlx_voice(engine, {"segments": segments}, {})
    metrics = run_g2.synthesize_mlx_voice(engine, {"segments": segments}, {})
    assert calls == [["n1", "n2", "n3"], ["n2", "n3"]]
    assert metrics["segment_hits"] == 1 and metrics["chapter_hits"] == 1
    final = json.loads((tmp_path / "artifacts/voice-report.json").read_text())
    assert [s["id"] for s in final["segments"]] == ["n1", "n2", "n3"]

"""Benchmark cache restoration with the real generated WAVs, without new synthesis."""
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def main():
    from g2 import run_g2, voice_cache, voices
    out = Path(__file__).resolve().parent / "real"
    location = json.loads((out / "location.json").read_text())
    attempt = max((Path(location["engine_data"]) / "jobs" / location["job_id"] / "attempts").iterdir(), key=lambda p: int(p.name)) / "g2-run"
    plan = json.loads((attempt / "artifacts/voice-input.json").read_text())
    generated = attempt / "fixtures/assets/segments"
    report = json.loads((generated / "voice-report.json").read_text())
    engine = voices.mlx_engine("voxcpm_anchor", ROOT)
    started = time.monotonic()
    fingerprint = voice_cache.identity(ROOT, engine, "voxcpm_anchor", plan["voice_settings"])
    first_identity = time.monotonic() - started
    replay = out / "warm-replay"
    replay.mkdir(exist_ok=False)
    cache = voice_cache.VoiceChapterCache(ROOT / "g3/data/voice-cache/voxcpm2", fingerprint, plan["segments"])
    cache.restore(replay / "first-restore")
    cache.publish(generated, report)
    assert not cache.write_errors, cache.write_errors
    run_g2.ARTIFACTS = replay / "artifacts"
    run_g2.SEGMENTS_DIR = replay / "segments"
    run_g2.VOICE = "voxcpm_anchor"
    def no_synthesis(*args, **kwargs):
        raise AssertionError("Warm replay must not start another voice process")
    run_g2._run_fixed_command = no_synthesis
    started = time.monotonic()
    metrics = run_g2.synthesize_mlx_voice(engine, plan, plan["voice_settings"])
    elapsed = time.monotonic() - started
    assert metrics["segment_hits"] == len(plan["segments"])
    assert metrics["segment_misses"] == 0
    for segment in plan["segments"]:
        name = segment["id"] + ".wav"
        assert (generated / name).read_bytes() == (replay / "segments" / name).read_bytes()
    receipt = {"first_identity_seconds": round(first_identity, 4), "warm_replay_seconds": round(elapsed, 4),
        "cache": metrics, "evicted_chapters_during_publish": cache.evicted,
        "real_generated_audio_identical": True, "new_synthesis_processes": 0}
    (out / "warm-replay.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps(receipt, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

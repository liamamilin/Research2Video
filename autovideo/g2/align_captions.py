"""Fixed offline MLX forced-aligner entry point; preserves the existing WAV."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import sys
import time

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def align_in_process(ir_path: Path, output: Path, *, cache=None, emit=None) -> None:
    import mlx.core as mx
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    from g2.caption_alignment import align_segment, narration_hash, proportional_cues, verify_plan, word_spans
    started = time.monotonic()
    from g2.workspace import project_path
    try:
        ir_path, output = project_path(ROOT, ir_path), project_path(ROOT, output)
    except ValueError as exc:
        raise ValueError("Invalid caption workspace") from exc
    ir = json.loads(ir_path.read_text())
    asset = next(a for a in ir["assets"] if a["id"] == ir["narration"]["audio_asset_id"])
    audio_path = (ir_path.parent / asset["path"]).resolve()
    if not audio_path.is_relative_to(ir_path.parent.resolve()):
        raise ValueError("Invalid narration audio path")
    audio_sha = _file_sha256(audio_path)
    if asset["sha256"] != audio_sha:
        raise ValueError("Narration audio changed before caption alignment")
    model_dir = ROOT / "g3/data/voice-models/qwen3-forced-aligner-0.6b-8bit"
    weights = model_dir / "model.safetensors"
    if not weights.is_file():
        raise ValueError("Missing local Qwen3 forced alignment model; install the pinned weights first")
    if cache is not None:
        model, load_seconds = cache.get_aligner(model_dir)
        model_sha256 = cache.get_file_sha256(weights)
    else:
        from mlx_audio.stt.utils import load_model
        t_load = time.monotonic()
        model = load_model(str(model_dir))
        load_seconds = time.monotonic() - t_load
        model_sha256 = _file_sha256(weights)
    samples, rate = sf.read(audio_path, dtype="float32")
    if samples.ndim != 1:
        raise ValueError("Caption alignment requires mono narration")
    cues, evidence = [], []
    log = emit if emit is not None else (lambda line: print(line, flush=True))
    for row in ir["narration"]["segments"]:
        a, b = round(row["start_ms"] * rate / 1000), round(row["end_ms"] * rate / 1000)
        from math import gcd
        divisor = gcd(rate, 16000)
        speech = resample_poly(samples[a:b], 16000 // divisor, rate // divisor).astype(np.float32)
        items = []
        align_error = None
        if word_spans(row["text"]):
            try:
                result = model.generate(speech, text=row["text"], language="Chinese")
                items = [{"text": item.text, "start_ms": round(item.start_time * 1000), "end_ms": round(item.end_time * 1000)}
                         for item in result.items]
            except Exception as exc:
                align_error = f"{type(exc).__name__}: {str(exc)[:200]}"
            finally:
                mx.clear_cache()
        if align_error is not None:
            segment_cues = proportional_cues(row)
            segment_evidence = {"segment_id": row["id"], "items": [], "method": "proportional_fallback",
                                "fallback_reason": align_error, "rejected_items": []}
        else:
            segment_cues, segment_evidence = align_segment(row, items, allow_proportional_fallback=True)
        cues.extend(segment_cues)
        evidence.append(segment_evidence)
        method = segment_evidence.get("method")
        if method == "measured_phrase":
            log(f"ALIGNED {row['id']}: using measured phrase (word timestamps outside audio)")
        elif method == "proportional_fallback":
            log(f"ALIGNED {row['id']}: using proportional fallback ({segment_evidence.get('fallback_reason', 'aligner mismatch')})")
        else:
            log(f"ALIGNED {row['id']}: {len(items)} units")
    plan = {"version": 2, "method": "local Qwen3 forced alignment with measured phrase fallback; original transcript and PCM unchanged",
            "narration_sha256": narration_hash(ir), "audio_sha256": audio_sha,
            "model": json.loads((model_dir / "autovideo-model-source.json").read_text()),
            "model_sha256": model_sha256,
            "load_seconds": round(load_seconds, 3),
            "warm_daemon": cache is not None,
            "generation_seconds": round(time.monotonic() - started, 3), "cues": cues, "word_evidence": evidence}
    verify_plan(ir, plan)
    output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def align(ir_path: Path, output: Path) -> None:
    from g2.workspace import project_path
    try:
        checked_ir, checked_out = project_path(ROOT, ir_path), project_path(ROOT, output)
    except ValueError as exc:
        raise ValueError("Invalid caption workspace") from exc
    if ROOT == Path(__file__).resolve().parents[1]:
        from g2.mlx_daemon import call_daemon
        if call_daemon({"op": "align", "ir_path": str(checked_ir), "output": str(checked_out)}, root=ROOT):
            return
    align_in_process(checked_ir, checked_out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    align(args.ir, args.output)

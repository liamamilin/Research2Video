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


def align(ir_path: Path, output: Path) -> None:
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly
    from mlx_audio.stt.utils import load_model
    from g2.caption_alignment import build_cues, narration_hash, verify_plan
    started = time.monotonic()
    ir_path, output = ir_path.resolve(), output.resolve()
    if not ir_path.is_relative_to(ROOT) or not output.is_relative_to(ROOT):
        raise ValueError("Invalid caption workspace")
    ir = json.loads(ir_path.read_text())
    asset = next(a for a in ir["assets"] if a["id"] == ir["narration"]["audio_asset_id"])
    audio_path = (ir_path.parent / asset["path"]).resolve()
    if not audio_path.is_relative_to(ir_path.parent):
        raise ValueError("Invalid narration audio path")
    audio_sha = hashlib.sha256(audio_path.read_bytes()).hexdigest()
    if asset["sha256"] != audio_sha:
        raise ValueError("Narration audio changed before caption alignment")
    model_dir = ROOT / "g3/data/voice-models/qwen3-forced-aligner-0.6b-8bit"
    if not (model_dir / "model.safetensors").is_file():
        raise ValueError("Missing local Qwen3 forced alignment model; install the pinned weights first")
    model = load_model(str(model_dir))
    samples, rate = sf.read(audio_path, dtype="float32")
    if samples.ndim != 1:
        raise ValueError("Caption alignment requires mono narration")
    cues, evidence = [], []
    for row in ir["narration"]["segments"]:
        a, b = round(row["start_ms"] * rate / 1000), round(row["end_ms"] * rate / 1000)
        from math import gcd
        divisor = gcd(rate, 16000)
        speech = resample_poly(samples[a:b], 16000 // divisor, rate // divisor).astype(np.float32)
        result = model.generate(speech, text=row["text"], language="Chinese")
        items = [{"text": item.text, "start_ms": round(item.start_time * 1000), "end_ms": round(item.end_time * 1000)}
                 for item in result.items]
        cues.extend(build_cues(row, items))
        evidence.append({"segment_id": row["id"], "items": items})
        print(f"ALIGNED {row['id']}: {len(items)} units", flush=True)
    plan = {"version": 2, "method": "local Qwen3 forced alignment; original transcript and PCM unchanged",
            "narration_sha256": narration_hash(ir), "audio_sha256": audio_sha,
            "model": json.loads((model_dir / "autovideo-model-source.json").read_text()),
            "model_sha256": hashlib.sha256((model_dir / "model.safetensors").read_bytes()).hexdigest(),
            "generation_seconds": round(time.monotonic() - started, 3), "cues": cues, "word_evidence": evidence}
    verify_plan(ir, plan)
    output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    align(args.ir, args.output)

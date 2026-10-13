"""Create same-scene B/C review clips and a randomized blind packet."""
from __future__ import annotations

import hashlib
import json
import random
import shutil
import subprocess
from pathlib import Path

import numpy as np

from asset_adapter import sha256_file


PROJECT = Path(__file__).resolve().parents[1]
H1 = PROJECT / "h1"
EXPERIMENT = json.loads((H1 / "experiment.json").read_text(encoding="utf-8"))
FFMPEG = PROJECT / "g0/darwin_arm64/ffmpeg"
FFPROBE = PROJECT / "g0/darwin_arm64/ffprobe"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def probe(path: Path) -> dict:
    result = subprocess.run(
        [str(FFPROBE), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def decode_audio_pcm(path: Path) -> bytes:
    result = subprocess.run(
        [str(FFMPEG), "-v", "error", "-i", str(path), "-map", "0:a:0", "-acodec", "pcm_s16le",
         "-ar", "48000", "-ac", "2", "-f", "s16le", "pipe:1"],
        check=True, capture_output=True,
    )
    return result.stdout


def extract_clip(source: Path, dest: Path, start_frame: int, end_frame: int, pair_id: str, variant: str) -> dict:
    if dest.exists():
        raise RuntimeError(f"refusing to overwrite review clip: {dest}")
    start = start_frame / 30
    duration = (end_frame - start_frame) / 30
    argv = [
        str(FFMPEG), "-hide_banner", "-loglevel", "error", "-i", str(source),
        "-ss", f"{start:.6f}", "-t", f"{duration:.6f}", "-map", "0:v:0", "-map", "0:a:0",
        "-vf", "fps=30", "-r", "30", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", str(dest),
    ]
    result = subprocess.run(argv, capture_output=True, text=True)
    run_record = {
        "pair_id": pair_id,
        "variant": variant,
        "command": argv,
        "exit_code": result.returncode,
        "stderr": result.stderr[-1600:],
    }
    if result.returncode != 0 or not dest.is_file():
        raise RuntimeError(f"clip extraction failed for {pair_id} {variant}: {result.stderr[-800:]}")
    checked = probe(dest)
    streams = checked["streams"]
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if not video or not audio or video.get("width") != 1920 or video.get("height") != 1080:
        raise RuntimeError(f"invalid extracted media streams for {pair_id} {variant}")
    frames = int(video.get("nb_frames", "0"))
    if frames != end_frame - start_frame:
        raise RuntimeError(f"unexpected frame count for {pair_id} {variant}: {frames}")
    full_decode = subprocess.run(
        [str(FFMPEG), "-nostdin", "-hide_banner", "-v", "error", "-xerror", "-i", str(dest),
         "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    if full_decode.returncode != 0:
        raise RuntimeError(f"full decode failed for {pair_id} {variant}: {full_decode.stderr[-800:]}")
    pcm = decode_audio_pcm(dest)
    record = {
        **run_record,
        "path": str(dest.relative_to(PROJECT)),
        "sha256": sha256_file(dest),
        "size_bytes": dest.stat().st_size,
        "duration_seconds": float(checked["format"]["duration"]),
        "frames": frames,
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name"),
        "audio_sample_rate_hz": int(audio.get("sample_rate", "0")),
        "audio_channels": audio.get("channels"),
        "audio_pcm_sha256": hashlib.sha256(pcm).hexdigest(),
        "audio_full_decode": "PASS",
    }
    write_json(dest.with_suffix(".json"), record)
    return record


def main() -> int:
    key_by_pair = {}
    clip_records = []
    pair_root = H1 / "runs/pairs-v3"
    pair_root.mkdir(parents=True, exist_ok=True)
    case_sources = EXPERIMENT["case_sources"]
    c_video_by_case = {case_id: H1 / "runs" / case_id / "C_image_motion_v3_full.mp4" for case_id in case_sources}

    for pair in EXPERIMENT["pairs"]:
        case_id = pair["case_id"]
        source_root = PROJECT / case_sources[case_id]
        source_props = json.loads((source_root / "artifacts/render-props.json").read_text(encoding="utf-8"))["ir"]
        scene = next((item for item in source_props["scenes"] if item["id"] == pair["scene_id"]), None)
        if scene is None:
            raise RuntimeError(f"scene not found: {case_id}/{pair['scene_id']}")
        base_video = source_root / "artifacts/G2_自动生成.mp4"
        c_video = c_video_by_case[case_id]
        if not c_video.is_file():
            raise RuntimeError(f"C render is missing: {c_video}")
        pair_dir = pair_root / pair["pair_id"]
        pair_dir.mkdir(parents=True, exist_ok=True)
        frames = {"start": scene["start_frame"], "end": scene["end_frame"]}
        b_record = extract_clip(base_video, pair_dir / "B.mp4", frames["start"], frames["end"], pair["pair_id"], "B")
        c_record = extract_clip(c_video, pair_dir / "C.mp4", frames["start"], frames["end"], pair["pair_id"], "C")
        if b_record["frames"] != c_record["frames"]:
            raise RuntimeError(f"B/C frame count differs in {pair['pair_id']}")
        audio_pcm_equal = b_record["audio_pcm_sha256"] == c_record["audio_pcm_sha256"]
        if not audio_pcm_equal:
            b_pcm = np.frombuffer(decode_audio_pcm(pair_dir / "B.mp4"), dtype=np.int16).astype(np.float64)
            c_pcm = np.frombuffer(decode_audio_pcm(pair_dir / "C.mp4"), dtype=np.int16).astype(np.float64)
            if len(b_pcm) != len(c_pcm) or len(b_pcm) == 0:
                raise RuntimeError(f"B/C decoded audio lengths differ in {pair['pair_id']}")
            correlation = float(np.corrcoef(b_pcm, c_pcm)[0, 1])
            if correlation < 0.999:
                raise RuntimeError(f"B/C audio diverged in {pair['pair_id']}: correlation={correlation:.6f}")
        else:
            correlation = 1.0
        key_by_pair[pair["pair_id"]] = {
            "case_id": case_id,
            "scene_id": pair["scene_id"],
            "scene_frames": frames,
            "B": b_record,
            "C": c_record,
            "audio_pcm_equal": audio_pcm_equal,
            "decoded_audio_correlation": correlation,
        }

    rng = random.Random(20261013)
    all_codes = []
    for pair_id in sorted(key_by_pair):
        b_first = rng.choice([True, False])
        ordered = [("B", "left"), ("C", "right")] if b_first else [("C", "left"), ("B", "right")]
        pair_entry = {"pair_id": pair_id, "samples": {}}
        for variant, side in ordered:
            source_record = key_by_pair[pair_id][variant]
            sample_code = hashlib.sha256(f"{pair_id}:{side}:h1-review-v1".encode()).hexdigest()[:10].upper()
            sample_name = f"sample-{sample_code}.mp4"
            reviewer_path = H1 / "review/materials-v3" / sample_name
            reviewer_path.parent.mkdir(parents=True, exist_ok=True)
            if reviewer_path.exists():
                raise RuntimeError(f"refusing to overwrite blinded sample {reviewer_path}")
            shutil.copy2(PROJECT / source_record["path"], reviewer_path)
            pair_entry["samples"][side] = {
                "sample_code": sample_code,
                "file": str(reviewer_path.relative_to(PROJECT)),
                "variant": variant,
                "sha256": source_record["sha256"],
            }
            all_codes.append(sample_code)
        key_by_pair[pair_id]["blind_pair"] = pair_entry

    write_json(H1 / "review/H1_REVIEW_KEY_v3.json", {
        "warning": "Experimenter-only key. Do not give to reviewers before scoring is locked.",
        "seed": 20261013,
        "pairs": {pair_id: record["blind_pair"] for pair_id, record in key_by_pair.items()},
    })
    reviewer_pairs = []
    for pair_id, record in key_by_pair.items():
        public_pair = {"pair_id": pair_id, "samples": {}}
        for side, sample in record["blind_pair"]["samples"].items():
            public_pair["samples"][side] = {"sample_code": sample["sample_code"], "file": sample["file"]}
        reviewer_pairs.append(public_pair)
    write_json(H1 / "review/BLIND_REVIEW_PACKAGE_v3.json", {
        "experiment_id": EXPERIMENT["experiment_id"],
        "created_on": "2026-10-13",
        "reviewer_instructions": "Do not inspect run logs, model names, source run identifiers, or the experimenter-only review key. Score both clips independently for every pair before choosing a preference.",
        "pairs": reviewer_pairs,
    })
    for pair_id, record in key_by_pair.items():
        for variant in ("B", "C"):
            clip_records.append({"pair_id": pair_id, "variant": variant, **record[variant]})
    write_json(H1 / "runs/PAIR_MEDIA_MANIFEST_v3.json", {
        "experiment_id": EXPERIMENT["experiment_id"],
        "clip_count": len(clip_records),
        "pair_count": len(key_by_pair),
        "clips": clip_records,
        "pairs": [{"pair_id": pair_id,
                   "audio_pcm_equal": record["audio_pcm_equal"],
                   "decoded_audio_correlation": record["decoded_audio_correlation"],
                   "frames": record["B"]["frames"]}
                  for pair_id, record in key_by_pair.items()],
        "blind_codes": all_codes,
    })
    print(json.dumps({"pairs": len(key_by_pair), "clips": len(clip_records),
                      "blinded_samples": len(all_codes)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

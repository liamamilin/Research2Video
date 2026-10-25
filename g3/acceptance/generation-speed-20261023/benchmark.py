"""Render a preserved real IR without new LLM/TTS calls, then run full media QA.

Use a fresh output directory per variant. The source attempt is never modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

PROJECT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--concurrency", default="auto")
    args = parser.parse_args()
    source = args.source_run.resolve(strict=True)
    out = args.output.resolve()
    if not source.is_relative_to(PROJECT) or not out.is_relative_to(PROJECT):
        raise ValueError("Source and output must stay inside the project")
    out.mkdir(parents=True, exist_ok=False)
    shutil.copytree(source / "fixtures", out / "fixtures")
    (out / "artifacts").mkdir()
    (out / "input").mkdir()
    original_input = source / "input/input.json"
    if not original_input.is_file():
        original_input = source.parents[2] / "input/input.json"
    shutil.copyfile(original_input, out / "input/input.json")
    (out / "input/source.txt").write_text("性能验收：复用保存的真实讲稿、语音和分镜。")
    for name in ("captions.srt", "chapters.json", "narration-plan.json", "alignment.json"):
        if (source / "artifacts" / name).is_file():
            shutil.copyfile(source / "artifacts" / name, out / "artifacts" / name)
    os.environ.update(AUTOVIDEO_G2_RUN_DIR=str(out), AUTOVIDEO_G2_RUN_ID=out.name,
                      AUTOVIDEO_G2_DURATION_MODE="content", AUTOVIDEO_G2_SOURCE_FILE=str(out / "input/source.txt"),
                      AUTOVIDEO_G2_INTENT_FILE=str(out / "input/input.json"),
                      AUTOVIDEO_G2_RENDER_CONCURRENCY=args.concurrency)
    from g2 import run_g2 as pipeline
    from g1.video_ir import validate_video_ir
    compiled = validate_video_ir(json.loads(pipeline.IR_PATH.read_text()), pipeline.FIXTURE_ROOT)
    started = time.monotonic()
    pipeline.render(compiled, renderer_root=out / "renderer")
    render_seconds = time.monotonic() - started
    qa = pipeline.verify_media(compiled, {})
    qa.update(real_llm=False, real_tts=False, narration_reused=True, tts_reused=True,
              scope="Renderer/package/media QA using preserved real narration and IR; no new LLM/TTS")
    pipeline.write_json(pipeline.ARTIFACTS / "qa.json", qa)
    config = pipeline.package_request_from_intent()
    if config:
        pipeline.run_package_stage(config)
    hashes = pipeline._run_fixed_command("decoded-frame-hashes", [pipeline.FFMPEG, "-nostdin", "-v", "error",
        "-i", pipeline.VIDEO_PATH, "-map", "0:v:0", "-f", "framemd5", "-"], PROJECT)
    frame_hashes = "\n".join(line for line in hashes.splitlines() if not line.startswith("#"))
    metrics = {"source_run": str(source.relative_to(PROJECT)),
               "resources": json.loads((pipeline.ARTIFACTS / "render-resources.json").read_text()),
               "render_seconds": round(render_seconds, 3), "content_seconds": compiled["total_frames"] / pipeline.FPS,
               "frames": compiled["total_frames"], "decoded_frames_sha256": hashlib.sha256(frame_hashes.encode()).hexdigest(),
               "ir_sha256": pipeline.digest(pipeline.IR_PATH), "qa_status": qa["status"],
               "tts_correlation": qa["audio"]["decoded_tts_correlation"],
               "new_llm_calls": 0, "new_tts_calls": 0,
               "output": str(pipeline.VIDEO_PATH.relative_to(PROJECT))}
    pipeline.write_json(pipeline.ARTIFACTS / "benchmark.json", metrics)
    pipeline.write_manifest(renderer_root=out / "renderer")
    print(json.dumps(metrics, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

"""Reproduce the failed caption job in a fresh external workspace, offline."""
from pathlib import Path
import json
import os
import shutil
import sys
import time

PROJECT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT))
SOURCE = PROJECT / "g3/data/jobs/728086c8-e3d4-426e-b437-a0b513523e95/attempts/1/g2-run"


def main():
    from g2.workspace import project_path
    output = PROJECT / "g3/data/jobs" / ("_stability_" + time.strftime("%Y%m%dT%H%M%S"))
    output.mkdir(exist_ok=False)
    shutil.copytree(SOURCE / "fixtures", output / "fixtures")
    shutil.copytree(SOURCE / "artifacts", output / "artifacts")
    (output / "logs").mkdir()
    shutil.copytree(SOURCE.parents[2] / "input", output / "input")
    os.environ.update(AUTOVIDEO_G2_RUN_DIR=str(output.resolve()), AUTOVIDEO_G2_RUN_ID=output.name,
                      AUTOVIDEO_G2_SOURCE_FILE=str((output / "input/source.txt").resolve()),
                      AUTOVIDEO_G2_INTENT_FILE=str((output / "input/input.json").resolve()),
                      AUTOVIDEO_G2_DURATION_MODE="content", AUTOVIDEO_G2_VIDEO_FORM="information_motion")
    from g2 import run_g2 as pipeline
    from g2 import caption_alignment
    from g1.video_ir import validate_video_ir
    from g2.authored_script import verify_outputs
    assert pipeline.RUN_ROOT == project_path(PROJECT, output.resolve())
    ir = json.loads(pipeline.IR_PATH.read_text())
    source_ir_sha = pipeline.digest(SOURCE / "fixtures/video-ir-v0.2.json")
    source_wav_sha = pipeline.digest(SOURCE / "fixtures/assets/communications_noise.wav")
    started = time.monotonic()
    captions = caption_alignment.prepare(pipeline, ir)
    caption_seconds = time.monotonic() - started
    print(f"CAPTIONS PASS {len(captions['cues'])} cues; {caption_seconds:.3f}s", flush=True)
    compiled = validate_video_ir(ir, pipeline.FIXTURE_ROOT)
    started = time.monotonic()
    pipeline.render(compiled, renderer_root=output / "renderer")
    render_seconds = time.monotonic() - started
    qa = pipeline.verify_media(compiled, {})
    qa.update(real_llm=False, real_tts=False, narration_reused=True, tts_reused=True,
              scope="Original failed IR/audio; new offline forced alignment and actual render")
    words = verify_outputs(pipeline, pipeline.PROJECT_INPUT.read_text())
    assert pipeline.digest(pipeline.IR_PATH) == source_ir_sha
    assert pipeline.digest(pipeline.ASSETS / "communications_noise.wav") == source_wav_sha
    assert pipeline.digest(SOURCE / "fixtures/video-ir-v0.2.json") == source_ir_sha
    assert pipeline.digest(SOURCE / "fixtures/assets/communications_noise.wav") == source_wav_sha
    pipeline.write_json(pipeline.ARTIFACTS / "qa.json", qa)
    pipeline.write_manifest(renderer_root=output / "renderer")
    result = {"status": "PASS", "source_job": "728086c8-e3d4-426e-b437-a0b513523e95",
              "physical_output": str(output.resolve()), "video": str(pipeline.VIDEO_PATH),
              "caption_count": len(captions["cues"]), "caption_seconds": round(caption_seconds, 3),
              "render_seconds": round(render_seconds, 3), "media": qa, "authored_words": words,
              "source_ir_unchanged": True, "source_wav_unchanged": True,
              "new_llm_calls": 0, "new_tts_calls": 0}
    evidence = Path(__file__).parent / "render-result.json"
    evidence.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k not in {"media", "authored_words"}}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

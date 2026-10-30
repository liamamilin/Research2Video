"""Resume a compiled, authored explanation after rendering failed in a sibling attempt."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil


def checkpoint(pipeline, source: str) -> dict | None:
    prior = os.environ.get("AUTOVIDEO_G2_PREVIOUS_RUN_DIR")
    if not pipeline.CONTENT_MODE or not pipeline.LOCK_NARRATION or not prior:
        return None
    try:
        previous = pipeline.project_path(pipeline.PROJECT, Path(prior))
        if previous == pipeline.RUN_ROOT or previous.parent.parent != pipeline.RUN_ROOT.parent.parent:
            return None
        artifacts = previous / "artifacts"
        brief = json.loads((artifacts / "input-brief.json").read_text())
        alignment = json.loads((artifacts / "alignment.json").read_text())
        ir = json.loads((previous / "fixtures/video-ir-v0.2.json").read_text())
        saved = json.loads((pipeline.PROJECT_INPUT.parent / "input.json").read_text())
        commands = [json.loads(line) for line in (previous / "logs/commands.jsonl").read_text().splitlines()]
        source_sha = hashlib.sha256(source.encode()).hexdigest()
        titles = [c["title"] for c in ir.get("content_structure", {}).get("chapters", [])]
        if (brief.get("source_sha256") != source_sha or ir["source"]["text_sha256"] != source_sha
                or ir.get("presentation", {}).get("preset") != "explanation"
                or alignment.get("voice") != pipeline.VOICE
                or float(ir.get("playback", {}).get("speed", 1)) != pipeline.OUTPUT_SPEED
                or ir["video"]["title"] != saved.get("title")
                or titles != [c["title"] for c in saved.get("chapters", [])]
                or not any(c.get("label") == "compile-ir" and c.get("exit_code") == 0 for c in commands)
                or not any(c.get("label") == "render" and c.get("exit_code") == 1 for c in commands)):
            return None
        for name in ("final-narration-plan.json", "caption-alignment.json", "captions.srt", "tts-metrics.json"):
            if not (artifacts / name).is_file():
                return None
        from g1.video_ir import validate_video_ir
        compiled = validate_video_ir(ir, previous / "fixtures")
        return {"previous": previous, "brief": brief, "alignment": alignment, "ir": ir, "compiled": compiled}
    except (OSError, ValueError, KeyError, TypeError):
        return None  # A missing/incompatible checkpoint follows the ordinary generation path.


def resume(pipeline, source: str) -> dict | None:
    ready = checkpoint(pipeline, source)
    if ready is None:
        return None
    previous = ready["previous"]
    shutil.copytree(previous / "fixtures", pipeline.FIXTURE_ROOT, dirs_exist_ok=True)
    excluded = {"model-cost.json", "render-resources.json", "render-props.json", "production-execution.json",
                "generation-schedule.json", "reused-plans.json", "render-checkpoint-reuse.json"}
    for path in (previous / "artifacts").iterdir():
        if path.is_file() and not path.is_symlink() and path.suffix in {".json", ".txt", ".srt"} and path.name not in excluded:
            shutil.copyfile(path, pipeline.ARTIFACTS / path.name)
    from g2.authored_script import verify_outputs
    pipeline.write_json(pipeline.ARTIFACTS / "narration-check.json", verify_outputs(pipeline, source))
    compiled, alignment = ready["compiled"], ready["alignment"]
    pipeline.write_json(pipeline.ARTIFACTS / "compiled-timeline.json", compiled)
    form = ready["brief"]["selected_form"]
    os.environ["AUTOVIDEO_G2_VIDEO_FORM"] = form
    evidence = {"source_attempt": str(previous), "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "audio_sha256": pipeline.digest(pipeline.ASSETS / "communications_noise.wav"),
        "video_ir_sha256": pipeline.digest(pipeline.IR_PATH), "reused_narration": True,
        "reused_voice": True, "reused_storyboard": True, "reused_captions": True,
        "new_model_calls": 0, "new_voice_calls": 0}
    pipeline.write_json(pipeline.ARTIFACTS / "render-checkpoint-reuse.json", evidence)
    for stage in ("prepare-input", "narration-plan", "tts-and-align", "storyboard-plan", "draw-things-backgrounds", "compile-ir"):
        pipeline.skip_step(stage, "复用同一讲稿、声音和倍速的已完成时间线")
    print(f"RENDER CHECKPOINT: reusing voice, captions and storyboard from {previous.name}", flush=True)
    renderer = pipeline.RUN_ROOT / "renderer"
    pipeline.run_step("render", lambda: pipeline.render(compiled, renderer_root=renderer))
    qa = pipeline.run_step("verify-media", pipeline.verify_media, compiled, alignment)
    qa.update(video_form=form, visual_storytelling=True, recovery=evidence,
              information={"scene_count": len(compiled["scenes"]),
                  "components": sorted({s["component"] for s in compiled["scenes"]}), "timing": "reused compiled timeline"})
    pipeline.add_narration_check(qa)
    pipeline.write_json(pipeline.ARTIFACTS / "qa.json", qa)
    pipeline.write_call_summary()
    pipeline.write_manifest(renderer_root=renderer)
    return qa

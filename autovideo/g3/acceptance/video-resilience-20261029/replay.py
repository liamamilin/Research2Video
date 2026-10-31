"""Replay actual failed plans and render unchanged original speech in new files."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import shutil
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

CASES = {
    "kimi-values": ("b47bbf5e-35d2-487b-b91c-7f1a9f8fc111", "c1", 3, "card_2_p1"),
    "kimi-icons": ("cb910fd5-fc29-4e46-95c5-00d53f136d82", "c2", 3, "card_3_p2"),
    "kimi-comparison": ("cb209da8-142f-4c7d-8fe6-131c397ad8a8", "c2", 3, "card_3_p2"),
    "housing-values": ("241cf913-1685-401a-a912-69c8b1afc042", "c1", 1, "card_2_p2"),
}
JOBS = sorted({v[0] for v in CASES.values()} | {"45432c04-c43a-4ea0-898f-d04aa0eea7b1"})


def save(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def load_response(path: Path):
    result = json.loads(json.loads(path.read_text())["choices"][0]["message"]["content"])
    request = json.loads(path.with_name(path.name.replace(".response.", ".request.")).read_text())
    for message in request["messages"]:
        try:
            context = json.loads(message["content"])
        except (ValueError, TypeError):
            continue
        if isinstance(context, dict) and "groups" in context:
            return result, context
    raise ValueError("Missing saved group context")


def replay(output: Path):
    from g2 import explanation
    results = []
    for job in JOBS:
        folder = ROOT / "g3/data/jobs" / job / "attempts/1/g2-run/artifacts/llm_calls"
        for response in sorted(folder.glob("*explanation*.response.json")):
            original, context = load_response(response)
            fixed = copy.deepcopy(original)
            row = {"job_id": job, "response": response.name}
            try:
                explanation.validate(fixed, context)
                row["status"] = "PASS"
                # Only decorative bindings and existing title/unit adaptation
                # can differ. Both original and normalized pages remain saved.
                save(output / "normalized" / job / response.name, {"original": original, "normalized": fixed})
            except Exception as exc:
                row.update(status="FAIL", error=type(exc).__name__ + ": " + str(exc))
            results.append(row)
    receipt = {"results": results, "passed": sum(r["status"] == "PASS" for r in results),
               "total": len(results), "new_model_calls": 0, "original_tasks_modified": False}
    save(output / "replay.json", receipt)
    print(json.dumps({k: receipt[k] for k in ("passed", "total", "new_model_calls")}), flush=True)


def render_case(name: str, out: Path):
    import math
    import numpy as np
    import soundfile as sf
    from g2 import explanation, run_g2 as pipeline

    out.mkdir(parents=True, exist_ok=False)
    job, chapter, attempt, page_id = CASES[name]
    source = ROOT / "g3/data/jobs" / job / "attempts/1/g2-run"
    response = source / f"artifacts/llm_calls/{chapter}-explanation-attempt-{attempt}.response.json"
    plan, context = load_response(response)
    page = next(p for p in plan["pages"] if p["id"] == page_id)
    group = next(g for g in context["groups"] if g["id"] == page_id)
    original_page = copy.deepcopy(page)
    explanation.validate({"pages": [page]}, {**context, "groups": [group]})
    alignment = json.loads((source / "artifacts/alignment.json").read_text())
    selected = [r for r in alignment["segments"] if r["id"] in group["segment_ids"]]
    source_audio = source / "fixtures/assets/communications_noise.wav"
    samples, rate = sf.read(source_audio, dtype="int16")
    before_sha = hashlib.sha256(source_audio.read_bytes()).hexdigest()
    first_frame = max(0, math.floor((selected[0]["start_ms"] - 100) * .03))
    last_frame = math.ceil((selected[-1]["end_ms"] + 100) * .03)
    per_frame = rate // 30
    pcm = samples[first_frame * per_frame:last_frame * per_frame]
    frames = math.ceil(len(pcm) / per_frame)
    pcm = np.pad(pcm, (0, frames * per_frame - len(pcm)))
    rows = [{"id": r["id"], "text": r["text"],
             "start_ms": round(r["start_ms"] - first_frame * 1000 / 30),
             "end_ms": round(r["end_ms"] - first_frame * 1000 / 30)} for r in selected]
    for field, path in {"ARTIFACTS": out / "artifacts", "LOGS": out / "logs",
                        "FIXTURE_ROOT": out / "fixtures", "ASSETS": out / "fixtures/assets"}.items():
        path.mkdir(parents=True, exist_ok=True)
        setattr(pipeline, field, path)
    pipeline.COMMANDS_PATH = pipeline.LOGS / "commands.jsonl"
    pipeline.IR_PATH = pipeline.FIXTURE_ROOT / "video-ir-v0.2.json"
    pipeline.VIDEO_PATH = out / "video.mp4"
    pipeline.PROJECT_INPUT = out / "source.txt"
    pipeline.PROJECT_INPUT.write_text("".join(r["text"] for r in rows))
    pipeline.PACKAGING_INPUT = {}  # Render only the affected content, no distribution or package.
    pipeline.CONTENT_MODE = False
    pipeline.OUTPUT_SPEED = json.loads((source.parents[2] / "input/input.json").read_text())["video_speed"]
    audio = pipeline.ASSETS / "communications_noise.wav"
    sf.write(audio, pcm, rate, subtype="PCM_16")
    saved_alignment = {"rows": rows, "duration_ms": round(frames * 1000 / 30),
                       "duration_seconds": frames / 30, "total_frames": frames, "audio_path": audio}
    narration = {"title": context["title"], "segments": [{"id": r["id"], "text": r["text"]} for r in rows]}
    board = explanation.compile_storyboard(pipeline, {"pages": [page], "groups": [group]}, saved_alignment, {}, [])
    compiled = pipeline.validate_compile_stage(narration, board, saved_alignment)
    pipeline.render(compiled, renderer_root=out / "renderer")
    decode = subprocess.run([str(pipeline.FFMPEG), "-nostdin", "-v", "error", "-xerror", "-i",
                             str(pipeline.VIDEO_PATH), "-f", "null", "-"], capture_output=True, text=True)
    assert decode.returncode == 0 and not decode.stderr, decode.stderr
    ir = json.loads(pipeline.IR_PATH.read_text())
    assert [r["text"] for r in ir["narration"]["segments"]] == [r["text"] for r in selected]
    assert hashlib.sha256(source_audio.read_bytes()).hexdigest() == before_sha
    duration = compiled["total_frames"] / 30
    for index, at in enumerate((duration * .2, duration * .55, max(0, duration - .6))):
        subprocess.run([str(pipeline.FFMPEG), "-nostdin", "-v", "error", "-ss", str(at), "-i",
                        str(pipeline.VIDEO_PATH), "-frames:v", "1", "-update", "1", str(out / f"frame-{index}.png")], check=True)
    save(out / "receipt.json", {"source_job": job, "source_response": str(response), "page_id": page_id,
        "original_page": original_page, "normalized_page": page,
        "original_pcm_sha256": before_sha, "original_audio_unchanged": True,
        "source_frame_interval": [first_frame, last_frame], "voice_speed": pipeline.OUTPUT_SPEED,
        "duration_seconds": duration, "number_layout": compiled["scenes"][0].get("price_layout"),
        "new_model_calls": 0, "new_tts_calls": 0, "original_text_unchanged": True,
        "decode_exit_code": decode.returncode, "decode_errors": decode.stderr})
    print(json.dumps({"case": name, "video": str(pipeline.VIDEO_PATH), "seconds": duration}), flush=True)


def refresh_case(name: str, previous: Path, out: Path):
    """Recompile and render with the already aligned original PCM and captions."""
    from g2 import explanation, information_motion, run_g2 as pipeline
    from g1.video_ir import validate_video_ir
    from g2.caption_alignment import verify_plan

    out.mkdir(parents=True, exist_ok=False)
    receipt = json.loads((previous / "receipt.json").read_text())
    original = json.loads((previous / "fixtures/video-ir-v0.2.json").read_text())
    plan, context = load_response(Path(receipt["source_response"]))
    page = next(p for p in plan["pages"] if p["id"] == receipt["page_id"])
    group = next(g for g in context["groups"] if g["id"] == receipt["page_id"])
    explanation.validate({"pages": [page]}, {**context, "groups": [group]})
    shutil.copytree(previous / "fixtures/assets", out / "fixtures/assets")
    for field, path in {"ARTIFACTS": out / "artifacts", "LOGS": out / "logs",
                        "FIXTURE_ROOT": out / "fixtures", "ASSETS": out / "fixtures/assets"}.items():
        path.mkdir(parents=True, exist_ok=True)
        setattr(pipeline, field, path)
    pipeline.COMMANDS_PATH = pipeline.LOGS / "commands.jsonl"
    pipeline.IR_PATH = pipeline.FIXTURE_ROOT / "video-ir-v0.2.json"
    pipeline.VIDEO_PATH = out / "video.mp4"
    pipeline.PROJECT_INPUT = out / "source.txt"
    shutil.copyfile(previous / "source.txt", pipeline.PROJECT_INPUT)
    pipeline.PACKAGING_INPUT, pipeline.CONTENT_MODE = {}, False
    rows = original["narration"]["segments"]
    narration = {"title": context["title"], "segments": [{"id": r["id"], "text": r["text"]} for r in rows]}
    alignment = {"rows": rows, "duration_ms": original["video"]["duration_ms"],
                 "audio_path": pipeline.ASSETS / "communications_noise.wav"}
    board = explanation.compile_storyboard(pipeline, {"pages": [page], "groups": [group]}, alignment, {}, [])
    ir = pipeline.compile_ir(narration, board, alignment)
    if "playback" in original:
        ir["playback"] = original["playback"]
    for file in ("caption-alignment.json", "captions.srt"):
        shutil.copyfile(previous / "artifacts" / file, pipeline.ARTIFACTS / file)
    captions = json.loads((pipeline.ARTIFACTS / "caption-alignment.json").read_text())
    verify_plan(ir, captions)
    changes = information_motion.align_visual_beats(ir, captions,
        json.loads((pipeline.ARTIFACTS / "information-timing.json").read_text()))
    save(pipeline.ARTIFACTS / "visual-cue-alignment.json", changes)
    save(pipeline.IR_PATH, ir)
    compiled = validate_video_ir(ir, pipeline.FIXTURE_ROOT)
    save(pipeline.ARTIFACTS / "compiled-timeline.json", compiled)
    pipeline.render(compiled, renderer_root=out / "renderer")
    qa = pipeline.verify_media(compiled, alignment)
    qa.update(real_llm=False, real_tts=False, reused_real_model_output=True, reused_original_voice=True,
              new_model_calls=0, new_tts_calls=0, new_aligner_calls=0)
    save(pipeline.ARTIFACTS / "qa.json", qa)
    assert ir["narration"] == original["narration"]
    assert pipeline.digest(pipeline.ASSETS / "communications_noise.wav") == pipeline.digest(previous / "fixtures/assets/communications_noise.wav")
    duration = compiled["total_frames"] / 30
    for index, at in enumerate((duration * .2, duration * .55, max(0, duration - .6))):
        subprocess.run([str(pipeline.FFMPEG), "-nostdin", "-v", "error", "-ss", str(at), "-i",
                        str(pipeline.VIDEO_PATH), "-frames:v", "1", "-update", "1", str(out / f"frame-{index}.png")], check=True)
    save(out / "receipt.json", {**receipt, "duration_seconds": duration, "previous_replay": str(previous),
        "rendered_from_current_compiler": True, "number_layout": compiled["scenes"][0].get("price_layout"),
        "new_aligner_calls": 0, "media_qa": qa["status"], "audio": qa["audio"], "captions": qa["captions"]})
    print(json.dumps({"case": name, "video": str(pipeline.VIDEO_PATH), "seconds": duration}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--case", choices=CASES)
    parser.add_argument("--refresh-from", type=Path)
    args = parser.parse_args()
    if args.case and args.refresh_from:
        refresh_case(args.case, args.refresh_from.resolve(), args.output.resolve())
    elif args.case:
        render_case(args.case, args.output.resolve())
    else:
        replay(args.output.resolve())

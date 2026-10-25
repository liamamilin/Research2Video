"""Independent mechanical, layout-contract, and Golden Case checks for G4."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
import subprocess
import sys
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from g1.video_ir import SUBTITLE_FONT_PATH, validate_video_ir


PROJECT = Path(__file__).resolve().parents[1]
RUNS = PROJECT / "g4/runs"
FFMPEG = PROJECT / "g0/darwin_arm64/ffmpeg"
FFPROBE = PROJECT / "g0/darwin_arm64/ffprobe"
CASE_IDS = {"case-a", "case-b", "case-c"}
RUN_DIR_NAMES = {"case-a": "case-a-retry", "case-b": "case-b", "case-c": "case-c-retry"}


class G4QCError(RuntimeError):
    pass


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise G4QCError(f"missing or invalid evidence: {path}") from exc
    if not isinstance(value, dict):
        raise G4QCError(f"evidence must be a JSON object: {path}")
    return value


def _fixed_command(label: str, args: list[str | Path], log_dir: Path, timeout: int = 300) -> str:
    executable = Path(args[0]).resolve()
    if executable not in {FFMPEG.resolve(), FFPROBE.resolve()}:
        raise G4QCError(f"refusing to run non-allowlisted media tool: {executable}")
    result = subprocess.run(list(map(str, args)), cwd=PROJECT, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, timeout=timeout,
                            check=False, shell=False)
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{label}.log").write_text(result.stdout or "", encoding="utf-8")
    if result.returncode:
        raise G4QCError(f"{label} exited {result.returncode}; see {log_dir / (label + '.log')}")
    return result.stdout or ""


def _check_budget(cost: dict[str, Any]) -> dict[str, Any]:
    calls = cost.get("calls")
    if not isinstance(calls, list) or not 2 <= len(calls) <= 7:
        raise G4QCError("each real Golden Case must use 2–7 recorded model calls")
    if cost.get("max_calls_per_run") != 7:
        raise G4QCError("per-run request cap is not the documented limit of seven")
    actual = float(cost.get("peak_uncached_cost_upper_bound_usd", -1))
    cap = float(cost.get("configured_worst_case_budget_usd", 0))
    if actual < 0 or cap <= 0 or actual > cap:
        raise G4QCError("recorded model cost exceeds its configured upper-bound budget")
    return {"status": "PASS", "calls": len(calls), "upper_bound_usd": actual, "cap_usd": cap}


def check_case_semantics(case_id: str, source: str, storyboard: dict, ir: dict,
                         qa: dict, captions: str) -> dict[str, Any]:
    scenes = storyboard.get("scenes", [])
    components = [scene.get("component") for scene in scenes]
    ir_scenes = ir.get("scenes", [])
    result: dict[str, Any] = {}
    if case_id == "case-a":
        flow = next((s for s in scenes if s.get("component") == "flow_diagram"), None)
        if not flow:
            raise G4QCError("Case A is missing its flow_diagram")
        labels = " ".join(n.get("label", "") for n in flow.get("props", {}).get("nodes", []))
        actions = {beat.get("action") for beat in flow.get("beats", [])}
        if not all(token in labels for token in ("发送", "信道", "接收")):
            raise G4QCError("Case A flow does not identify sender, channel, and receiver")
        if not {"activate_path", "mark_disrupted"}.issubset(actions):
            raise G4QCError("Case A is missing a transmission or interference state change")
        changed = qa.get("visual_beats", {})
        for action in ("activate_path", "mark_disrupted"):
            candidates = [v for v in changed.values() if v.get("action") == action]
            if not candidates or any(float(v.get("changed_pixel_ratio", 0)) < 0.002 for v in candidates):
                raise G4QCError(f"Case A {action} does not have observable before/after frame evidence")
        result["communication_state_changes"] = "PASS"
    elif case_id == "case-b":
        comparisons = [s for s in scenes if s.get("component") == "comparison"]
        if not comparisons:
            raise G4QCError("Case B must render a registered comparison component")
        props = comparisons[0].get("props", {})
        left, right = props.get("left", {}), props.get("right", {})
        left_text = " ".join([left.get("label", ""), *left.get("items", [])])
        right_text = " ".join([right.get("label", ""), *right.get("items", [])])
        combined = f"{left_text} {right_text}"
        if not all(token in combined for token in ("批处理", "流处理")):
            raise G4QCError("Case B comparison must name both batch and stream processing")
        if not left.get("items") or not right.get("items") or set(left.get("items", [])) == set(right.get("items", [])):
            raise G4QCError("Case B does not present distinct characteristics on each side")
        result["two_method_comparison"] = "PASS"
    elif case_id == "case-c":
        expected = _json(PROJECT / "g4/golden_cases/case-c/expected-chart.json")
        charts = [s for s in scenes if s.get("component") == "bar_chart"]
        if not charts:
            raise G4QCError("Case C is missing the required bar_chart")
        props = charts[0].get("props", {})
        actual = {key: props.get(key) for key in ("categories", "values", "unit", "baseline_zero")}
        wanted = {key: expected[key] for key in actual}
        if actual != wanted:
            raise G4QCError(f"Case C chart differs from its fixed source data: {actual!r}")
        if "虚构" not in source or "演示" not in source or "虚构" not in captions or "演示" not in captions:
            raise G4QCError("Case C must disclose fictional demonstration data in its spoken captions")
        result["chart_source_match"] = "PASS"
        result["fictional_data_disclosure"] = "PASS"
    else:
        raise G4QCError(f"unknown Golden Case: {case_id}")

    if len(components) != 3 or len(ir_scenes) != 3:
        raise G4QCError("Golden Case must render exactly three narrated scenes")
    return result


def _sample_frames(run_dir: Path, case_id: str, compiled: dict, out_dir: Path) -> tuple[dict[int, Path], dict]:
    video = run_dir / "artifacts/G2_自动生成.mp4"
    if not video.is_file():
        raise G4QCError(f"final video is missing: {video}")
    samples: dict[int, dict[str, Any]] = {}
    for scene in compiled["scenes"]:
        first, last = int(scene["start_frame"]), int(scene["end_frame"]) - 1
        middle = (first + last) // 2
        for label, frame in (("start", first), ("middle", middle), ("end", last)):
            samples.setdefault(frame, {"labels": []})["labels"].append(f"{scene['id']}:{label}")
    beat_rows = []
    for beat in compiled.get("beats", []):
        frame = int(beat["frame"])
        before, after = max(0, frame - 3), min(compiled["total_frames"] - 1, frame + 4)
        samples.setdefault(before, {"labels": []})["labels"].append(f"{beat['id']}:before")
        samples.setdefault(after, {"labels": []})["labels"].append(f"{beat['id']}:after")
        beat_rows.append({"id": beat["id"], "action": beat["action"], "frame": frame,
                          "before_frame": before, "after_frame": after})
    frames = sorted(samples)
    expression = "+".join(f"eq(n\\,{frame})" for frame in frames)
    out_dir.mkdir(parents=True, exist_ok=True)
    _fixed_command("sample-frames", [FFMPEG, "-nostdin", "-hide_banner", "-v", "error", "-y", "-i", video,
                    "-vf", f"select={expression}", "-vsync", "0", out_dir / "frame-%03d.png"],
                   out_dir / "logs", timeout=180)
    paths = sorted(out_dir.glob("frame-*.png"))
    if len(paths) != len(frames):
        raise G4QCError(f"frame extraction returned {len(paths)} images for {len(frames)} requested frames")
    frame_map = dict(zip(frames, paths))
    for frame in frames:
        metadata = samples[frame]
        metadata["frame"] = frame
        metadata["labels"].sort()
        with Image.open(frame_map[frame]) as image:
            if image.size != (1920, 1080):
                raise G4QCError(f"sampled frame is {image.size}, expected 1920x1080")
            # Any strong signal within the outer 2% is a non-blocking clipping/layout risk flag.
            rgb = image.convert("RGB")
            width, height = rgb.size
            border = Image.new("L", (width, height), 0)
            draw = ImageDraw.Draw(border)
            mx, my = round(width * 0.02), round(height * 0.02)
            draw.rectangle((0, 0, width - 1, my), fill=255)
            draw.rectangle((0, height - my, width - 1, height - 1), fill=255)
            draw.rectangle((0, 0, mx, height - 1), fill=255)
            draw.rectangle((width - mx, 0, width - 1, height - 1), fill=255)
            import numpy as np
            px = np.asarray(rgb, dtype=np.uint8)
            edge = np.asarray(border, dtype=np.uint8) > 0
            signal = np.max(px, axis=2) > 80
            metadata["outer_2pct_high_signal_ratio"] = float(np.mean(signal[edge]))

    beat_changes = {}
    for beat in beat_rows:
        before = np.asarray(Image.open(frame_map[beat["before_frame"]]).convert("RGB"), dtype=np.int16)
        after = np.asarray(Image.open(frame_map[beat["after_frame"]]).convert("RGB"), dtype=np.int16)
        ratio = float(np.mean(np.max(np.abs(after - before), axis=2) > 25))
        beat_changes[beat["id"]] = {**beat, "changed_pixel_ratio": ratio}
    _write_contact_sheet(case_id, samples, frame_map, out_dir / "contact-sheet.jpg")
    return frame_map, {"frame_count": len(frame_map), "samples": list(samples.values()),
                       "beat_before_after": beat_changes,
                       "layout_edge_signal_is_nonblocking": True}


def _write_contact_sheet(case_id: str, samples: dict[int, dict], frame_map: dict[int, Path], target: Path) -> None:
    width, height, label_h = 640, 360, 30
    ordered = list(samples.items())
    rows = (len(ordered) + 2) // 3
    sheet = Image.new("RGB", (width * 3, (height + label_h) * rows), "#101827")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype(SUBTITLE_FONT_PATH, 18)
    for index, (frame, metadata) in enumerate(ordered):
        image = Image.open(frame_map[frame]).convert("RGB")
        image.thumbnail((width, height))
        x, y = (index % 3) * width, (index // 3) * (height + label_h)
        draw.text((x + 8, y + 3), f"f{frame} · {', '.join(metadata['labels'])[:54]}",
                  font=font, fill="#F3F6FC")
        sheet.paste(image, (x, y + label_h))
    sheet.save(target, quality=90)


def audit_case(case_id: str) -> dict[str, Any]:
    if case_id not in CASE_IDS:
        raise G4QCError(f"case id must be one of {sorted(CASE_IDS)}")
    run_dir = RUNS / RUN_DIR_NAMES[case_id]
    artifacts, fixture_dir = run_dir / "artifacts", run_dir / "fixtures"
    case_dir = PROJECT / "g4/golden_cases" / case_id
    source = (case_dir / "source.txt").read_text(encoding="utf-8").strip()
    required = [
        artifacts / "input.json", artifacts / "narration-plan.json", artifacts / "final-narration-plan.json",
        artifacts / "storyboard-plan.json", artifacts / "alignment.json", artifacts / "tts-metrics.json",
        artifacts / "captions.srt", artifacts / "compiled-timeline.json", artifacts / "model-cost.json",
        artifacts / "qa.json", artifacts / "G2_自动生成.mp4", fixture_dir / "video-ir-v0.2.json",
        fixture_dir / "assets/communications_noise.wav", run_dir / "logs/commands.jsonl",
    ]
    missing = [str(path.relative_to(PROJECT)) for path in required if not path.is_file()]
    if missing:
        raise G4QCError(f"source-to-MP4 evidence is incomplete: {', '.join(missing)}")
    input_record = _json(artifacts / "input.json")
    source_bytes = source.encode("utf-8")
    if input_record.get("source_text") != source or input_record.get("source_sha256") != hashlib.sha256(source_bytes).hexdigest():
        raise G4QCError("generated run input does not match the committed Golden Case source")
    for key in ("human_authored_narration", "human_authored_storyboard", "human_authored_video_ir"):
        if input_record.get(key) is not False:
            raise G4QCError(f"real-generation evidence is marked as human supplied: {key}")
    qa = _json(artifacts / "qa.json")
    if qa.get("status") != "PASS" or qa.get("real_llm") is not True or qa.get("real_tts") is not True or qa.get("mock_calls") != 0:
        raise G4QCError("G2 evidence does not establish real LLM, real TTS, and zero mock calls")
    if not 2 <= int(qa.get("llm_call_count", 0)) <= 7:
        raise G4QCError("real generation call count falls outside the per-run bound")
    cost_check = _check_budget(_json(artifacts / "model-cost.json"))
    storyboard = _json(artifacts / "storyboard-plan.json")
    ir = _json(fixture_dir / "video-ir-v0.2.json")
    captions = (artifacts / "captions.srt").read_text(encoding="utf-8")
    semantic = check_case_semantics(case_id, source, storyboard, ir, qa, captions)
    compiled = validate_video_ir(ir, fixture_dir)
    if compiled["total_frames"] != int(qa["video"]["frames"]):
        raise G4QCError("saved IR frame count does not match the encoded video")
    output = artifacts / "G2_自动生成.mp4"
    out_dir = run_dir / "g4-qc"
    out_dir.mkdir(parents=True, exist_ok=True)
    probe_text = _fixed_command("ffprobe", [FFPROBE, "-v", "error", "-count_frames", "-show_streams",
                              "-show_format", "-of", "json", output], out_dir / "logs", timeout=180)
    probe = json.loads(probe_text)
    (out_dir / "ffprobe.json").write_text(json.dumps(probe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    streams = {stream["codec_type"]: stream for stream in probe["streams"]}
    video, audio = streams.get("video", {}), streams.get("audio", {})
    if (video.get("codec_name"), video.get("width"), video.get("height"), video.get("avg_frame_rate")) != ("h264", 1920, 1080, "30/1"):
        raise G4QCError("output is not H.264 1920x1080 at 30 fps")
    if (audio.get("codec_name"), audio.get("sample_rate"), audio.get("channels")) != ("aac", "48000", 2):
        raise G4QCError("output audio is not decodable AAC 48 kHz stereo")
    frames = int(video.get("nb_read_frames", 0))
    duration = float(probe.get("format", {}).get("duration", 0))
    v_duration, a_duration = float(video.get("duration", duration)), float(audio.get("duration", duration))
    if frames != compiled["total_frames"] or not 10 <= duration <= 20 or abs(v_duration - a_duration) > 1 / 30:
        raise G4QCError("frame count, duration, or A/V stream durations do not match the validated timeline")
    _fixed_command("full-decode", [FFMPEG, "-nostdin", "-hide_banner", "-v", "error", "-xerror", "-i", output,
                    "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"], out_dir / "logs", timeout=180)
    frame_map, frame_check = _sample_frames(run_dir, case_id, compiled, out_dir / "keyframes")
    for item in frame_check["beat_before_after"].values():
        if item["action"] in {"activate_path", "mark_disrupted"} and item["changed_pixel_ratio"] < 0.002:
            raise G4QCError(f"rendered visual action is not observable: {item['id']}")
    return {"status": "PASS", "case_id": case_id,
            "source_to_mp4": {"source": str((case_dir / "source.txt").relative_to(PROJECT)),
                              "narration": "artifacts/final-narration-plan.json", "tts": "fixtures/assets/communications_noise.wav",
                              "alignment": "artifacts/alignment.json", "video_ir": "fixtures/video-ir-v0.2.json",
                              "video": "artifacts/G2_自动生成.mp4", "captions": "artifacts/captions.srt"},
            "real_generation": {"model": qa["model"], "real_llm": qa["real_llm"], "real_tts": qa["real_tts"],
                                "mock_calls": qa["mock_calls"], "call_count": qa["llm_call_count"]},
            "budget": cost_check, "semantic": semantic,
            "media": {"codec": video["codec_name"], "width": video["width"], "height": video["height"],
                      "fps": video["avg_frame_rate"], "frames": frames, "duration_seconds": duration,
                      "audio_codec": audio["codec_name"], "sample_rate": audio["sample_rate"],
                      "channels": audio["channels"], "full_decode": "PASS",
                      "tts_correlation": qa["audio"]["decoded_tts_correlation"]},
            "layout_and_visual": {"runtime_ir_validation": "PASS", **frame_check,
                                  "contact_sheet": str((out_dir / "keyframes/contact-sheet.jpg").relative_to(PROJECT))},
            "output_directory": str(run_dir.relative_to(PROJECT))}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        raise SystemExit("usage: g0/.venv/bin/python -m g4.qc case-a|case-b|case-c")
    report = audit_case(args[0])
    target = RUNS / RUN_DIR_NAMES[args[0]] / "g4-qc.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"G4 QC PASS: {args[0]} -> {target.relative_to(PROJECT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

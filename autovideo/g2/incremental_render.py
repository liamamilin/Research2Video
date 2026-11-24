"""Incremental single-scene re-render for visual revisions.

When a visual revision only edits one scene (narration, audio, timeline, and
other scenes unchanged), render only the affected frame window with Remotion
and splice it frame-accurately into the verified parent MP4 using FFmpeg.
Any signature, frame-count, or precondition mismatch falls back cleanly to a
full render.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

DISSOLVE_MAX_PAD_FRAMES = 30


def _scene_flow_entry(scene: dict) -> str:
    flow = (scene.get("props") or {}).get("scene_flow")
    return str(flow.get("entry") or "cut") if isinstance(flow, dict) else "cut"


def plan_incremental_window(
    parent_ir: dict,
    ir: dict,
    parent_compiled: dict,
    compiled: dict,
    target_scene_id: str,
) -> tuple[dict | None, str]:
    """Return ``(window_dict, "ok")`` when safe to re-render only the target scene.

    Otherwise return ``(None, reason)`` so the caller falls back to full render.
    """
    if os.environ.get("AUTOVIDEO_G2_DISABLE_INCREMENTAL_REVISION") == "1":
        return None, "disabled_by_env"
    if compiled.get("presentation", {}).get("preset") == "information_cards":
        return None, "fast_cards_executor"
    for key in ("video", "narration", "style", "presentation"):
        if ir.get(key) != parent_ir.get(key):
            return None, f"{key}_changed"
    total_frames = int(compiled.get("total_frames") or 0)
    if total_frames <= 0 or total_frames != int(parent_compiled.get("total_frames") or 0):
        return None, "total_frames_mismatch"

    parent_scenes = parent_ir.get("scenes") or []
    scenes = ir.get("scenes") or []
    if len(parent_scenes) != len(scenes) or len(scenes) < 2:
        return None, "single_or_mismatched_scene_count"

    parent_by_id = {s["id"]: s for s in parent_scenes}
    for scene in scenes:
        if scene["id"] != target_scene_id and scene != parent_by_id.get(scene["id"]):
            return None, f"other_scene_changed:{scene['id']}"

    compiled_scenes = compiled.get("scenes") or []
    parent_compiled_by_id = {s["id"]: s for s in (parent_compiled.get("scenes") or [])}
    target_idx = next((i for i, s in enumerate(compiled_scenes) if s["id"] == target_scene_id), None)
    if target_idx is None:
        return None, "target_scene_missing"
    target = compiled_scenes[target_idx]
    parent_target = parent_compiled_by_id.get(target_scene_id)
    if (
        not parent_target
        or int(target["start_frame"]) != int(parent_target["start_frame"])
        or int(target["end_frame"]) != int(parent_target["end_frame"])
    ):
        return None, "target_frame_range_changed"

    start_frame = int(target["start_frame"])
    end_frame = int(target["end_frame"])
    # Include cross-scene carry chains and trailing dissolve overlap so
    # transitions out of the revised scene never show stale parent frames.
    idx = target_idx + 1
    while idx < len(compiled_scenes):
        nxt = compiled_scenes[idx]
        nxt_entry = _scene_flow_entry(nxt)
        nxt_len = max(0, int(nxt["end_frame"]) - int(nxt["start_frame"]))
        if nxt_entry == "carry":
            end_frame = int(nxt["end_frame"])
            idx += 1
            continue
        if nxt_entry == "dissolve" and nxt_len > 0:
            pad = min(DISSOLVE_MAX_PAD_FRAMES, max(1, nxt_len // 4))
            end_frame = min(total_frames, end_frame + pad)
        break

    if start_frame < 0 or end_frame > total_frames or start_frame >= end_frame:
        return None, "invalid_frame_window"
    if end_frame - start_frame >= total_frames:
        return None, "window_covers_full_video"

    return {
        "scene_id": target_scene_id,
        "start_frame": start_frame,
        "end_frame": end_frame,
        "total_frames": total_frames,
        "rendered_frames": end_frame - start_frame,
        "reused_frames": total_frames - (end_frame - start_frame),
    }, "ok"


def _probe_parent_video(pipeline, parent_video: Path, compiled: dict) -> tuple[bool, str]:
    if not parent_video.is_file() or parent_video.stat().st_size == 0:
        return False, "parent_video_missing"
    try:
        raw = pipeline._run_fixed_command(
            "ffprobe-parent",
            [pipeline.FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", parent_video],
            pipeline.PROJECT,
        )
        info = json.loads(raw)
    except Exception:
        return False, "parent_video_probe_failed"
    video_streams = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    if not video_streams:
        return False, "parent_video_no_stream"
    stream = video_streams[0]
    exp_w = int(compiled["video"]["width"])
    exp_h = int(compiled["video"]["height"])
    exp_frames = int(compiled["total_frames"])
    if int(stream.get("width") or 0) != exp_w or int(stream.get("height") or 0) != exp_h:
        return False, "parent_video_dimensions_mismatch"
    nb_frames = stream.get("nb_frames")
    if nb_frames is not None and str(nb_frames).isdigit():
        if int(nb_frames) != exp_frames:
            return False, f"parent_video_frame_count_mismatch:{nb_frames}!={exp_frames}"
    else:
        dur = float(stream.get("duration") or info.get("format", {}).get("duration") or 0)
        if abs(round(dur * 30) - exp_frames) > 1:
            return False, "parent_video_duration_mismatch"
    return True, "ok"


def _build_splice_filter(start_frame: int, end_frame: int, total_frames: int) -> str:
    """Build frame-accurate FFmpeg filter_complex splicing ``[1:v]`` into ``[0:v]``."""
    parts: list[str] = []
    concat_inputs: list[str] = []
    norm = "scale=in_range=auto:out_range=tv:out_color_matrix=bt709,format=yuv420p"
    has_pre = start_frame > 0
    has_post = end_frame < total_frames
    if has_pre and has_post:
        parts.append("[0:v]split=2[vpre][vpost]")
        pre_src, post_src = "[vpre]", "[vpost]"
    else:
        pre_src, post_src = "[0:v]", "[0:v]"
    if has_pre:
        parts.append(f"{pre_src}trim=start_frame=0:end_frame={start_frame},setpts=PTS-STARTPTS,{norm}[pre]")
        concat_inputs.append("[pre]")
    patch_len = end_frame - start_frame
    parts.append(f"[1:v]trim=start_frame=0:end_frame={patch_len},setpts=PTS-STARTPTS,{norm}[mid]")
    concat_inputs.append("[mid]")
    if has_post:
        parts.append(f"{post_src}trim=start_frame={end_frame}:end_frame={total_frames},setpts=PTS-STARTPTS,{norm}[post]")
        concat_inputs.append("[post]")
    parts.append(f"{''.join(concat_inputs)}concat=n={len(concat_inputs)}:v=1:a=0[vout]")
    return ";".join(parts)


def try_incremental_render(
    pipeline,
    *,
    parent_dir: Path,
    parent_ir: dict,
    ir: dict,
    compiled: dict,
    target_scene_id: str,
    renderer_root: Path,
) -> dict:
    """Attempt incremental single-scene render; return receipt dict (``used: bool``).

    When preconditions fail, returns ``{"used": False, "reason": ...}`` without
    touching ``pipeline.VIDEO_PATH`` so caller can run a normal full render.
    """
    if hasattr(pipeline, "package_request_from_intent"):
        pipeline.package_request_from_intent()
    if (pipeline.PACKAGING_INPUT or {}).get("production_plan", {}).get("executor", {}).get("id") == "hybrid_cards":
        return {"used": False, "reason": "hybrid_cards_executor"}
    parent_compiled_path = parent_dir / "artifacts" / "compiled-timeline.json"
    if not parent_compiled_path.is_file():
        return {"used": False, "reason": "parent_compiled_missing"}
    try:
        parent_compiled = json.loads(parent_compiled_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"used": False, "reason": "parent_compiled_unreadable"}

    window, reason = plan_incremental_window(parent_ir, ir, parent_compiled, compiled, target_scene_id)
    if window is None:
        return {"used": False, "reason": reason}

    parent_video = parent_dir / "artifacts" / pipeline.VIDEO_FILENAME
    ok, probe_reason = _probe_parent_video(pipeline, parent_video, compiled)
    if not ok:
        return {"used": False, "reason": probe_reason}

    work_dir = pipeline.ARTIFACTS / "incremental-revision"
    work_dir.mkdir(parents=True, exist_ok=True)
    patch_mp4 = work_dir / f"scene-{target_scene_id}.mp4"

    # 1. Render only the affected frame window via Remotion.
    plan = (pipeline.PACKAGING_INPUT or {}).get("production_plan")
    if plan and hasattr(pipeline, "write_json"):
        pipeline.write_json(pipeline.ARTIFACTS / "production-execution.json", {
            "schema_version": 1,
            "production_plan_sha256": plan["sha256"],
            "executor": "remotion",
            "form": os.environ.get("AUTOVIDEO_G2_VIDEO_FORM"),
            "style": plan["style"],
            "music": plan["music"],
        })
    pipeline.render(
        compiled,
        renderer_root=renderer_root,
        hybrid_segment={
            "label": f"rev-{target_scene_id}",
            "start_frame": window["start_frame"],
            "end_frame": window["end_frame"],
            "output": patch_mp4,
        },
    )

    # 2. Splice the newly rendered scene into the parent video and mux narration.
    filter_complex = _build_splice_filter(window["start_frame"], window["end_frame"], window["total_frames"])
    audio_path = pipeline.ASSETS / "communications_noise.wav"
    encoders = [("h264_videotoolbox", ["-b:v", "5500k", "-allow_sw", "0"])] if sys.platform == "darwin" else []
    encoders.append(("libx264", ["-preset", "veryfast", "-crf", "18"]))
    failures = []
    chosen_encoder = ""
    for encoder, options in encoders:
        candidate = work_dir / f"spliced-{encoder}.mp4"
        if candidate.exists():
            candidate.unlink()
        try:
            pipeline._run_fixed_command(
                f"revision-splice-{encoder}",
                [
                    pipeline.FFMPEG, "-nostdin", "-v", "warning", "-n",
                    "-i", parent_video,
                    "-i", patch_mp4,
                    "-i", audio_path,
                    "-filter_complex", filter_complex,
                    "-map", "[vout]", "-map", "2:a:0",
                    "-r", "30",
                    "-frames:v", str(window["total_frames"]),
                    "-t", f"{window['total_frames'] / 30:.6f}",
                    "-c:v", encoder, *options,
                    "-profile:v", "high",
                    "-color_range", "tv", "-colorspace", "bt709",
                    "-color_trc", "bt709", "-color_primaries", "bt709",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                    "-movflags", "+faststart",
                    candidate,
                ],
                pipeline.PROJECT,
                timeout=max(1200, window["total_frames"] // 5),
            )
            candidate.replace(pipeline.VIDEO_PATH)
            chosen_encoder = encoder
            break
        except Exception as exc:
            failures.append({"encoder": encoder, "error": str(exc)[:300]})
    else:
        return {"used": False, "reason": "splice_encode_failed", "failures": failures}

    return {
        "used": True,
        "reason": "ok",
        "encoder": chosen_encoder,
        **window,
    }

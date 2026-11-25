"""Tests for incremental single-scene visual revision rendering."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import wave

import numpy as np
import pytest

from g2.incremental_render import plan_incremental_window, try_incremental_render

PROJECT = Path(__file__).resolve().parents[2]
FFMPEG = PROJECT / "g0/darwin_arm64/ffmpeg"
FFPROBE = PROJECT / "g0/darwin_arm64/ffprobe"


def _sample_ir_and_compiled(next_entry: str = "cut") -> tuple[dict, dict]:
    ir = {
        "video": {"title": "测试", "width": 1920, "height": 1080, "fps": 30, "duration_ms": 3000},
        "narration": {"text": "甲乙丙", "segments": [{"id": "n1", "start_ms": 0, "end_ms": 3000}]},
        "style": {"preset": "minimal_light", "seed": 1},
        "presentation": {"preset": "information_motion"},
        "scenes": [
            {"id": "s1", "component": "title_card", "props": {"title": "开场"}},
            {"id": "s2", "component": "concept_reveal", "props": {"title": "原中段"}},
            {"id": "s3", "component": "concept_reveal", "props": {"title": "收尾", "scene_flow": {"entry": next_entry}}},
        ],
    }
    compiled = {
        "video": dict(ir["video"]),
        "presentation": dict(ir["presentation"]),
        "total_frames": 90,
        "scenes": [
            {"id": "s1", "start_frame": 0, "end_frame": 30, "props": ir["scenes"][0]["props"]},
            {"id": "s2", "start_frame": 30, "end_frame": 60, "props": ir["scenes"][1]["props"]},
            {"id": "s3", "start_frame": 60, "end_frame": 90, "props": ir["scenes"][2]["props"]},
        ],
    }
    return ir, compiled


def test_plan_incremental_window_handles_cut_dissolve_carry_and_fallbacks(monkeypatch):
    parent_ir, parent_compiled = _sample_ir_and_compiled("cut")
    ir = deepcopy(parent_ir)
    ir["scenes"][1]["props"]["title"] = "改后的中段"
    compiled = deepcopy(parent_compiled)
    compiled["scenes"][1]["props"]["title"] = "改后的中段"

    win, reason = plan_incremental_window(parent_ir, ir, parent_compiled, compiled, "s2")
    assert reason == "ok" and win is not None
    assert (win["start_frame"], win["end_frame"], win["rendered_frames"], win["reused_frames"]) == (30, 60, 30, 60)

    # Dissolve into next scene adds transition padding (min(30, 30//4) = 7 frames).
    d_parent_ir, d_parent_compiled = _sample_ir_and_compiled("dissolve")
    d_ir, d_compiled = deepcopy(d_parent_ir), deepcopy(d_parent_compiled)
    d_ir["scenes"][1]["props"]["title"] = "改后"
    d_compiled["scenes"][1]["props"]["title"] = "改后"
    d_win, d_reason = plan_incremental_window(d_parent_ir, d_ir, d_parent_compiled, d_compiled, "s2")
    assert d_reason == "ok" and d_win is not None
    assert (d_win["start_frame"], d_win["end_frame"]) == (30, 67)

    # Carry into next scene extends through the carried scene.
    c_parent_ir, c_parent_compiled = _sample_ir_and_compiled("carry")
    c_ir, c_compiled = deepcopy(c_parent_ir), deepcopy(c_parent_compiled)
    c_ir["scenes"][1]["props"]["title"] = "改后"
    c_compiled["scenes"][1]["props"]["title"] = "改后"
    c_win, c_reason = plan_incremental_window(c_parent_ir, c_ir, c_parent_compiled, c_compiled, "s2")
    assert c_reason == "ok" and c_win is not None
    assert (c_win["start_frame"], c_win["end_frame"]) == (30, 90)

    # Changing a non-target scene forces fallback.
    bad_ir = deepcopy(ir)
    bad_ir["scenes"][0]["props"]["title"] = "也改了"
    assert plan_incremental_window(parent_ir, bad_ir, parent_compiled, compiled, "s2")[0] is None

    # Env kill-switch forces fallback.
    monkeypatch.setenv("AUTOVIDEO_G2_DISABLE_INCREMENTAL_REVISION", "1")
    assert plan_incremental_window(parent_ir, ir, parent_compiled, compiled, "s2") == (None, "disabled_by_env")


@pytest.mark.skipif(not (FFMPEG.is_file() and FFPROBE.is_file()), reason="bundled ffmpeg/ffprobe required")
def test_try_incremental_render_splices_real_mp4_frame_accurately(tmp_path):
    parent_dir = tmp_path / "parent"
    (parent_dir / "artifacts").mkdir(parents=True)
    run_dir = tmp_path / "child"
    artifacts = run_dir / "artifacts"
    assets = run_dir / "fixtures" / "assets"
    artifacts.mkdir(parents=True)
    assets.mkdir(parents=True)

    # Create a 3-second (90-frame) parent MP4: s1=red, s2=green, s3=blue.
    parent_video = parent_dir / "artifacts" / "G2_自动生成.mp4"
    subprocess.run(
        [
            str(FFMPEG), "-nostdin", "-v", "error", "-y",
            "-f", "lavfi", "-i", "color=c=red:s=1920x1080:r=30:d=1",
            "-f", "lavfi", "-i", "color=c=green:s=1920x1080:r=30:d=1",
            "-f", "lavfi", "-i", "color=c=blue:s=1920x1080:r=30:d=1",
            "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0,format=yuv420p[v]",
            "-map", "[v]", "-frames:v", "90",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
            str(parent_video),
        ],
        check=True,
    )

    # Create 3-second narration WAV.
    wav_path = assets / "communications_noise.wav"
    raw = (np.sin(np.arange(24000 * 3) * 2 * np.pi * 440 / 24000) * 8000).astype("<i2").tobytes()
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(raw)

    parent_ir, parent_compiled = _sample_ir_and_compiled("cut")
    (parent_dir / "artifacts" / "compiled-timeline.json").write_text(json.dumps(parent_compiled), encoding="utf-8")
    ir, compiled = deepcopy(parent_ir), deepcopy(parent_compiled)
    ir["scenes"][1]["props"]["title"] = "改后的黄色中段"
    compiled["scenes"][1]["props"]["title"] = "改后的黄色中段"

    rendered_segments = []

    def fake_render(_compiled, *, renderer_root=None, hybrid_segment=None):
        assert hybrid_segment is not None
        rendered_segments.append(hybrid_segment)
        frames = hybrid_segment["end_frame"] - hybrid_segment["start_frame"]
        subprocess.run(
            [
                str(FFMPEG), "-nostdin", "-v", "error", "-y",
                "-f", "lavfi", "-i", f"color=c=yellow:s=1920x1080:r=30:d={frames / 30:.6f}",
                "-frames:v", str(frames),
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p",
                str(hybrid_segment["output"]),
            ],
            check=True,
        )

    def run_cmd(_label, argv, cwd, timeout=None):
        return subprocess.check_output([str(a) for a in argv], cwd=cwd, text=True, timeout=timeout)

    out_video = artifacts / "G2_自动生成.mp4"
    pipeline = SimpleNamespace(
        PROJECT=PROJECT,
        FFMPEG=FFMPEG,
        FFPROBE=FFPROBE,
        ARTIFACTS=artifacts,
        ASSETS=assets,
        VIDEO_FILENAME="G2_自动生成.mp4",
        VIDEO_PATH=out_video,
        PACKAGING_INPUT={},
        render=fake_render,
        _run_fixed_command=run_cmd,
    )

    receipt = try_incremental_render(
        pipeline,
        parent_dir=parent_dir,
        parent_ir=parent_ir,
        ir=ir,
        compiled=compiled,
        target_scene_id="s2",
        renderer_root=run_dir / "renderer",
    )
    assert receipt["used"] is True
    assert receipt["rendered_frames"] == 30 and receipt["reused_frames"] == 60
    assert len(rendered_segments) == 1 and (rendered_segments[0]["start_frame"], rendered_segments[0]["end_frame"]) == (30, 60)
    assert out_video.is_file()

    # Full decode check + ffprobe verification.
    subprocess.run([str(FFMPEG), "-v", "error", "-i", str(out_video), "-f", "null", "-"], check=True)
    probe = json.loads(subprocess.check_output(
        [str(FFPROBE), "-v", "error", "-count_frames", "-show_streams", "-of", "json", str(out_video)],
        text=True,
    ))
    vstream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    astream = next(s for s in probe["streams"] if s["codec_type"] == "audio")
    assert int(vstream["nb_read_frames"]) == 90
    assert (int(vstream["width"]), int(vstream["height"])) == (1920, 1080)
    assert astream["codec_name"] == "aac"

    # Sample center pixel at frame 15 (red), 45 (yellow), 75 (blue) to confirm exact splice boundaries.
    def sample_rgb(sec: float) -> tuple[int, int, int]:
        raw_rgb = subprocess.check_output([
            str(FFMPEG), "-v", "error", "-ss", f"{sec:.3f}", "-i", str(out_video),
            "-frames:v", "1", "-vf", "crop=2:2:960:540", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ])
        return raw_rgb[0], raw_rgb[1], raw_rgb[2]

    r1, g1, b1 = sample_rgb(0.5)   # s1: red
    r2, g2, b2 = sample_rgb(1.5)   # s2: yellow (replaced!)
    r3, g3, b3 = sample_rgb(2.5)   # s3: blue
    assert r1 > 200 and g1 < 50 and b1 < 50
    assert r2 > 200 and g2 > 200 and b2 < 50
    assert r3 < 50 and g3 < 50 and b3 > 200

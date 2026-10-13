"""Render the H1 image-motion variant from frozen M0 IR and audio."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from asset_adapter import resolve_scene_backdrops, sha256_file


PROJECT = Path(__file__).resolve().parents[1]
H1 = PROJECT / "h1"
EXPERIMENT_PATH = H1 / "experiment.json"
ASSET_MANIFEST = H1 / "asset-manifest.json"
RENDERER = H1 / "renderer"
PUBLIC = RENDERER / "public"
NODE = Path(shutil.which("node") or "")
CLI = PROJECT / "g0/vendor/remotion-audiogram/node_modules/@remotion/cli/remotion-cli.js"
CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
FFPROBE = PROJECT / "g0/darwin_arm64/ffprobe"
FFMPEG = PROJECT / "g0/darwin_arm64/ffmpeg"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_case(case_id: str, case_source: str, pairs: list[dict]) -> dict:
    source_root = PROJECT / case_source
    source_props = source_root / "artifacts/render-props.json"
    source_video = source_root / "artifacts/G2_自动生成.mp4"
    source_audio = source_root / "fixtures/assets/communications_noise.wav"
    if not all(path.is_file() for path in (source_props, source_video, source_audio)):
        raise RuntimeError(f"missing frozen M0 source files for {case_id}")

    source_props_data = json.loads(source_props.read_text(encoding="utf-8"))
    ir = source_props_data["ir"]
    selection = {pair["scene_id"]: pair["asset_id"] for pair in pairs}
    cache = H1 / "cache/sha256"
    public_assets = PUBLIC / "assets"
    scene_backdrops = resolve_scene_backdrops(
        ir, selection, ASSET_MANIFEST, PROJECT, cache, public_assets
    )

    audio_name = f"{case_id}-voice.wav"
    public_audio = public_assets / audio_name
    audio_sha = sha256_file(source_audio)
    if public_audio.exists() and sha256_file(public_audio) != audio_sha:
        raise RuntimeError(f"renderer audio path already contains different bytes: {audio_name}")
    if not public_audio.exists():
        shutil.copy2(source_audio, public_audio)

    ir["audio_public_file"] = f"assets/{audio_name}"
    ir["scene_backdrops"] = scene_backdrops
    run_root = H1 / "runs" / case_id
    run_root.mkdir(parents=True, exist_ok=True)
    props_path = run_root / "render-props.json"
    output = run_root / "C_image_motion_v3_full.mp4"
    log_path = run_root / "render-v3.log"
    metadata_path = run_root / "render-metadata-v3.json"
    if output.exists():
        raise RuntimeError(f"refusing to overwrite existing H1 output: {output}")
    write_json(props_path, {"ir": ir})

    argv = [
        str(NODE), str(CLI), "render", "src/index.ts", "H1ImageMotion", str(output),
        f"--props={props_path}", f"--public-dir={PUBLIC}",
        "--width=1920", "--height=1080", "--fps=30",
        f"--duration={ir['total_frames']}", "--codec=h264", "--audio-codec=aac",
        "--pixel-format=yuv420p", "--concurrency=2", "--crf=18",
        f"--browser-executable={CHROME}", "--overwrite=false",
    ]
    command_record = {
        "case_id": case_id,
        "variant": "C",
        "command": argv,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "source_video": str(source_video.relative_to(PROJECT)),
        "source_video_sha256": sha256_file(source_video),
        "source_props_sha256": sha256_file(source_props),
        "source_audio_sha256": audio_sha,
        "scene_backdrops": scene_backdrops,
    }
    started = time.monotonic()
    result = subprocess.run(argv, cwd=RENDERER, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=1200)
    elapsed = time.monotonic() - started
    log_path.write_text(result.stdout, encoding="utf-8")
    command_record.update({"exit_code": result.returncode, "elapsed_seconds": round(elapsed, 3),
                           "log_path": str(log_path.relative_to(PROJECT))})
    write_json(run_root / "render-command-v3.json", command_record)
    if result.returncode != 0 or not output.is_file():
        raise RuntimeError(f"H1 renderer failed for {case_id}; see {log_path}")

    probe = subprocess.run(
        [str(FFPROBE), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)],
        check=True, capture_output=True, text=True,
    )
    probe_json = json.loads(probe.stdout)
    write_json(run_root / "ffprobe.json", probe_json)
    streams = probe_json.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    if not video or not audio or video.get("width") != 1920 or video.get("height") != 1080:
        raise RuntimeError(f"H1 output has invalid video/audio streams for {case_id}")
    full_decode = subprocess.run([str(FFMPEG), "-v", "error", "-i", str(output), "-f", "null", "-"],
                                 capture_output=True, text=True)
    if full_decode.returncode != 0:
        raise RuntimeError(f"H1 output failed full decode for {case_id}: {full_decode.stderr[-800:]}")

    metadata = {
        **command_record,
        "output_path": str(output.relative_to(PROJECT)),
        "output_sha256": sha256_file(output),
        "output_size_bytes": output.stat().st_size,
        "video_codec": video.get("codec_name"),
        "video_width": video.get("width"),
        "video_height": video.get("height"),
        "video_fps": video.get("r_frame_rate"),
        "video_frames": int(video["nb_frames"]) if video.get("nb_frames", "N/A").isdigit() else None,
        "audio_codec": audio.get("codec_name"),
        "audio_sample_rate_hz": int(audio["sample_rate"]) if audio.get("sample_rate", "").isdigit() else None,
        "audio_channels": audio.get("channels"),
        "duration_seconds": float(probe_json["format"]["duration"]),
        "full_decode": "PASS",
    }
    write_json(metadata_path, metadata)
    return metadata


def main() -> int:
    if not NODE.is_file() or not CLI.is_file() or not CHROME.is_file():
        raise RuntimeError("Node, Remotion CLI, or Chrome dependency is unavailable")
    node_modules = RENDERER / "node_modules"
    if not node_modules.exists():
        node_modules.symlink_to(PROJECT / "g0/vendor/remotion-audiogram/node_modules", target_is_directory=True)

    experiment = json.loads(EXPERIMENT_PATH.read_text(encoding="utf-8"))
    cases = experiment["case_sources"]
    results = []
    for case_id, case_source in cases.items():
        pairs = [pair for pair in experiment["pairs"] if pair["case_id"] == case_id]
        results.append(run_case(case_id, case_source, pairs))
    write_json(H1 / "runs/C_RENDER_SUMMARY_v3.json", {"variant": "C", "cases": results})
    print(json.dumps({"variant": "C", "renders": len(results),
                      "outputs": [item["output_path"] for item in results],
                      "elapsed_seconds": [item["elapsed_seconds"] for item in results]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"H1_RENDER_FAILED: {exc}", file=sys.stderr)
        raise

"""Read-only media acceptance for the meeting topic; run after its render completes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

FOLDER = Path(__file__).resolve().parent
ROOT = FOLDER.parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from core.video import store  # noqa: E402
from web.services import video_render  # noqa: E402

RENDER_ID = "4db7013f7f77474fa640e525284c3523"
BIN = ROOT.parent.parent / "autovideo/g0/darwin_arm64"


def command(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, text=True, capture_output=True, timeout=900)


def seconds(value: str) -> float:
    h, m, s = value.replace(",", ".").split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def main() -> None:
    render = store.get_render(RENDER_ID)
    if not render or render["status"] != "COMPLETED":
        raise RuntimeError("Wait for the existing render to complete; do not create another render.")
    video = video_render.safe_file(render, "video.mp4")
    if video is None:
        raise FileNotFoundError("Completed render has no downloaded video.mp4")
    video = video.resolve()
    probe = command([str(BIN / "ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)])
    probe.check_returncode()
    info = json.loads(probe.stdout)
    (FOLDER / "ffprobe.json").write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n")
    decode_args = [str(BIN / "ffmpeg"), "-nostdin", "-v", "error", "-xerror", "-i", str(video),
                   "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"]
    decode = command(decode_args)
    (FOLDER / "full-decode.log").write_text(f"exit_code={decode.returncode}\n" + decode.stderr)
    decode.check_returncode()

    duration = float(info["format"]["duration"])
    ir_file = video_render.safe_file(render, "video-ir.json")
    ir = json.loads(ir_file.read_text()) if ir_file else {}
    brand_file = video_render.safe_file(render, "brand-qa.json")
    brand = json.loads(brand_file.read_text()) if brand_file else {}
    shift = float(brand.get("narration_shift_seconds") or 0)
    captions = video_render.safe_file(render, "captions.srt")
    cues = []
    if captions:
        for block in re.split(r"\n\s*\n", captions.read_text().strip()):
            match = re.search(r"(\d+:\d+:\d+[,.]\d+) --> (\d+:\d+:\d+[,.]\d+)\n([\s\S]*)", block)
            if match:
                cues.append({"start": seconds(match[1]), "end": seconds(match[2]), "text": match[3].replace("\n", " ")})
    samples = []
    scene_map = {s["id"]: s for s in ir.get("scenes", [])}
    scene_ids = ["card_2", "card_3", "card_11_p1", "card_13_p2", "card_14", "card_15", "card_16", "card_21", "card_25"]
    for index, fraction in enumerate((0.06, 0.15, 0.26, 0.38, 0.49, 0.60, 0.72, 0.84, 0.94), 1):
        scene = scene_map.get(scene_ids[index - 1])
        timestamp = round((scene["start_ms"] + 0.85 * scene["duration_ms"]) / 1000 + shift, 2) if scene else round(duration * fraction, 2)
        path = FOLDER / f"frame-{index:02d}.jpg"
        result = command([str(BIN / "ffmpeg"), "-nostdin", "-v", "error", "-y", "-ss", str(timestamp),
                          "-i", str(video), "-frames:v", "1", "-vf", "scale=960:-2", str(path)])
        result.check_returncode()
        samples.append({"frame": path.name, "seconds": timestamp,
                        "scene_id": scene["id"] if scene else None, "purpose": scene.get("purpose") if scene else None,
                        "caption": " ".join(c["text"] for c in cues if c["start"] <= timestamp <= c["end"]),
                        "nearby_captions": [c for c in cues if c["end"] >= timestamp - 3 and c["start"] <= timestamp + 3]})
    from PIL import Image, ImageDraw
    sheet = Image.new("RGB", (1440, 3 * 294), "#17191f")
    draw = ImageDraw.Draw(sheet)
    for i, sample in enumerate(samples):
        with Image.open(FOLDER / sample["frame"]) as source:
            sheet.paste(source.resize((480, 270)), ((i % 3) * 480, (i // 3) * 294))
        draw.text(((i % 3) * 480 + 8, (i // 3) * 294 + 276), f'{sample["frame"]} | {sample["seconds"]:.2f}s', fill="white")
    sheet.save(FOLDER / "contact-sheet.jpg", quality=93)
    evidence = {"render_id": RENDER_ID, "video": str(video), "duration_seconds": duration,
                "full_decode_exit_code": decode.returncode, "playback": ir.get("playback"),
                "brand_narration_shift_seconds": shift, "streams": [
                    {k: stream[k] for k in ("codec_type", "codec_name", "width", "height", "r_frame_rate", "sample_rate", "channels") if k in stream}
                    for stream in info["streams"]], "caption_cues": len(cues), "samples": samples}
    (FOLDER / "media-check.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: evidence[k] for k in ("video", "duration_seconds", "full_decode_exit_code", "streams", "caption_cues")}, ensure_ascii=False))


if __name__ == "__main__":
    main()

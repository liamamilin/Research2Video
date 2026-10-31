"""Inspect the completed industry theme render without creating another job."""
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

BIN = ROOT.parent.parent / "autovideo/g0/darwin_arm64"


def command(args: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(args, text=True, capture_output=True, timeout=900)
    result.check_returncode()
    return result


def seconds(value: str) -> float:
    h, m, s = value.replace(",", ".").split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def main() -> None:
    run = json.loads((FOLDER / "run.json").read_text())
    script = store.get_script(run["script_id"])
    renders = store.list_renders(run["script_id"])
    render = next((r for r in renders if r["status"] == "COMPLETED"), None)
    if not render:
        raise RuntimeError("Wait for this theme's existing render to complete.")
    video = video_render.safe_file(render, "video.mp4")
    if video is None:
        raise FileNotFoundError("No downloaded video.mp4 in the completed render.")
    video = video.resolve()
    probe = command([str(BIN / "ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)])
    info = json.loads(probe.stdout)
    (FOLDER / "ffprobe.json").write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n")
    decode = subprocess.run([str(BIN / "ffmpeg"), "-nostdin", "-v", "error", "-xerror", "-i", str(video),
                             "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"],
                            text=True, capture_output=True, timeout=900)
    (FOLDER / "full-decode.log").write_text(f"exit_code={decode.returncode}\n" + decode.stderr)
    decode.check_returncode()
    duration = float(info["format"]["duration"])
    cues = []
    captions = video_render.safe_file(render, "captions.srt")
    if captions:
        for block in re.split(r"\n\s*\n", captions.read_text().strip()):
            match = re.search(r"(\d+:\d+:\d+[,.]\d+) --> (\d+:\d+:\d+[,.]\d+)\n([\s\S]*)", block)
            if match:
                cues.append({"start": seconds(match[1]), "end": seconds(match[2]), "text": match[3].replace("\n", " ")})
    samples = []
    for i, fraction in enumerate((0.03, 0.15, 0.27, 0.39, 0.51, 0.63, 0.75, 0.87, 0.97), 1):
        timestamp = round(duration * fraction, 2)
        path = FOLDER / f"frame-{i:02d}.jpg"
        command([str(BIN / "ffmpeg"), "-nostdin", "-v", "error", "-y", "-ss", str(timestamp), "-i", str(video),
                 "-frames:v", "1", "-vf", "scale=1280:-2", str(path)])
        samples.append({"frame": path.name, "seconds": timestamp,
                        "caption": " ".join(c["text"] for c in cues if c["start"] <= timestamp <= c["end"]),
                        "nearby_captions": [c for c in cues if c["end"] >= timestamp - 3 and c["start"] <= timestamp + 3]})
    from PIL import Image, ImageDraw
    sheet = Image.new("RGB", (1440, 3 * 294), "#17191f")
    draw = ImageDraw.Draw(sheet)
    for i, sample in enumerate(samples):
        with Image.open(FOLDER / sample["frame"]) as source:
            sheet.paste(source.resize((480, 270)), ((i % 3) * 480, (i // 3) * 294))
        draw.text(((i % 3) * 480 + 8, (i // 3) * 294 + 276), f'{sample["frame"]} | {sample["seconds"]:.2f}s', fill="white")
    sheet.save(FOLDER / "contact-sheet.jpg", quality=94)
    evidence = {"theme_id": run["theme_id"], "script_id": run["script_id"], "render_id": render["id"],
                "video": str(video), "duration_seconds": duration, "full_decode_exit_code": decode.returncode,
                "streams": [{k: s[k] for k in ("codec_type", "codec_name", "width", "height", "r_frame_rate", "sample_rate", "channels") if k in s}
                            for s in info["streams"]], "caption_cues": len(cues), "samples": samples}
    (FOLDER / "media-check.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    (FOLDER / "script.json").write_text(json.dumps(store.get_version(script["id"], render["version"]), ensure_ascii=False, indent=2) + "\n")
    run.update(render_id=render["id"], report_path=script["report_path"], video=str(video), duration_seconds=duration)
    (FOLDER / "run.json").write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: evidence[k] for k in ("render_id", "video", "duration_seconds", "full_decode_exit_code", "streams", "caption_cues")}, ensure_ascii=False))


if __name__ == "__main__":
    main()

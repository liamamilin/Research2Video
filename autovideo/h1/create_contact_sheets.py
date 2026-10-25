"""Create internal and blinded representative-frame sheets for H1 clips."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


PROJECT = Path(__file__).resolve().parents[1]
H1 = PROJECT / "h1"
FFMPEG = PROJECT / "g0/darwin_arm64/ffmpeg"
WIDTH, HEIGHT = 640, 360
LEFT, TOP, GAP, LABEL = 24, 24, 18, 46
FONT = ImageFont.load_default()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def frame_at(video: Path, output: Path, seconds: float) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    argv = [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-ss", f"{seconds:.4f}", "-i", str(video),
            "-frames:v", "1", "-vf", f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black",
            "-y", str(output)]
    result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode != 0 or not output.is_file():
        raise RuntimeError(f"thumbnail extraction failed for {video}: {result.stderr[-800:]}")


def sheet(rows: list[tuple[str, Path, str, Path, str]], output: Path) -> None:
    width = LEFT * 2 + WIDTH * 2 + GAP
    row_height = HEIGHT + LABEL
    height = TOP * 2 + len(rows) * row_height + max(0, len(rows) - 1) * GAP
    canvas = Image.new("RGB", (width, height), "#0b1020")
    draw = ImageDraw.Draw(canvas)
    for index, (pair_label, left_path, left_label, right_path, right_label) in enumerate(rows):
        y = TOP + index * (row_height + GAP)
        draw.text((LEFT, y), f"{pair_label}   {left_label}", fill="#f3f6fc", font=FONT)
        draw.text((LEFT + WIDTH + GAP, y), right_label, fill="#a9b7cd", font=FONT)
        canvas.paste(Image.open(left_path).convert("RGB"), (LEFT, y + LABEL))
        canvas.paste(Image.open(right_path).convert("RGB"), (LEFT + WIDTH + GAP, y + LABEL))
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, quality=90, optimize=True)


def main() -> int:
    internal = json.loads((H1 / "runs/PAIR_MEDIA_MANIFEST_v3.json").read_text(encoding="utf-8"))
    public = json.loads((H1 / "review/BLIND_REVIEW_PACKAGE_v3.json").read_text(encoding="utf-8"))
    thumb_root = H1 / "runs/thumbnails"
    internal_rows = []
    blind_rows = []
    frame_records = []
    clip_by_pair = {}
    for clip in internal["clips"]:
        clip_by_pair.setdefault(clip["pair_id"], {})[clip["variant"]] = clip
    for pair in public["pairs"]:
        pair_id = pair["pair_id"]
        code_left = pair["samples"]["left"]["sample_code"]
        code_right = pair["samples"]["right"]["sample_code"]
        internal_pair = clip_by_pair[pair_id]
        blind_left = H1 / pair["samples"]["left"]["file"].removeprefix("h1/")
        blind_right = H1 / pair["samples"]["right"]["file"].removeprefix("h1/")
        left_internal = internal_pair["B"]
        right_internal = internal_pair["C"]
        left_path = PROJECT / left_internal["path"]
        right_path = PROJECT / right_internal["path"]
        duration = float(left_internal["duration_seconds"])
        mid = duration / 2
        b_thumb = thumb_root / f"{pair_id}-B.png"
        c_thumb = thumb_root / f"{pair_id}-C.png"
        blind_l_thumb = thumb_root / f"{pair_id}-left.png"
        blind_r_thumb = thumb_root / f"{pair_id}-right.png"
        frame_at(left_path, b_thumb, mid)
        frame_at(right_path, c_thumb, mid)
        frame_at(blind_left, blind_l_thumb, mid)
        frame_at(blind_right, blind_r_thumb, mid)
        internal_rows.append((pair_id, b_thumb, "B / controlled", c_thumb, "C / image-motion"))
        blind_rows.append((pair_id, blind_l_thumb, f"{code_left} / left", blind_r_thumb, f"{code_right} / right"))
        for path, video_path in ((b_thumb, left_path), (c_thumb, right_path),
                                 (blind_l_thumb, blind_left), (blind_r_thumb, blind_right)):
            frame_records.append({"path": str(path.relative_to(PROJECT)),
                                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                  "source_video": str(video_path.relative_to(PROJECT)),
                                  "time_seconds": mid})
    internal_path = H1 / "review/contact-sheet-internal-v3.jpg"
    blind_path = H1 / "review/contact-sheet-blind-v3.jpg"
    sheet(internal_rows, internal_path)
    sheet(blind_rows, blind_path)
    write_json(H1 / "runs/CONTACT_SHEET_MANIFEST_v3.json", {
        "internal_sheet": str(internal_path.relative_to(PROJECT)),
        "blind_sheet": str(blind_path.relative_to(PROJECT)),
        "thumbnail_count": len(frame_records),
        "thumbnails": frame_records,
    })
    public["contact_sheet"] = str(blind_path.relative_to(PROJECT))
    write_json(H1 / "review/BLIND_REVIEW_PACKAGE.json", public)
    print(json.dumps({"internal_sheet": str(internal_path), "blind_sheet": str(blind_path),
                      "thumbnails": len(frame_records)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Publish cover: the title over a content frame picked from the finished video.

Renders never ship a cover file, so platforms would pick an arbitrary frame (often the empty intro).
Everything here is best effort: any failure returns None and the upload proceeds as before.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

SIZE = (1920, 1080)
BACKGROUND = (247, 246, 242)
INK = (28, 58, 44)
ACCENT = (31, 122, 84)
FONTS = ('/System/Library/Fonts/Hiragino Sans GB.ttc', '/System/Library/Fonts/STHeiti Medium.ttc',
         '/System/Library/Fonts/Supplemental/Arial Unicode.ttf',
         '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc', '/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc')
SAMPLE_POINTS = (0.12, 0.2, 0.3, 0.42, 0.55, 0.7)
TITLE_LINES = 3


def duration(ffmpeg: str, video: Path) -> float:
    run = subprocess.run([ffmpeg, '-nostdin', '-i', str(video)], capture_output=True, text=True, timeout=30, check=False)
    match = re.search(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)', run.stderr)
    return int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3]) if match else 0.0


def frames(ffmpeg: str, video: Path, work: Path) -> list[Path]:
    total = duration(ffmpeg, video)
    if total <= 0:
        return []
    out = []
    for index, point in enumerate(SAMPLE_POINTS):
        target = work / f'f{index}.jpg'
        run = subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-y', '-ss', f'{total * point:.2f}', '-i', str(video),
                              '-frames:v', '1', '-q:v', '2', str(target)], capture_output=True, timeout=60, check=False)
        if run.returncode == 0 and target.is_file() and target.stat().st_size:
            out.append(target)
    return out


def busiest(paths: list[Path]):
    """The frame with the most visual content: empty intros and black tails score lowest."""
    from PIL import Image, ImageStat
    best, best_score = None, -1.0
    for path in paths:
        image = Image.open(path).convert('RGB')
        image = image.crop((0, 0, image.width, int(image.height * (1 - SUBTITLE_STRIP))))
        small = image.resize((192, 108))
        score = sum(ImageStat.Stat(small.convert('L')).stddev) + sum(ImageStat.Stat(small).stddev) / 3
        if ImageStat.Stat(small.convert('L')).mean[0] < 20:
            continue
        if score > best_score:
            best, best_score = image, score
    return best


def font(size: int):
    from PIL import ImageFont
    for name in FONTS:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return None


NO_LINE_START = '，。、；：！？）》」』”’…,.;:!?)'
SUBTITLE_STRIP = 0.14


def wrap(draw, text: str, face, width: int) -> list[str]:
    """Greedy wrap; a closing punctuation mark never starts a line (it is pulled up, or takes its predecessor down)."""
    lines, line = [], ''
    for char in text:
        if line and draw.textlength(line + char, font=face) > width:
            if char in NO_LINE_START and len(line) > 1:
                lines.append(line[:-1])
                line = line[-1]
            else:
                lines.append(line)
                line = ''
        line += char
    return lines + ([line] if line else [])


def compose(frame, title: str):
    """1920x1080: title on top, the content frame as a card below."""
    from PIL import Image, ImageDraw, ImageFilter
    title = ' '.join(title.split())
    canvas = Image.new('RGB', SIZE, BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    margin, width = 110, SIZE[0] - 220
    for pixels in (138, 120, 104, 88, 74):
        face = font(pixels)
        if face is None:
            return None
        lines = wrap(draw, title, face, width)
        if len(lines) <= (2 if pixels > 100 else TITLE_LINES):
            break
    else:
        lines = lines[:TITLE_LINES]
        lines[-1] = lines[-1][:-1] + '…'
    height = int(pixels * 1.28)
    draw.rectangle((margin, 96, margin + 16, 96 + height * len(lines) - 20), fill=ACCENT)
    for index, line in enumerate(lines):
        draw.text((margin + 52, 80 + index * height), line, font=face, fill=INK, stroke_width=max(2, pixels // 40), stroke_fill=INK)
    top = 96 + height * len(lines) + 24
    card_height = SIZE[1] - top - 70
    card_width = int(card_height * frame.width / frame.height)
    card = frame.resize((card_width, card_height), Image.LANCZOS)
    left = (SIZE[0] - card_width) // 2
    shadow = Image.new('RGBA', SIZE, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((left, top + 12, left + card_width, top + card_height + 12), 28, fill=(0, 0, 0, 70))
    canvas.paste(shadow.filter(ImageFilter.GaussianBlur(22)), (0, 0), shadow.filter(ImageFilter.GaussianBlur(22)))
    mask = Image.new('L', card.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, card_width, card_height), 28, fill=255)
    canvas.paste(card, (left, top), mask)
    return canvas


def make_cover(video: Path, title: str, dest: Path, ffmpeg: str | None) -> Path | None:
    """Write dest (JPEG) and return it; None when anything needed is missing."""
    if not ffmpeg or not title.strip() or not video.is_file():
        return None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            frame = busiest(frames(ffmpeg, video, Path(tmp)))
            image = compose(frame, title) if frame is not None else None
            if image is None:
                return None
            dest.parent.mkdir(parents=True, exist_ok=True)
            partial = dest.with_suffix('.partial.jpg')
            image.save(partial, 'JPEG', quality=92)
            partial.replace(dest)
            return dest
    except Exception:
        return None

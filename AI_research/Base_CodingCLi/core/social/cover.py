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
PORTRAIT_SIZE = (1080, 1440)
DOUYIN_LANDSCAPE_SIZE = (1440, 1080)
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
    """Wrap Chinese and keep fitting Latin names/numbers together, with no leading closing punctuation."""
    lines, line = [], ''
    tokens = re.findall(r'\$?[A-Za-z0-9]+(?:[._/+:-][A-Za-z0-9]+)*|.', text)
    units = [unit for token in tokens for unit in (list(token) if draw.textlength(token, font=face) > width else [token])]
    for char in units:
        if line and draw.textlength(line + char, font=face) > width:
            if char in NO_LINE_START and len(line) > 1:
                last = re.search(r'[A-Za-z0-9]+(?:[._/+:-][A-Za-z0-9]+)*$', line)
                tail = last.group() if last else line[-1]
                lines.append(line[:-len(tail)])
                line = tail
            else:
                lines.append(line)
                line = ''
        line += char
    return [part for part in lines + ([line] if line else []) if part]


def compose(frame, title: str, size: tuple[int, int] = SIZE):
    """1920x1080: title on top, the content frame as a card below."""
    from PIL import Image, ImageDraw, ImageFilter, ImageOps
    title = ' '.join(title.split())
    canvas = Image.new('RGB', size, BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    margin, width = 110, size[0] - 220
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
    card = ImageOps.contain(frame, (size[0] - margin * 2, size[1] - top - 70), Image.Resampling.LANCZOS)
    card_width, card_height = card.size
    left = (size[0] - card_width) // 2
    shadow = Image.new('RGBA', size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((left, top + 12, left + card_width, top + card_height + 12), 28, fill=(0, 0, 0, 70))
    canvas.paste(shadow.filter(ImageFilter.GaussianBlur(22)), (0, 0), shadow.filter(ImageFilter.GaussianBlur(22)))
    mask = Image.new('L', card.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, card_width, card_height), 28, fill=255)
    canvas.paste(card, (left, top), mask)
    return canvas


def compose_portrait(frame, title: str):
    """A 3:4 profile cover with the title and intact frame inside its central square."""
    from PIL import Image, ImageDraw, ImageFilter, ImageOps
    canvas = Image.new('RGB', PORTRAIT_SIZE, BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    text = ' '.join(title.split())
    left, right, top, bottom = 84, PORTRAIT_SIZE[0] - 84, 208, 1230
    for pixels in (86, 78, 70, 62, 54, 48):
        face = font(pixels)
        if face is None:
            return None
        lines = wrap(draw, text, face, right - left - 32)
        if len(lines) <= 4:
            break
    else:
        lines = lines[:4]
        lines[-1] = lines[-1][:-1] + '…'
    line_height = int(pixels * 1.3)
    draw.rounded_rectangle((left, top + 12, left + 8, top + line_height * len(lines)), 4, fill=ACCENT)
    for index, line in enumerate(lines):
        draw.text((left + 32, top + index * line_height), line, font=face, fill=INK,
                  stroke_width=1, stroke_fill=INK)
    card_top = top + line_height * len(lines) + 64
    card = ImageOps.contain(frame.convert('RGB'), (right - left, bottom - card_top), Image.Resampling.LANCZOS)
    card_left = (PORTRAIT_SIZE[0] - card.width) // 2
    shadow = Image.new('RGBA', PORTRAIT_SIZE, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (card_left, card_top + 10, card_left + card.width, card_top + card.height + 10), 22, fill=(0, 0, 0, 55))
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    canvas.paste(shadow, (0, 0), shadow)
    mask = Image.new('L', card.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, card.width, card.height), 22, fill=255)
    canvas.paste(card, (card_left, card_top), mask)
    return canvas


def make_covers(video: Path, title: str, destinations: dict[str, Path], ffmpeg: str | None) -> dict[str, Path]:
    """Sample the video once for both orientations; never crop a landscape cover into a portrait."""
    if not ffmpeg or not title.strip() or not video.is_file():
        return {}
    made = {}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            frame = busiest(frames(ffmpeg, video, Path(tmp)))
            if frame is None:
                return {}
            for orientation, dest in destinations.items():
                image = (compose_portrait(frame, title) if orientation == 'portrait' else
                         compose(frame, title, DOUYIN_LANDSCAPE_SIZE if orientation == 'douyin_landscape' else SIZE))
                if image is None:
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                partial = dest.with_suffix('.partial.jpg')
                image.save(partial, 'JPEG', quality=92)
                partial.replace(dest)
                made[orientation] = dest
    except Exception:
        pass
    return made


def make_cover(video: Path, title: str, dest: Path, ffmpeg: str | None) -> Path | None:
    """Write the landscape JPEG; keep the original API for other platforms."""
    return make_covers(video, title, {'landscape': dest}, ffmpeg).get('landscape')

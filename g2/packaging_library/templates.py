"""Registered frame renderers for the packaging library."""
from __future__ import annotations

from pathlib import Path

FPS = 30
WIDTH, HEIGHT = 1920, 1080
FONT = Path("/System/Library/Fonts/Hiragino Sans GB.ttc")

def _luminance(color: str) -> float:
    r, g, b = (int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _fit_font(text: str, size: int, max_width: int, minimum: int = 28):
    from PIL import ImageFont
    while True:
        font = ImageFont.truetype(str(FONT), size, index=0)
        if font.getlength(text) <= max_width or size <= minimum:
            return font
        size -= 2


def _text_layer(text: str, font, color: str):
    from PIL import Image, ImageDraw
    box = font.getbbox(text)
    layer = Image.new("RGBA", (box[2] - min(box[0], 0) + 8, box[3] + 12), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((max(-box[0], 0), 0), text, font=font, fill=color)
    return layer


def _ease(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3


def _paste(canvas, layer, center_x: int, y: int, progress: float, rise: int = 28):
    from PIL import Image
    alpha = layer.getchannel("A").point(lambda v: int(v * progress))
    faded = layer.copy()
    faded.putalpha(alpha)
    canvas.alpha_composite(faded, (int(center_x - layer.width / 2), int(y + (1 - progress) * rise)))


def render_clip_frames(kind: str, cfg: dict, accent: str, frames: int, out_dir: Path) -> None:
    """Draw the registered template ('stinger' or 'end_card') frame by frame as PNG files."""
    from PIL import Image, ImageDraw
    if not FONT.is_file():
        raise ValueError("缺少中文字体 Hiragino Sans GB")
    out_dir.mkdir(parents=True, exist_ok=True)
    on_accent = "#ffffff" if _luminance(accent) < 0.62 else "#101827"
    if kind == "stinger":
        title = _text_layer(cfg["title"], _fit_font(cfg["title"], 170, 1600), on_accent)
        sub = _text_layer(cfg["subtitle"], _fit_font(cfg["subtitle"], 64, 1500), on_accent) if cfg.get("subtitle") else None
        for i in range(frames):
            t = i / FPS
            canvas = Image.new("RGBA", (WIDTH, HEIGHT), accent)
            draw = ImageDraw.Draw(canvas)
            line_w = int(560 * _ease(t / 0.45))
            draw.rectangle((WIDTH // 2 - line_w // 2, 632, WIDTH // 2 + line_w // 2, 640), fill=on_accent)
            _paste(canvas, title, WIDTH // 2, 380, _ease(t / 0.35))
            if sub:
                _paste(canvas, sub, WIDTH // 2, 680, _ease((t - 0.2) / 0.35))
            canvas.convert("RGB").save(out_dir / f"f{i:04d}.png")
    elif kind == "end_card":
        ink, soft = "#101827", "#475467"
        title = _text_layer(cfg["title"], _fit_font(cfg["title"], 150, 1400), ink)
        lines = [_text_layer(line, _fit_font(line, 52, 1500), soft) for line in cfg.get("lines", [])]
        for i in range(frames):
            t = i / FPS
            canvas = Image.new("RGBA", (WIDTH, HEIGHT), "#f6f8fb")
            draw = ImageDraw.Draw(canvas)
            draw.rectangle((0, 0, 28, HEIGHT), fill=accent)
            span = int(480 * _ease(t / 0.5))
            top = 330 if lines else 400
            draw.rectangle((WIDTH // 2 - span // 2, top + 235, WIDTH // 2 + span // 2, top + 243), fill=accent)
            _paste(canvas, title, WIDTH // 2, top, _ease(t / 0.35))
            for n, layer in enumerate(lines):
                _paste(canvas, layer, WIDTH // 2, top + 290 + n * 86, _ease((t - 0.2 - 0.12 * n) / 0.35))
            canvas.convert("RGB").save(out_dir / f"f{i:04d}.png")
    elif kind == "source_card":
        heading = "主要来源" if cfg["title"] == "来源" else cfg["title"]
        title = _text_layer(heading, _fit_font(heading, 90, 1500), "#101827")
        rows = cfg["sources"]
        for i in range(frames):
            t = i / FPS
            canvas = Image.new("RGBA", (WIDTH, HEIGHT), "#f6f8fb")
            draw = ImageDraw.Draw(canvas)
            draw.rectangle((0, 0, 28, HEIGHT), fill=accent)
            _paste(canvas, title, WIDTH // 2, 130, _ease(t / .35))
            for n, row in enumerate(rows):
                y = 340 + n * 205
                heading = _text_layer(row["title"], _fit_font(row["title"], 52, 1580), "#101827")
                detail = _text_layer(row["domain"] + "  ·  " + row["date"], _fit_font(row["domain"] + "  ·  " + row["date"], 36, 1580, minimum=16), "#475467")
                _paste(canvas, heading, WIDTH // 2, y, _ease((t - .15 * n) / .35))
                _paste(canvas, detail, WIDTH // 2, y + 78, _ease((t - .15 * n) / .35))
            canvas.convert("RGB").save(out_dir / f"f{i:04d}.png")
    else:
        raise ValueError(f"未注册的模板：{kind}")

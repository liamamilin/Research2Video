"""Registered frame renderers for the packaging library."""
from __future__ import annotations

from pathlib import Path

FPS = 30
WIDTH, HEIGHT = 1920, 1080
FONT = Path("/System/Library/Fonts/Hiragino Sans GB.ttc")


def visual_theme(ir: dict | None = None, production_style: dict | None = None) -> dict:
    """Match the existing content renderer, without introducing a selectable style.

    A legacy plan's style ID is not its rendered palette: information motion is
    light, whereas process diagrams use the dark renderer. Resolve after the IR
    exists, and record the resulting palette in the package evidence.
    """
    if (production_style or {}).get("id") == "editorial_light":
        from g2.production_plan import STYLES
        tokens = {**STYLES["editorial_light"], **production_style}
        return {"id": "editorial_light", **{key: tokens[key] for key in ("background", "text", "muted")}}
    preset = ((ir or {}).get("presentation") or {}).get("preset")
    if preset in {"information_motion", "explanation"}:
        return {"id": "information_motion", "background": "#f7f8f5", "text": "#202d28",
                "muted": "#69756e", "dot_grid": True}
    if preset == "information_cards":
        return {"id": "information_cards", "background": "#f3f5f1", "text": "#213d39", "muted": "#73847b"}
    if preset in {"kinetic_typography", "image_slideshow", "chat_dialogue", "object_showcase", "dynamic_collage"}:
        return {"id": "creative_light", "background": "#f5f2e9", "text": "#213d39", "muted": "#73847b"}
    if ir is not None:
        return {"id": "legacy_dark", "background": "#0b1020", "text": "#f3f6fc", "muted": "#a9b7cd"}
    return {"id": "common", "background": "#f6f8fb", "text": "#101827", "muted": "#475467"}


def _card_canvas(theme: dict, accent: str, *, version: int):
    from PIL import Image, ImageDraw
    canvas = Image.new("RGBA", (WIDTH, HEIGHT), theme["background"])
    draw = ImageDraw.Draw(canvas)
    if version == 1:
        draw.rectangle((0, 0, 28, HEIGHT), fill=accent)
    else:
        if theme.get("dot_grid"):
            for y in range(13, HEIGHT, 26):
                for x in range(13, WIDTH, 26):
                    draw.ellipse((x, y, x + 1, y + 1), fill="#dde5db")
        # Keep decoration inside the same safe area as the content. A full-height
        # strip at x=0 looked like a leftover edge from a different-size frame.
        draw.rounded_rectangle((140, 70, 224, 75), radius=2, fill=accent)
    return canvas


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


def render_clip_frames(kind: str, cfg: dict, accent: str, frames: int, out_dir: Path, *,
                       version: int = 1, theme: dict | None = None) -> None:
    """Draw the registered template ('stinger' or 'end_card') frame by frame as PNG files."""
    from PIL import Image, ImageDraw
    if not FONT.is_file():
        raise ValueError("缺少中文字体 Hiragino Sans GB")
    supported = {"stinger": {1}, "end_card": {1, 2}, "source_card": {1, 2}}
    if version not in supported.get(kind, set()):
        raise ValueError(f"未注册的模板版本：{kind}@{version}")
    out_dir.mkdir(parents=True, exist_ok=True)
    # Saved v1 component selections retain their exact original palette/layout.
    theme = visual_theme() if version == 1 else theme or visual_theme()
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
        ink, soft = theme["text"], theme["muted"]
        title = _text_layer(cfg["title"], _fit_font(cfg["title"], 150, 1400), ink)
        lines = [_text_layer(line, _fit_font(line, 52, 1500), soft) for line in cfg.get("lines", [])]
        for i in range(frames):
            t = i / FPS
            canvas = _card_canvas(theme, accent, version=version)
            draw = ImageDraw.Draw(canvas)
            span = int(480 * _ease(t / 0.5))
            top = 330 if lines else 400
            draw.rectangle((WIDTH // 2 - span // 2, top + 235, WIDTH // 2 + span // 2, top + 243), fill=accent)
            _paste(canvas, title, WIDTH // 2, top, _ease(t / 0.35))
            for n, layer in enumerate(lines):
                _paste(canvas, layer, WIDTH // 2, top + 290 + n * 86, _ease((t - 0.2 - 0.12 * n) / 0.35))
            canvas.convert("RGB").save(out_dir / f"f{i:04d}.png")
    elif kind == "source_card":
        heading = "主要来源" if cfg["title"] == "来源" else cfg["title"]
        title = _text_layer(heading, _fit_font(heading, 90, 1500), theme["text"])
        rows = cfg["sources"]
        for i in range(frames):
            t = i / FPS
            canvas = _card_canvas(theme, accent, version=version)
            _paste(canvas, title, WIDTH // 2, 130, _ease(t / .35))
            for n, row in enumerate(rows):
                y = 340 + n * 205
                heading = _text_layer(row["title"], _fit_font(row["title"], 52, 1580), theme["text"])
                detail = _text_layer(row["domain"] + "  ·  " + row["date"], _fit_font(row["domain"] + "  ·  " + row["date"], 36, 1580, minimum=16), theme["muted"])
                _paste(canvas, heading, WIDTH // 2, y, _ease((t - .15 * n) / .35))
                _paste(canvas, detail, WIDTH // 2, y + 78, _ease((t - .15 * n) / .35))
            canvas.convert("RGB").save(out_dir / f"f{i:04d}.png")
    else:
        raise ValueError(f"未注册的模板：{kind}")

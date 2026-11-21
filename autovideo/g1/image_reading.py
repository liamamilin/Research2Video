"""Measure an image's blank exterior without choosing or removing any subject."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops


def reading_bounds(path: Path) -> dict | None:
    """Keep every non-white pixel, plus breathing room; busy images stay whole.

    Exact white is deliberate: faint rules, watermarks and JPEG noise count as
    content. This is a conservative framing aid, not OCR or subject detection.
    The original file and model-facing image coordinates are never rewritten.
    """
    try:
        with Image.open(path) as source:
            rgba = source.convert('RGBA')
            image = Image.alpha_composite(Image.new('RGBA', rgba.size, 'white'), rgba).convert('RGB')
        width, height = image.size
        box = ImageChops.difference(image, Image.new('RGB', image.size, 'white')).getbbox()
    except (OSError, ValueError):
        return None
    if box:
        padding = round(min(width, height) * .025)
        x1, y1, x2, y2 = box
        box = [max(0, x1 - padding), max(0, y1 - padding),
               min(width, x2 + padding), min(height, y2 + padding)]
    else:
        box = [0, 0, width, height]
    return {'width': width, 'height': height, 'box': box}

"""Measured price-change geometry shared by planning, compilation and rendering."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import math
import re

from PIL import ImageFont

INNER_WIDTH = 1528
LABEL_WIDTH = 400
GAP = 36
MAX_HEIGHT = 440


@lru_cache(maxsize=None)
def _font(size: int):
    for path in ("/System/Library/Fonts/Hiragino Sans GB.ttc",
                 "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"):
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    raise ValueError("数字变化卡需要可用的中文字体")


def _width(text: str, size: int) -> float:
    return _font(size).getlength(text)


def _lines(text: str, size: int, width: float) -> int:
    # Labels are at most ten characters; a conservative width includes glyph
    # weight and rounding differences between Pillow and the browser.
    return max(1, math.ceil(_width(text, size) * 1.04 / width))


def _value_width(text: str, size: int) -> float:
    match = re.match(r"^(\D*?)(\d[\d,]*(?:\.\d+)?)(.*)$", text)
    parts = (match[1], match[2], match[3]) if match else ("", text, "")
    side = int(size * .42 + .5)
    return sum(_width(part, use) for part, use in zip(parts, (side, size, side))) * 1.04


def price_layout(props: dict, large: bool = False) -> dict:
    """Fit unchanged values; move labels above the values when a row needs room.

    The returned geometry is compiler output, never an authored component prop.
    Number strings, units, deltas, directions and narration stay unchanged.
    """
    scale = lambda value: int(value * (1.3 if large else 1) + .5)
    rows = props["rows"]
    badge, unit = scale(52), scale(36)
    base = 180 if len(rows) == 1 else 128
    for stacked in (False, True):
        row_gap, unit_gap = (24, 12) if stacked else (34, 20)
        for size in range(base, 87, -2):
            for label in range(scale(46), scale(32) - 1, -2):
                height = (_lines(props["unit"], unit, INNER_WIDTH) * unit * 1.4 + unit_gap
                          if props.get("unit") else 0) + row_gap * (len(rows) - 1)
                for row in rows:
                    delta = (("−" if row["direction"] == "down" else "+") + row["change"]
                             if row.get("change") else "")
                    badge_width = _width(delta, badge) * 1.04 + 52 if delta else 0
                    label_width = INNER_WIDTH - badge_width - GAP if stacked and delta else (
                        INNER_WIDTH if stacked else LABEL_WIDTH)
                    lines = _lines(row["label"], label, label_width)
                    room = INNER_WIDTH if stacked else INNER_WIDTH - LABEL_WIDTH - GAP - (
                        badge_width + GAP if delta else 0)
                    used = (_value_width(row["before"], size) + _value_width(row["after"], size)
                            + round(size * .62) + 2 * round(size * .18))
                    if lines > 2 or used + 24 > room:
                        break
                    label_height = max(lines * label * 1.35, badge * 1.3 + 12 if delta else 0)
                    height += label_height + 12 + size * 1.1 if stacked else max(label_height, size * 1.1)
                else:
                    if height <= MAX_HEIGHT:
                        return {"number_size": size, "side_size": int(size * .42 + .5),
                                "label_size": label, "label_width": LABEL_WIDTH,
                                "badge_size": badge, "unit_size": unit,
                                "row_gap": row_gap, "unit_gap": unit_gap,
                                "stacked_label": stacked, "height": round(height, 2)}
    raise ValueError("price_change rows do not fit at readable sizes; split the on-screen comparison")

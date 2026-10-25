"""Turn measured image-relative DOM rectangles into bounded renderer instructions."""
from __future__ import annotations

from core.video import visual_matching


def for_asset(asset: dict, need: dict | None = None) -> dict | None:
    quality = asset.get("quality") or {}
    targets = quality.get("dom_targets") or []
    anchors = visual_matching.terms((need or {}).get("claims", quality.get("region_text", "")))
    wanted_prices = set(visual_matching.prices((need or {}).get("claims", "")))
    ranked = []
    for target in targets:
        if not (0 <= target["x1"] < target["x2"] <= 1000 and 0 <= target["y1"] < target["y2"] <= 1000):
            continue
        text = target["text"]
        overlap = sum(word in text.lower() for word in anchors)
        if not overlap:
            continue
        score = overlap + (4 if wanted_prices and wanted_prices.issubset(visual_matching.prices(text)) else 0)
        score += 1 if target["tag"] in {"p", "code", "button", "td"} else 0
        score -= len(text) / 1000
        ranked.append((score, target))
    if not ranked:
        return None
    _, target = max(ranked, key=lambda row: row[0])
    # Render the measured DOM coordinates. A model's focus proposal remains in
    # the audit and cannot silently replace these verified coordinates.
    region = {k: target[k] for k in ("x1", "y1", "x2", "y2")}
    label = "价格与计费条件" if (need or {}).get("kind") == "pricing" else {
        "code": "对应代码示例", "p": "对应功能说明", "button": "相关操作入口", "a": "相关页面入口"}.get(target["tag"], "页面重点内容")
    region.update(label=label, style="box")
    return {"width": asset.get("registered_width", asset["width"]),
            "height": asset.get("registered_height", asset["height"]), "regions": [region]}

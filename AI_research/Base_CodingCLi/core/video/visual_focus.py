"""Turn measured image-relative DOM rectangles into bounded renderer instructions."""
from __future__ import annotations

from core.video import visual_matching


def _reading_regions(quality: dict, need: dict) -> list[dict]:
    """Use actual OCR rectangles inside embedded screenshots and code examples."""
    image = quality.get("image") or {}
    anchors = visual_matching.terms(need["text"])
    visible = (image.get("vision") or {}).get("visible_text", "").lower()
    candidates = []
    for target in image.get("ocr_targets", []):
        text = target["text"].strip()
        if len(text) < 5 or not (0 <= target["x1"] < target["x2"] <= 1000 and 0 <= target["y1"] < target["y2"] <= 1000):
            continue
        if quality.get("visual_kind") == "image" and target["y2"] < 200:
            # Embedded app screenshots often include a toolbar. A generic word
            # such as "Nodes" must not outrank the actual workflow nodes below.
            if not any(word in need["text"] for word in ("菜单", "入口", "导航", "工具栏")):
                continue
        score = sum(word in text.lower() for word in anchors)
        pictured = text.lower() in visible
        if not score and not pictured:
            continue
        candidates.append((score + int(pictured), target))
    selected = []
    for _, target in sorted(candidates, key=lambda item: item[0], reverse=True):
        # Nearby OCR lines belong to the same object. Avoid visiting a label
        # twice just because OCR split it into two lines.
        if any(abs(target["x1"]-row["x1"]) < 60 and abs(target["y1"]-row["y1"]) < 110 for row in selected):
            continue
        selected.append(target)
        if len(selected) == 6:
            break
    if len(selected) > 3:
        if quality.get("visual_kind") == "image":
            selected.sort(key=lambda row: (row["y1"]//150, row["x1"]))
            selected = [selected[0], selected[(len(selected)-1)//2], selected[-1]]
        else:
            selected = selected[:3]
    regions = []
    for target in sorted(selected, key=lambda row: (row["y1"]//150, row["x1"])):
        pad_y = (target["y2"]-target["y1"]) * (4 if quality.get("visual_kind") == "image" else 1)
        regions.append({"x1": max(0, target["x1"]-25), "y1": max(0, target["y1"]-pad_y),
                        "x2": min(1000, target["x2"]+25), "y2": min(1000, target["y2"]+20),
                        "label": target["text"][:32], "style": "box"})
    return regions


def for_asset(asset: dict, need: dict | None = None) -> dict | None:
    quality = asset.get("quality") or {}
    if (need or {}).get("visual_subject"):
        regions = _reading_regions(quality, need)
        if regions:
            return {"width": asset.get("registered_width", asset["width"]),
                    "height": asset.get("registered_height", asset["height"]), "regions": regions}
    targets = quality.get("dom_targets") or []
    anchors = visual_matching.terms((need or {}).get("claims", quality.get("region_text", "")))
    wanted_prices = set(visual_matching.prices((need or {}).get("claims", "")))
    ranked = []
    for target in targets:
        if not (0 <= target["x1"] < target["x2"] <= 1000 and 0 <= target["y1"] < target["y2"] <= 1000):
            continue
        text = target["text"]
        overlap = sum(word in text.lower() for word in anchors)
        is_visual = quality.get("visual_kind") in {"image", "code"} and target["tag"] in {"img", "pre"}
        if not overlap and not is_visual:
            continue
        score = overlap + (4 if wanted_prices and wanted_prices.issubset(visual_matching.prices(text)) else 0) + (8 if is_visual else 0)
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
        "img": "功能演示与结果", "pre": "输入与代码示例",
        "code": "对应代码示例", "p": "对应功能说明", "button": "相关操作入口", "a": "相关页面入口"}.get(target["tag"], "页面重点内容")
    region.update(label=label, style="box")
    return {"width": asset.get("registered_width", asset["width"]),
            "height": asset.get("registered_height", asset["height"]), "regions": [region]}

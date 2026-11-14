"""Opening card: the intro card shows the video's most striking number instead of the column name.

Off unless the script froze ``presentation.opening_card: true`` (an A/B switch, spec/ab-testing.md §6). It reuses
AutoVideo's existing intro card, switched on (``packaging.intro`` title ≤12 and subtitle ≤24 characters), so the renderer is
unchanged. The words come from the cover copy (core/social/cover_copy.py): only numbers the narration contains.
Any failure leaves the payload as it was and says why in the visuals audit.

Known limit (11-04, real render): when a brand is applied, AutoVideo turns the intro off again
(autovideo/g3/brand_profiles.py), so the card does not show on branded videos. The switch stays「待确认」in the A/B
registry until that is decided.
"""
from __future__ import annotations

import copy
import logging
from pathlib import Path

logger = logging.getLogger("ai_research.web.opening_card")

TITLE_MAX, SUBTITLE_MAX = 12, 24
CARD_SECONDS = 2.0  # packaging allows 0.5–5 s; long enough to read one number


def wanted(context: dict | None) -> bool:
    return bool(((context or {}).get("presentation") or {}).get("opening_card") is True)


def effective_packaging(payload: dict) -> dict | None:
    """Recipe settings, then matrix settings, then the request's own packaging (later layers win)."""
    from core.video.packaging import merge
    context = payload.get("production_context") or {}
    layers = [((context.get("content_recipe") or {}).get("settings") or {}).get("packaging"),
              ((context.get("matrix") or {}).get("settings") or {}).get("packaging"), payload.get("packaging")]
    present = [layer for layer in layers if isinstance(layer, dict)]
    if not present:
        return None
    merged: dict = {}
    for layer in present:
        merged = merge(merged, layer)
    return merged


def apply(payload: dict, render: dict, context: dict | None, cache: Path) -> tuple[dict, dict]:
    """(payload, audit note). Unchanged payload with a reason when the card cannot be shown."""
    if not wanted(context):
        return payload, {}
    if "package" in payload:
        return payload, {"applied": False, "reason": "旧版片头格式，不支持要点卡"}
    packaging = effective_packaging(payload)
    if packaging is None or packaging.get("enabled") is False:
        return payload, {"applied": False, "reason": "这个配方没有片头"}
    try:
        from core.social import cover_copy
        words = cover_copy.for_render(render, render.get("title") or payload.get("title") or "", cache)
    except Exception as exc:  # noqa: BLE001 - the ordinary intro is always available
        logger.warning("opening card for %s: copy failed (%s)", render.get("id"), type(exc).__name__)
        words = None
    if not words or not words.get("big"):
        return payload, {"applied": False, "reason": "没有合适的数字，用原来的片头"}
    out = copy.deepcopy(payload)
    # the highest packaging layer, as the article path in video_render.create already sends it
    out["packaging"] = copy.deepcopy(packaging)
    card = out["packaging"].setdefault("intro", {})
    if not isinstance(card, dict):
        card = out["packaging"]["intro"] = {}
    # AutoVideo's common defaults keep the intro off (packaging-resolved.json: intro.enabled false), so the card
    # turns it on: a short stinger before the content carrying the number.
    card.update(enabled=True, seconds=CARD_SECONDS)
    card["title"] = words["big"][:TITLE_MAX]
    subtitle = words.get("figure") or words.get("topic") or ""
    if subtitle and len(subtitle) <= SUBTITLE_MAX:
        card["subtitle"] = subtitle
    else:
        card.pop("subtitle", None)
    return out, {"applied": True, "title": card["title"], "subtitle": card.get("subtitle", "")}

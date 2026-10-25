"""The AutoVideo packaging selection contract used by recipe configuration.

Rendering implementations and complete common defaults belong to AutoVideo's
component library. Recipes only reference its preset and override selected data.
"""
from __future__ import annotations

import copy
import math
import re

SLOTS = {"intro": "stinger", "outro": "end_card", "badge": "corner_badge",
         "source_card": "source_card", "sound": "synth_sting"}
FIELDS = {"intro": {"component", "enabled", "title", "subtitle", "seconds"},
          "outro": {"component", "enabled", "title", "lines", "seconds"},
          "badge": {"component", "enabled", "text"},
          "source_card": {"component", "enabled", "seconds"}, "sound": {"component", "enabled"}}


def merge(defaults: dict, overrides: dict) -> dict:
    result = copy.deepcopy(defaults)
    for key, value in overrides.items():
        result[key] = {**result.get(key, {}), **value} if key in SLOTS and isinstance(value, dict) else copy.deepcopy(value)
    return result


def validate(raw: dict) -> dict:
    if not isinstance(raw, dict) or set(raw) - {"preset", "recipe", "label", "accent", "enabled", *SLOTS}:
        raise ValueError("packaging 含不支持的字段")
    value = copy.deepcopy(raw)
    if value.setdefault("preset", "common") != "common":
        raise ValueError("packaging.preset 须为 common")
    if "enabled" in value and not isinstance(value["enabled"], bool):
        raise ValueError("packaging.enabled 须为 true 或 false")
    if "accent" in value and (not isinstance(value["accent"], str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value["accent"])):
        raise ValueError("packaging.accent 须为 #rrggbb")
    for name, limit in (("label", 12), ("recipe", 128)):
        if name in value:
            _text(value[name], name, limit)
    for slot, component in SLOTS.items():
        if slot not in value:
            continue
        part = value[slot]
        if not isinstance(part, dict) or set(part) - FIELDS[slot]:
            raise ValueError(f"packaging.{slot} 含不支持的字段")
        if part.get("component", component) != component:
            raise ValueError(f"packaging.{slot}.component 须为 {component}")
        if "enabled" in part and not isinstance(part["enabled"], bool):
            raise ValueError(f"packaging.{slot}.enabled 须为 true 或 false")
        for name, limit in (("title", 12), ("subtitle", 24), ("text", 12)):
            if name in part:
                _text(part[name], f"{slot}.{name}", limit)
        if "lines" in part:
            if not isinstance(part["lines"], list) or not 1 <= len(part["lines"]) <= 3:
                raise ValueError("packaging.outro.lines 须为 1–3 行")
            for line in part["lines"]:
                _text(line, "outro.lines", 30)
        if "seconds" in part:
            seconds = part["seconds"]
            if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not .5 <= seconds <= 5:
                raise ValueError(f"packaging.{slot}.seconds 须在 0.5–5 之间")
    return value


def _text(value, name: str, limit: int) -> None:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f"packaging.{name} 须为 1–{limit} 字的单行文字")

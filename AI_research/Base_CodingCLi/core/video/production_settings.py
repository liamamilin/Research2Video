"""Validate upstream settings; AutoVideo owns defaults, capabilities and resolution."""
from __future__ import annotations

import copy
import re

FIELDS = {"production_recipe", "production_mode", "visual_style", "video_form", "voice_id",
          "video_speed", "creative_intent", "packaging", "brand"}


def inherit(defaults: dict, overrides: dict) -> dict:
    """Keep omitted task fields inherited, including individual packaging settings."""
    result = copy.deepcopy(defaults)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = inherit(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def validate(value: dict | None) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - FIELDS:
        raise ValueError("render 只接受制作配方、档位、风格、形式、声音、包装与品牌设置")
    result = copy.deepcopy(value)
    for field, allowed in (("production_mode", {"standard", "fast"}),
                           ("visual_style", {"legacy", "editorial_light"})):
        if field in result and (not isinstance(result[field], str) or result[field] not in allowed):
            raise ValueError(f"render.{field} 无效")
    for field in ("production_recipe", "voice_id", "video_form"):
        if field in result and (not isinstance(result[field], str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,79}", result[field])):
            raise ValueError(f"render.{field} 无效")
    if "video_speed" in result and (isinstance(result["video_speed"], bool) or result["video_speed"] not in (.5, 1, 1.25, 1.5, 2)):
        raise ValueError("render.video_speed 无效")
    if "creative_intent" in result and not isinstance(result["creative_intent"], dict):
        raise ValueError("render.creative_intent 须为映射")
    if "brand" in result:
        brand = result["brand"]
        if (not isinstance(brand, dict) or set(brand) - {"enabled", "profile"}
                or not isinstance(brand.get("enabled", True), bool)
                or (brand.get("enabled", True) and (not isinstance(brand.get("profile"), str)
                    or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", brand["profile"])) )):
            raise ValueError("render.brand 须选择品牌 profile 或 enabled:false")
    if "packaging" in result:
        from .packaging import validate as validate_packaging
        result["packaging"] = validate_packaging(result["packaging"])
    return result

"""Portable brand contracts. Personal profiles and assets belong in local runtime data."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re


class BrandError(ValueError):
    pass


def header_layout(mode: str) -> dict:
    """Shared top rail: content on the left, identity on the right."""
    margin = 80 if mode == "fast" else 140
    return {"version": 1, "left": margin, "right": margin, "top": 54,
            "width": 520, "height": 76, "gap": 40}


def digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def identity(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value):
        raise BrandError("品牌编号须为小写字母、数字、下划线或连字符")
    return value


def selection(value: dict | None) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {"enabled", "profile"}:
        raise BrandError("brand 只接受 enabled 和 profile")
    enabled = value.get("enabled", True)
    if not isinstance(enabled, bool):
        raise BrandError("brand.enabled 须为布尔值")
    if not enabled:
        return {"enabled": False}
    return {"enabled": True, "profile": identity(value.get("profile"))}


def profile(value: dict) -> dict:
    fields = {"id", "name", "tagline", "accent", "background", "text", "intro_seconds", "outro_seconds", "assets",
              "corner_reveal_seconds", "cta", "apply_to_content", "corner_tagline"}
    if not isinstance(value, dict) or set(value) - fields:
        raise BrandError("品牌配置含不支持的字段")
    result = {"id": identity(value.get("id")), "name": value.get("name", ""),
              "tagline": value.get("tagline", ""), "accent": value.get("accent", "#28775b"),
              "background": value.get("background", "#f5f6f2"), "text": value.get("text", "#172b23"),
              "outro_seconds": value.get("outro_seconds", 4), "assets": copy.deepcopy(value.get("assets", {}))}
    for key, limit in (("name", 48), ("tagline", 64)):
        if (not isinstance(result[key], str) or len(result[key]) > limit
                or (key == "name" and not result[key].strip())
                or any(ord(c) < 32 for c in result[key])):
            raise BrandError(f"品牌 {key} 需为单行文字，最多 {limit} 字")
    for key in ("accent", "background", "text"):
        if not isinstance(result[key], str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", result[key]):
            raise BrandError(f"品牌 {key} 须为 #RRGGBB 颜色")
    duration = result["outro_seconds"]
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 3 <= duration <= 6:
        raise BrandError("品牌片尾须为 3–6 秒")
    # Omission stays byte-compatible with existing frozen brand profiles.
    if "intro_seconds" in value:
        intro = value["intro_seconds"]
        if (isinstance(intro, bool) or not isinstance(intro, (int, float))
                or not (intro == 0 or .5 <= intro <= 2)):
            raise BrandError("品牌片头须为 0（关闭）或 0.5–2 秒")
        result["intro_seconds"] = intro
    if "corner_reveal_seconds" in value:
        seconds = value["corner_reveal_seconds"]
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 0 <= seconds <= 1.5:
            raise BrandError("开场角标渐显须为 0–1.5 秒")
        result["corner_reveal_seconds"] = seconds
    if "apply_to_content" in value:
        if not isinstance(value["apply_to_content"], bool):
            raise BrandError("正文沿用品牌配色须为 true 或 false")
        result["apply_to_content"] = value["apply_to_content"]
    if "corner_tagline" in value:
        if not isinstance(value["corner_tagline"], bool):
            raise BrandError("角标显示品牌 Slogan 须为 true 或 false")
        result["corner_tagline"] = value["corner_tagline"]
    if "cta" in value:
        cta = value["cta"]
        if (not isinstance(cta, dict) or set(cta) - {"enabled", "message", "sound"}
                or not isinstance(cta.get("enabled", True), bool)):
            raise BrandError("互动引导只接受 enabled、message 和 sound")
        message = cta.get("message", "喜欢这期内容，欢迎")
        if not isinstance(message, str) or len(message) > 24 or any(ord(c) < 32 for c in message):
            raise BrandError("互动引导文案须为单行文字，最多 24 字")
        result["cta"] = {"enabled": cta.get("enabled", True), "message": message}
        if "sound" in cta:
            if not isinstance(cta["sound"], bool):
                raise BrandError("互动短音效须为 true 或 false")
            result["cta"]["sound"] = cta["sound"]
    assets = result["assets"]
    if not isinstance(assets, dict) or set(assets) - {"logo", "sonic"}:
        raise BrandError("品牌素材只支持 logo 和 sonic")
    for kind, asset in assets.items():
        ext = "png" if kind == "logo" else "wav"
        if (not isinstance(asset, dict) or set(asset) != {"file", "sha256"}
                or not re.fullmatch(r"[0-9a-f]{64}", asset.get("sha256", ""))
                or asset.get("file") != asset["sha256"] + "." + ext):
            raise BrandError("品牌素材记录无效")
    return result


def content_style(style: dict, config: dict) -> dict:
    """Freeze an opted-in brand palette into supported editorial card styles."""
    result = copy.deepcopy(style)
    if not config.get("apply_to_content") or style.get("id") != "editorial_light":
        return result

    def blend(foreground: str, background: str, amount: float) -> str:
        channels = [round(int(foreground[i:i+2], 16)*amount + int(background[i:i+2], 16)*(1-amount))
                    for i in (1, 3, 5)]
        return "#" + "".join(f"{channel:02x}" for channel in channels)

    background, text, accent = config["background"], config["text"], config["accent"]
    result.update(background=background, text=text, subtitle=text, accent=accent,
                  muted=blend(text, background, .64), line=blend(text, background, .14),
                  inactive=blend(text, background, .25), soft=blend(accent, background, .06),
                  palette_source="brand")
    return result


def check_record(value: dict) -> dict:
    if (not isinstance(value, dict) or value.get("schema_version") != 1
            or value.get("sha256") != digest({k: v for k, v in value.items() if k != "sha256"})):
        raise BrandError("品牌快照完整性校验失败")
    config = selection(value.get("selection"))
    if config is None or not config["enabled"] or profile(value.get("profile")) != value["profile"]:
        raise BrandError("品牌快照配置无效")
    if value["profile"]["id"] != config["profile"]:
        raise BrandError("品牌选择与快照不一致")
    return copy.deepcopy(value)


def frozen_input(input_dir: Path, request: dict) -> dict | None:
    record = request.get("brand_resolved")
    if record is None:
        if (request.get("brand") or {}).get("enabled"):
            raise BrandError("任务缺少品牌快照")
        return None
    record = check_record(record)
    if selection(request.get("brand")) != record["selection"]:
        raise BrandError("品牌选择与快照不一致")
    for asset in record["profile"]["assets"].values():
        path = input_dir / "brand" / asset["file"]
        if (path.is_symlink() or path.parent.is_symlink() or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != asset["sha256"]):
            raise BrandError("冻结的品牌素材缺失或已改变")
    return record

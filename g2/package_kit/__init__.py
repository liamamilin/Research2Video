"""Packaging kit: validated stinger / end-card request plus licensed sound assets.

Pure standard library so the API process can validate requests without the media stack.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parent
TEMPLATES = {"intro": "stinger", "outro": "end_card"}
SECONDS_RANGE = (0.5, 5.0)
LIMITS = {"badge": 12, "title": 12, "subtitle": 24, "lines": 3, "line": 30}
SOUND_NAME = re.compile(r"[a-z0-9_]{1,40}")
ACCENT = re.compile(r"#[0-9a-fA-F]{6}")
ALLOWED_KEYS = {"intro": {"template", "title", "subtitle", "seconds", "sound"},
                "outro": {"template", "title", "lines", "seconds", "sound"}}


class PackageConfigError(ValueError):
    pass


def _text(value, label: str, limit: int) -> str:
    if not isinstance(value, str):
        raise PackageConfigError(f"{label} 须为字符串")
    value = value.strip()
    if not value or len(value) > limit:
        raise PackageConfigError(f"{label} 须为 1–{limit} 个字")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise PackageConfigError(f"{label} 须为单行文字，不能含控制字符")
    return value


def load_kit() -> dict:
    return json.loads((KIT_ROOT / "kit.json").read_text(encoding="utf-8"))


def sound_names() -> list[str]:
    return sorted(item["name"] for item in load_kit()["sounds"])


def sound_asset(name: str) -> dict:
    """Return the sound's metadata with a verified file path; raise on missing or altered files."""
    for item in load_kit()["sounds"]:
        if item["name"] == name:
            path = KIT_ROOT / item["file"]
            if not path.is_file() or path.is_symlink():
                raise PackageConfigError(f"提示音文件缺失：{name}")
            if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                raise PackageConfigError(f"提示音文件与元数据的 sha256 不一致：{name}")
            return {**item, "path": path}
    raise PackageConfigError(f"套件里没有提示音：{name}")


def validate_package(value) -> dict:
    """Validate and normalise a request ``package`` block. Unknown fields are rejected."""
    if not isinstance(value, dict):
        raise PackageConfigError("package 须为对象")
    extra = set(value) - {"accent", "intro", "outro", "badge"}
    if extra:
        raise PackageConfigError(f"package 含不支持的字段：{sorted(extra)[0]}")
    accent = value.get("accent")
    if not isinstance(accent, str) or not ACCENT.fullmatch(accent):
        raise PackageConfigError("accent 须为 #rrggbb")
    result = {"accent": accent.lower()}
    if value.get("badge") is not None:
        result["badge"] = _text(value["badge"], "badge", LIMITS["badge"])
    if value.get("intro") is None and value.get("outro") is None:
        raise PackageConfigError("package 至少要有 intro 或 outro")
    for part in ("intro", "outro"):
        item = value.get(part)
        if item is None:
            continue
        if not isinstance(item, dict):
            raise PackageConfigError(f"{part} 须为对象")
        extra = set(item) - ALLOWED_KEYS[part]
        if extra:
            raise PackageConfigError(f"{part} 含不支持的字段：{sorted(extra)[0]}")
        if item.get("template") != TEMPLATES[part]:
            raise PackageConfigError(f"{part}.template 只能是 {TEMPLATES[part]}")
        seconds = item.get("seconds")
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) \
                or not SECONDS_RANGE[0] <= seconds <= SECONDS_RANGE[1]:
            raise PackageConfigError(f"{part}.seconds 须在 {SECONDS_RANGE[0]}–{SECONDS_RANGE[1]} 之间")
        out = {"template": item["template"], "title": _text(item.get("title"), f"{part}.title", LIMITS["title"]),
               "seconds": float(seconds)}
        if part == "intro" and item.get("subtitle") is not None:
            out["subtitle"] = _text(item["subtitle"], "intro.subtitle", LIMITS["subtitle"])
        if part == "outro" and item.get("lines") is not None:
            lines = item["lines"]
            if not isinstance(lines, list) or not 1 <= len(lines) <= LIMITS["lines"]:
                raise PackageConfigError(f"outro.lines 须为 1–{LIMITS['lines']} 行")
            out["lines"] = [_text(line, "outro.lines 每行", LIMITS["line"]) for line in lines]
        if item.get("sound") is not None:
            sound = item["sound"]
            if not isinstance(sound, str) or not SOUND_NAME.fullmatch(sound):
                raise PackageConfigError(f"{part}.sound 须为小写字母、数字、下划线")
            if sound not in sound_names():
                raise PackageConfigError(f"{part}.sound 不在套件里：{sound}")
            out["sound"] = sound
        result[part] = out
    return result

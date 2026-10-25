"""Trusted packaging components, common defaults and immutable per-job selections.

Only data is accepted from callers. Component IDs select registered implementations;
they never name a file to execute. The resolved record is saved with the input so
retries and revisions do not silently pick up changes to common defaults.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from urllib.parse import urlsplit

from g2.package_kit import PackageConfigError, _text, validate_package

ROOT = Path(__file__).resolve().parent
SLOTS = ("intro", "outro", "badge", "source_card", "sound")
KEYS = {"preset", "recipe", "label", "accent", "enabled", *SLOTS}
SLOT_KEYS = {
    "intro": {"component", "enabled", "title", "subtitle", "seconds"},
    "outro": {"component", "enabled", "title", "lines", "seconds"},
    "badge": {"component", "enabled", "text"},
    "source_card": {"component", "enabled", "seconds"},
    "sound": {"component", "enabled"},
}


def catalog() -> dict:
    return json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))


def validate_selection(value: dict | None) -> dict:
    if value is None:
        return {"preset": "common"}
    if not isinstance(value, dict) or set(value) - KEYS:
        raise PackageConfigError("packaging 含不支持的字段")
    value = copy.deepcopy(value)
    preset = value.setdefault("preset", "common")
    library = catalog()
    if not isinstance(preset, str) or preset not in library["presets"]:
        raise PackageConfigError("packaging.preset 必须是已注册方案：common")
    for field in ("enabled",):
        if field in value and not isinstance(value[field], bool):
            raise PackageConfigError(f"packaging.{field} 须为 true 或 false")
    for field, limit in (("label", 12), ("recipe", 128)):
        if field in value:
            value[field] = _text(value[field], f"packaging.{field}", limit)
    if "accent" in value:
        from g2.package_kit import ACCENT
        if not isinstance(value["accent"], str) or not ACCENT.fullmatch(value["accent"]):
            raise PackageConfigError("packaging.accent 须为 #rrggbb")
        value["accent"] = value["accent"].lower()
    for slot in SLOTS:
        if slot not in value:
            continue
        part = value[slot]
        if not isinstance(part, dict) or set(part) - SLOT_KEYS[slot]:
            raise PackageConfigError(f"packaging.{slot} 含不支持的字段")
        if "enabled" in part and not isinstance(part["enabled"], bool):
            raise PackageConfigError(f"packaging.{slot}.enabled 须为 true 或 false")
        component = part.get("component", library["presets"][preset][slot]["component"])
        if not isinstance(component, str) or library["components"].get(component, {}).get("slot") != slot:
            raise PackageConfigError(f"packaging.{slot}.component 必须引用该类别的已注册组件")
        for field, limit in (("title", 12), ("subtitle", 24), ("text", 12)):
            if field in part:
                part[field] = _text(part[field], f"packaging.{slot}.{field}", limit)
        if "lines" in part:
            lines = part["lines"]
            if not isinstance(lines, list) or not 1 <= len(lines) <= 3:
                raise PackageConfigError("packaging.outro.lines 须为 1–3 行")
            part["lines"] = [_text(line, "packaging.outro.lines", 30) for line in lines]
        if "seconds" in part:
            seconds = part["seconds"]
            if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not .5 <= seconds <= 5:
                raise PackageConfigError(f"packaging.{slot}.seconds 须在 0.5–5 之间")
    return value


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def source_props(sources: list[dict]) -> dict:
    """Text is derived exclusively from the caller's references, never model output."""
    from datetime import date
    if not isinstance(sources, list) or not 1 <= len(sources) <= 3:
        raise PackageConfigError("来源卡须有 1–3 条 sources")
    rows = []
    for item in sources:
        if not isinstance(item, dict) or set(item) != {"title", "url", "date"}:
            raise PackageConfigError("sources 每项须含 title、url、date")
        title = _text(item["title"], "sources.title", 40)
        url = item["url"]
        if not isinstance(url, str) or len(url) > 300 or any(c.isspace() or ord(c) < 32 for c in url):
            raise PackageConfigError("sources.url 无效")
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise PackageConfigError("sources.url 须为 http(s) 网址")
        try:
            if not isinstance(item["date"], str) or date.fromisoformat(item["date"]).isoformat() != item["date"]:
                raise ValueError
        except ValueError:
            raise PackageConfigError("sources.date 须为 YYYY-MM-DD 日期") from None
        host = parts.hostname.lower()
        domain = host[4:] if host.startswith("www.") else host
        if len(domain) > 80:
            raise PackageConfigError("来源卡显示域名不能超过 80 字符")
        rows.append({"title": title, "domain": domain, "date": item["date"]})
    return {"title": "来源", "sources": rows}


def resolve(selection: dict | None = None, *, sources: list[dict] | None = None) -> dict:
    selection = validate_selection(selection)
    library = catalog()
    common = copy.deepcopy(library["presets"][selection["preset"]])
    for key, value in selection.items():
        if key in SLOTS:
            common[key].update(value)
        elif key not in {"preset", "recipe"}:
            common[key] = value
    label = common["label"]
    package = {"accent": common["accent"], "badge": ""}
    enabled = common["enabled"]
    sound = common["sound"]["component"] if enabled and common["sound"]["enabled"] else None
    for slot in ("intro", "outro"):
        part = common[slot]
        if enabled and part["enabled"]:
            package[slot] = {"template": part["component"], "title": part.get("title", label), "seconds": part["seconds"]}
            for key in ("subtitle", "lines"):
                if key in part:
                    package[slot][key] = part[key]
            if sound:
                package[slot]["sound"] = sound
    badge = {"text": common["badge"].get("text", label) if enabled and common["badge"]["enabled"] else "", "color": common["accent"]}
    if badge["text"]:
        package["badge"] = badge["text"]
    else:
        package.pop("badge")
    card = None
    source_state = "disabled"
    if enabled and common["source_card"]["enabled"]:
        source_state = "no_sources"
        if sources:
            card = {"template": "source_card", "seconds": common["source_card"]["seconds"], **source_props(sources)}
            source_state = "enabled"
    record = {"library": library["library"], "version": library["version"], "catalog_sha256": _digest(library),
              "selection": selection, "components": common, "package": package if enabled else None,
              "badge": badge, "source_card": card, "source_card_state": source_state,
              "component_versions": {slot: {"id": common[slot]["component"], "version": library["components"][common[slot]["component"]]["version"]} for slot in SLOTS}}
    record["sha256"] = _digest(record)
    return record


def prepare_request(request: dict) -> dict:
    """Resolve all new-job entry points; preserve explicitly supplied legacy packages."""
    request = copy.deepcopy(request)
    if request.get("packaging_resolved") is not None:
        record = request["packaging_resolved"]
        if not isinstance(record, dict):
            raise PackageConfigError("已保存的包装配置须为对象")
        expected = record.get("sha256")
        if not expected or _digest({k: v for k, v in record.items() if k != "sha256"}) != expected:
            raise PackageConfigError("已保存的包装配置完整性校验失败")
        if record.get("package"):
            request["package"] = record["package"]
        else:
            request.pop("package", None)
        return request
    if request.get("package") is not None:
        if request.get("packaging") is not None:
            raise PackageConfigError("packaging 与旧 package 不能同时提交")
        package = validate_package(request["package"])
        selection = {"preset": "common", "accent": package["accent"],
                     "badge": {"enabled": bool(package.get("badge"))}}
        if package.get("badge"):
            selection["label"] = package["badge"]
        for slot in ("intro", "outro"):
            part = package.get(slot)
            selection[slot] = ({"enabled": True, "component": part["template"],
                                **{k: v for k, v in part.items() if k not in {"template", "sound"}}}
                               if part else {"enabled": False})
        sounds = [package[slot].get("sound") for slot in ("intro", "outro") if slot in package]
        selection["sound"] = {"enabled": any(sounds)}
        record = resolve(selection, sources=request.get("sources"))
        # Preserve the old wire format, including independent per-clip sound choices.
        record["package"] = package
        record["legacy_request"] = True
        record["sha256"] = _digest({k: v for k, v in record.items() if k != "sha256"})
        request.update(package=package, packaging_resolved=record)
        return request
    selection = validate_selection(request.get("packaging"))
    record = resolve(selection, sources=request.get("sources"))
    request.update(packaging=selection, packaging_resolved=record)
    if record["package"]:
        request["package"] = record["package"]
    else:
        request.pop("package", None)
    return request


def source_already_rendered(record: dict | None, ir: dict) -> bool:
    """A revision of an old narration-bound card must not append a second card."""
    if not record or not record.get("source_card"):
        return False
    existing = [scene["props"] for scene in ir["scenes"] if scene["component"] == "source_card"]
    expected = {k: v for k, v in record["source_card"].items() if k in {"title", "sources"}}
    if existing and any(props != expected for props in existing):
        raise PackageConfigError("旧版正文的来源卡与当前来源不同，请通过原稿或讲稿修订更新，避免生成两套来源卡")
    return bool(existing)

"""Versioned creative resources and recipes; production stays in registered executors."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

from g2.packaging_library import catalog as packaging_catalog, prepare_request as prepare_packaging

ROOT = Path(__file__).resolve().parent
DEFAULT_ASSET_ROOT = Path("/Volumes/个人资料/2026/视频生成工作流/AI创作")
EXECUTORS = {
    "information_motion": {"name": "简洁信息动效", "available": True},
    "process_diagram": {"name": "流程动态图解", "available": True},
    "kinetic_typography": {"name": "动态排版", "available": True},
    "image_slideshow": {"name": "图片轮播", "available": True},
    "chat_dialogue": {"name": "聊天对话体", "available": True},
    "information_cards": {"name": "信息卡片", "available": True},
    "object_showcase": {"name": "对象展示", "available": True},
    "dynamic_collage": {"name": "动态拼贴", "available": True},
    "knowledge_cards": {"name": "知识卡片与背景图", "available": True},
}


class CreativeResourceError(ValueError):
    pass


def digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def catalog() -> dict:
    return json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))


def recipe(recipe_id: str) -> dict:
    if not isinstance(recipe_id, str):
        raise CreativeResourceError("production_recipe 须引用已注册的制作配方")
    for item in catalog()["recipes"]:
        if item["id"] == recipe_id:
            return item
    raise CreativeResourceError("未知制作配方，请选择 AI 快讯、操作教程或原理科普")


def merge(base: dict, overrides: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in overrides.items():
        result[key] = merge(result[key], value) if isinstance(result.get(key), dict) and isinstance(value, dict) else copy.deepcopy(value)
    return result


def apply_defaults(request: dict) -> dict:
    """Only absent values inherit recipe defaults; explicit caller choices win."""
    if request.get("production_recipe") is None:
        return copy.deepcopy(request)
    defaults = copy.deepcopy(recipe(request["production_recipe"])["defaults"])
    if request.get("package") is not None:
        defaults.pop("packaging", None)  # Preserve the legacy package API.
    return merge(defaults, request)


def prepare_request(request: dict) -> dict:
    """Resolve once at the job boundary, retaining the saved definition on revisions."""
    request = copy.deepcopy(request)
    frozen = request.get("production_recipe_resolved")
    definition = None
    catalog_hash = None
    if frozen is not None:
        if (not isinstance(frozen, dict) or frozen.get("schema_version") != 1
                or frozen.get("sha256") != digest({k: v for k, v in frozen.items() if k != "sha256"})):
            raise CreativeResourceError("已保存的制作配方完整性校验失败")
        definition = frozen.get("recipe")
        if not isinstance(definition, dict) or definition.get("id") != request.get("production_recipe"):
            raise CreativeResourceError("已保存的制作配方与任务不一致")
        catalog_hash = frozen["catalog_sha256"]
    elif request.get("production_recipe") is not None:
        definition = recipe(request["production_recipe"])
        catalog_hash = digest(catalog())
        request = apply_defaults(request)
    request = prepare_packaging(request)
    from g2.music_library import selection, check_record
    if request.get("background_music") is not None:
        request["background_music"] = selection(request["background_music"])
    if request.get("background_music_resolved") is not None:
        check_record(request["background_music_resolved"])
    if definition is not None:
        selected_form = request.get("video_form")
        if selected_form not in {*EXECUTORS, "auto"}:
            raise CreativeResourceError("制作配方须使用当前已接入的表现形式")
        record = {
            "schema_version": 1, "catalog_sha256": catalog_hash, "recipe": definition,
            "selection": {key: request.get(key) for key in (
                "video_form", "duration_mode", "target_duration_sec", "voice_id", "video_speed", "creative_intent")},
            "music": {"mode": "none", "background_music_generated": False},
            "packaging_sha256": request["packaging_resolved"]["sha256"],
        }
        if request.get("background_music") is not None:
            config = request["background_music"]
            record["selection"]["background_music"] = config
            record["music"] = {"mode": "background" if config["enabled"] else "none", "background_music_generated": False}
            if config["enabled"]:
                record["music"].update(selection=config, asset_record_sha256=(request.get("background_music_resolved") or {}).get("sha256"))
                record["music"]["background_music_generated"] = bool((request.get("background_music_resolved") or {}).get("music_generated"))
        record["sha256"] = digest(record)
        request["production_recipe_resolved"] = record
    return request


def asset_root() -> Path:
    return Path(os.environ.get("AUTOVIDEO_CREATIVE_ASSET_ROOT", str(DEFAULT_ASSET_ROOT))).expanduser().resolve()


def _example_path(item: dict) -> Path | None:
    example = item.get("example")
    if not example:
        return None
    root = asset_root()
    path = root / example["relative_path"]
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or path.is_symlink() or not path.is_file():
        return None
    return path


def preview(form_id: str) -> tuple[Path, str]:
    """Serve only pinned examples; never accept a path or URL from callers."""
    item = next((item for item in catalog()["forms"] if item["id"] == form_id), None)
    path = _example_path(item) if item else None
    if path is None:
        raise FileNotFoundError("案例暂不可用，请检查创作素材所在磁盘")
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    if hasher.hexdigest() != item["example"]["sha256"]:
        raise CreativeResourceError("案例文件与收录版本不一致，未提供预览")
    return path, "video/mp4" if item["kind"] == "video" else "audio/wav"


def public_catalog() -> dict:
    from g2.music_engine import readiness as music_readiness
    data = catalog()
    for item in data["forms"]:
        if item.get("example"):
            available = _example_path(item) is not None
            item["example"] = {key: value for key, value in item["example"].items() if key != "relative_path"}
            item["example"].update(available=available, preview_url=f"/api/creative-resources/{item['id']}/preview")
    native_forms = [{"id": "AV01", "name": "信息卡片", "group": "原生信息表达", "kind": "video",
                     "description": "自动从完整讲稿提取逐句卡片，保留配音、字幕与栏目包装。",
                     "topics": ["专题速览", "知识讲解"], "structure": ["章节", "原文信息卡片", "来源与收尾"],
                     "availability": "available", "executors": ["information_cards"],
                     "capability_note": "快速制作，浅色编辑风格；使用原生合成，可搭配已登记的背景音乐。"}]
    return {**data, "native_forms": native_forms, "executors": EXECUTORS, "packaging": packaging_catalog(),
            "capabilities": {"music_generation": music_readiness()['ready'], "background_music": True}}

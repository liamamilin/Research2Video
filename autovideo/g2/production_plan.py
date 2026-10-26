"""One versioned configuration boundary for all video executors.

Public callers submit settings and optional named upstream layers, never a saved
plan. Retries verify the saved plan; revisions derive a new plan from it.
"""
from __future__ import annotations

import copy
import hashlib
import json

from g2 import creative_library

FIELDS = {
    "video_form", "production_mode", "visual_style", "duration_mode",
    "target_duration_sec", "voice_id", "video_speed", "creative_intent", "packaging", "background_music",
}
DEFAULTS = {
    "video_form": "knowledge_cards", "production_mode": "standard", "visual_style": "legacy",
    "duration_mode": "fixed", "target_duration_sec": 60, "voice_id": "zf_xiaoxiao",
    "video_speed": 1.0, "creative_intent": {},
}
STYLES = {
    "legacy": {"version": 1, "description": "保留各既有形式的原始视觉定义"},
    "editorial_light": {"version": 1, "background": "#f3f5f1", "surface": "#ffffff",
                        "text": "#182720", "muted": "#637168", "accent": "#1f7a4d",
                        "warning": "#9b650c", "subtitle": "#182720"},
}


class ProductionPlanError(ValueError):
    pass


def hybrid_enabled(request: dict) -> bool:
    """Opt in authored fast videos; historical frozen card jobs keep their executor."""
    plan = request.get("production_plan")
    return bool(request.get("production_mode") == "fast" and request.get("visual_storytelling")
                and ((plan is not None and plan.get("executor", {}).get("id") == "hybrid_cards")
                     or (plan is None and (request.get("chapters") or request.get("revision_mode") == "narration"))))


def digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def context_layers(context: dict | None) -> list[tuple[str, dict]]:
    if context is None:
        return []
    if not isinstance(context, dict) or set(context) - {"content_recipe", "matrix"}:
        raise ProductionPlanError("production_context 只接受 content_recipe 和 matrix")
    result = []
    for name in ("content_recipe", "matrix"):
        if name not in context:
            continue
        layer = context[name]
        if (not isinstance(layer, dict) or set(layer) - {"id", "version", "settings"}
                or not isinstance(layer.get("id"), str) or not 1 <= len(layer["id"]) <= 160
                or not isinstance(layer.get("settings", {}), dict)
                or set(layer.get("settings", {})) - FIELDS):
            raise ProductionPlanError(f"production_context.{name} 须含有效 id 和受支持的 settings")
        if "version" in layer and (not isinstance(layer["version"], (str, int)) or isinstance(layer["version"], bool)):
            raise ProductionPlanError(f"production_context.{name}.version 无效")
        result.append((name, layer))
    return result


def _origins(value: dict, scope: str, prefix: str = "") -> dict:
    result = {}
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(item, dict) and item:
            result.update(_origins(item, scope, path))
        else:
            result[path] = scope
    return result


def resolve_defaults(request: dict) -> tuple[dict, dict]:
    """Resolve before Pydantic inserts defaults, retaining explicit false/empty values."""
    submitted = copy.deepcopy(request)
    result = copy.deepcopy(DEFAULTS)
    origins = _origins(result, "common")
    definition = None
    if submitted.get("production_recipe") is not None:
        definition = creative_library.recipe(submitted["production_recipe"])
        defaults = copy.deepcopy(definition["defaults"])
        if submitted.get("package") is not None:
            defaults.pop("packaging", None)
        result = creative_library.merge(result, defaults)
        origins.update(_origins(defaults, "production_recipe"))
    explicit = {}
    for name, layer in context_layers(submitted.get("production_context")):
        settings = layer.get("settings", {})
        result = creative_library.merge(result, settings)
        explicit = creative_library.merge(explicit, settings)
        origins.update(_origins(settings, name))
    choices = {key: value for key, value in submitted.items() if key in FIELDS}
    explicit = creative_library.merge(explicit, choices)
    result = creative_library.merge(result, submitted)
    origins.update(_origins(choices, "request"))
    # A mode-only request chooses the first supported fast form. An explicitly
    # chosen incompatible form is an error, never silently converted to cards.
    if result.get("production_mode") == "fast":
        if "video_form" not in explicit or result.get("video_form") == "auto":
            result["video_form"] = "information_cards"
            origins["video_form"] = "fast_capability_selection"
        if "visual_style" not in explicit:
            result["visual_style"] = "editorial_light"
            origins["visual_style"] = "fast_capability_selection"
    for key in ("production_mode", "visual_style", "video_form"):
        if result.get(key) is None:
            raise ProductionPlanError(f"{key} 不能为 null；省略表示继承")
    validate_capability(result)
    return result, origins


def validate_capability(request: dict) -> None:
    mode, form, style = (request.get(key) for key in ("production_mode", "video_form", "visual_style"))
    if not isinstance(mode, str) or not isinstance(style, str) or mode not in {"standard", "fast"} or style not in STYLES:
        raise ProductionPlanError("未知制作档位或视觉风格")
    if mode == "fast":
        if form != "information_cards" or style != "editorial_light":
            raise ProductionPlanError("快速制作当前支持信息卡片与浅色编辑风格；请调整形式或选用标准制作")
        # Both executors feed the same post-QA music stage. Real fast-card mix
        # evidence: g3/acceptance/music-mix-20261023 (5c28e618).
        mapped_images = hybrid_enabled(request) and request.get("image_materials") and all(
            row.get("placements") for row in request["image_materials"])
        if request.get("use_draw_things") or (request.get("slideshow_images") and not mapped_images):
            raise ProductionPlanError("快速卡片本阶段使用受控文字排版，不生成或接收轮播图片")
    elif form == "information_cards" or style != "legacy":
        raise ProductionPlanError("信息卡片与浅色编辑风格本阶段须选择快速制作")


def _binding(request: dict) -> dict:
    # Older plans predate background music. Keep their binding byte compatible.
    # Keep the original null music placeholder: already saved v1 plans bind it.
    fields = FIELDS | {"production_recipe", "production_context", "content_notices", "music"}
    if request.get("video_form") in {"object_showcase", "dynamic_collage"}:
        fields |= {"slideshow_images", "slideshow_images_resolved", "image_materials"}
    if hybrid_enabled(request):
        fields |= {"visual_storytelling", "slideshow_images", "slideshow_images_resolved", "image_materials"}
    if "background_music" not in request:
        fields = fields - {"background_music"}
    return {key: copy.deepcopy(request.get(key)) for key in sorted(fields)}


def verify(request: dict) -> dict:
    plan = request.get("production_plan")
    if (not isinstance(plan, dict) or plan.get("schema_version") != 1
            or plan.get("sha256") != digest({k: v for k, v in plan.items() if k != "sha256"})):
        raise ProductionPlanError("制作计划缺失或完整性校验失败")
    bound = _binding(request)
    # Early v1 plans omitted the reserved null music key. Accept that exact
    # saved layout; real background_music settings still participate in
    # equality and cannot be added or changed on a frozen task.
    saved = plan.get("settings")
    if (isinstance(saved, dict) and "music" not in saved
            and request.get("music") is None):
        bound.pop("music", None)
    if saved != bound:
        raise ProductionPlanError("任务配置与冻结的制作计划不一致")
    if plan.get("packaging_sha256") != (request.get("packaging_resolved") or {}).get("sha256"):
        raise ProductionPlanError("包装与冻结的制作计划不一致")
    source_hash = (hashlib.sha256(request["source_text"].encode()).hexdigest()
                   if "source_text" in request else request.get("source_sha256"))
    if source_hash and plan.get("source_sha256") != source_hash:
        raise ProductionPlanError("文稿与冻结的制作计划不一致")
    from g2.packaging_library import prepare_request as prepare_packaging
    from g2.package_kit import PackageConfigError
    try:
        checked = prepare_packaging(request)
        if checked.get("package") != request.get("package"):
            raise ProductionPlanError("包装参数与冻结的制作计划不一致")
    except PackageConfigError as exc:
        raise ProductionPlanError(str(exc)) from exc
    if plan.get("recipe_sha256") != (request.get("production_recipe_resolved") or {}).get("sha256"):
        raise ProductionPlanError("制作配方与冻结的制作计划不一致")
    if plan.get("music_record_sha256") != (request.get("background_music_resolved") or {}).get("sha256"):
        raise ProductionPlanError("配乐与冻结的制作计划不一致")
    validate_capability(request)
    return copy.deepcopy(plan)


def prepare_request(request: dict) -> dict:
    """Freeze new jobs and derive revisions while retaining existing library snapshots."""
    result = copy.deepcopy(request)
    parent = result.pop("production_plan", None)
    revision = result.get("operation_type") in {"content_revision", "visual_revision", "media_revision", "scene_edit", "music_revision"}
    if parent is not None and not revision:
        result["production_plan"] = parent
        verify(result)
        return result
    if parent is not None:
        if parent.get("sha256") != digest({k: v for k, v in parent.items() if k != "sha256"}):
            raise ProductionPlanError("父版本制作计划完整性校验失败")
        origins = copy.deepcopy(parent["origins"])
        for key in FIELDS:
            if result.get(key) != parent["settings"].get(key):
                origins.update(_origins({key: result.get(key)}, "revision"))
        if result.get('operation_type') != 'content_revision':
            # Media/music revisions reuse the parent's scenes without carrying
            # authored chapters. Keep the parent's executor during resolution.
            result['production_plan'] = parent
    else:
        original = result.pop("_production_submitted", None)
        if original is not None:
            _, origins = resolve_defaults(original)
        else:
            result, origins = resolve_defaults(result)
    result = creative_library.prepare_request(result)
    validate_capability(result)
    common_origins = _origins({"packaging": result["packaging_resolved"]["components"]}, "common")
    origins = {**common_origins, **origins}
    style = {"id": result["visual_style"], **copy.deepcopy(STYLES[result["visual_style"]])}
    if result["production_mode"] == "fast":
        style["accent"] = result["packaging_resolved"]["components"]["accent"]
        origins["visual_style.accent"] = "packaging.accent"
    plan = {
        "schema_version": 1, "settings": _binding(result), "origins": origins,
        "style": style,
        "executor": {"id": ("hybrid_cards" if hybrid_enabled(result) else "ffmpeg_cards")
                     if result["production_mode"] == "fast" else "remotion", "version": 1},
        "output": {"width": 1920, "height": 1080, "fps": 30},
        "packaging_sha256": result["packaging_resolved"]["sha256"],
        "packaging": copy.deepcopy(result["packaging_resolved"]),
        "recipe_sha256": (result.get("production_recipe_resolved") or {}).get("sha256"),
        "source_sha256": hashlib.sha256(result["source_text"].encode()).hexdigest(),
        "music": copy.deepcopy((result.get("production_recipe_resolved") or {}).get("music")) or {"selection": copy.deepcopy(result.get("background_music")), "background_music_generated": False},
    }
    if parent:
        plan["parent_sha256"] = parent["sha256"]
    if result.get("background_music_resolved") is not None:
        plan["music_record_sha256"] = result["background_music_resolved"]["sha256"]
        plan["music"] = {"mode": "background" if result["background_music"]["enabled"] else "none",
                         "selection": copy.deepcopy(result["background_music"]),
                         "background_music_generated": bool(result["background_music_resolved"].get("music_generated"))}
    plan["sha256"] = digest(plan)
    result["production_plan"] = plan
    return result


def capabilities() -> dict:
    return {"schema_version": 1, "modes": ["standard", "fast"], "styles": copy.deepcopy(STYLES),
            "fast": {"forms": ["information_cards"], "styles": ["editorial_light"],
                     "executor": "ffmpeg_cards", "background_music": True,
                     "visual_storytelling": {"executor": "hybrid_cards", "input_mode": "narration",
                         "mapped_images": True, "automatic_scene_selection": True}}}

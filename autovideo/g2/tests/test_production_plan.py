import copy

import pytest

from g2 import creative_library
from g2.production_plan import ProductionPlanError, digest, prepare_request, resolve_defaults, verify


def test_layer_priority_and_explicit_off_with_field_provenance():
    resolved, origins = resolve_defaults({
        "production_recipe": "ai_news",
        "production_context": {
            "content_recipe": {"id": "research:topic_digest", "settings": {"voice_id": "zm_yunxi", "packaging": {"sound": {"enabled": True}}}},
            "matrix": {"id": "tech", "settings": {"voice_id": "zf_xiaoxiao", "packaging": {"sound": {"enabled": False}}}},
        },
        "creative_intent": {"tone": ""}, "packaging": {"outro": {"enabled": False}},
    })
    assert resolved["voice_id"] == "zf_xiaoxiao"
    assert resolved["packaging"]["sound"]["enabled"] is False
    assert resolved["packaging"]["outro"]["enabled"] is False
    assert resolved["creative_intent"]["tone"] == ""
    assert origins["voice_id"] == "matrix"
    assert origins["packaging.sound.enabled"] == "matrix"
    assert origins["packaging.outro.enabled"] == "request"


def test_fast_mode_only_chooses_compatible_form_but_explicit_form_is_not_replaced():
    value, origins = resolve_defaults({"production_recipe": "ai_news", "production_mode": "fast"})
    assert value["video_form"] == "information_cards"
    assert value["visual_style"] == "editorial_light"
    assert origins["video_form"] == "fast_capability_selection"
    with pytest.raises(ProductionPlanError):
        resolve_defaults({"production_mode": "fast", "video_form": "process_diagram"})


@pytest.mark.parametrize("extra", [
    {"production_mode": None}, {"visual_style": []}, {"production_mode": []},
    {"production_context": {"typo": {}}},
    {"production_context": {"matrix": {"id": "tech", "settings": {"unknown": True}}}},
    {"production_mode": "fast", "use_draw_things": True},
])
def test_invalid_configuration_fails_before_execution(extra):
    with pytest.raises((ProductionPlanError, ValueError)):
        resolve_defaults(extra)


def test_saved_configuration_is_frozen_and_revisions_keep_definitions(monkeypatch):
    parent = prepare_request({"production_recipe": "ai_news", "source_text": "原文保留条件。"})
    original = copy.deepcopy(parent)
    monkeypatch.setattr(creative_library, "catalog", lambda: {"recipes": []})
    assert prepare_request(parent) == original
    revised = prepare_request({**parent, "operation_type": "media_revision", "voice_id": "zm_yunxi"})
    assert revised["production_plan"]["parent_sha256"] == parent["production_plan"]["sha256"]
    assert revised["production_plan"]["origins"]["voice_id"] == "revision"
    assert revised["production_recipe_resolved"]["recipe"] == parent["production_recipe_resolved"]["recipe"]
    assert verify(revised) == revised["production_plan"]
    assert parent == original


@pytest.mark.parametrize("change", ["voice", "source", "packaging", "plan"])
def test_tampered_plan_input_or_packaging_is_rejected(change):
    value = prepare_request({"source_text": "必须保留的定稿。"})
    if change == "voice":
        value["voice_id"] = "zm_yunxi"
    elif change == "source":
        value["source_text"] += "擅自新增。"
    elif change == "packaging":
        value["packaging_resolved"]["badge"]["text"] = "被修改"
    else:
        value["production_plan"]["executor"]["id"] = "untrusted"
    with pytest.raises(ProductionPlanError):
        verify(value)


@pytest.mark.parametrize("layout", ["core_only", "reserved_music", "background_only", "both"])
def test_saved_v1_music_binding_layouts_remain_valid_and_reject_changes(layout):
    request = {"source_text": "已经保存的任务不能因新增配置而失效。"}
    if layout in {"background_only", "both"}:
        request["background_music"] = {"enabled": False, "level_db": -18}
    value = prepare_request(request)
    if layout in {"core_only", "background_only"}:
        plan = value["production_plan"]
        plan["settings"].pop("music")
        plan["sha256"] = digest({k: v for k, v in plan.items() if k != "sha256"})
    original = copy.deepcopy(value)
    assert verify(value) == original["production_plan"]
    assert prepare_request(value) == original
    value["background_music"] = {"enabled": True, "level_db": -18}
    with pytest.raises(ProductionPlanError, match="任务配置"):
        verify(value)


def test_brand_content_palette_is_opt_in_and_frozen_separately_from_packaging(tmp_path):
    from g3 import brand_profiles
    from g2.branding import BrandError
    data = tmp_path / "data"
    config = {"name": "Field Notes", "background": "#faf3e8", "text": "#242424", "accent": "#693c81"}
    brand_profiles.save(data, "studio", config)

    def make(name):
        folder = tmp_path / name; folder.mkdir()
        request = brand_profiles.freeze(data, folder, {"source_text": "先上传材料，再查看结果。",
            "production_mode": "fast", "brand": {"profile": "studio"}})
        return prepare_request(request)

    historical = make("old")
    assert "palette_source" not in historical["production_plan"]["style"]
    brand_profiles.save(data, "studio", {**config, "apply_to_content": True})
    saved = make("new")
    style = verify(saved)["style"]
    assert style["background"] == config["background"]
    assert style["subtitle"] == style["text"] == config["text"]
    assert style["accent"] == config["accent"]
    assert saved["production_plan"]["origins"]["visual_style.background"] == "brand"
    header = saved["production_plan"]["brand_layout"]
    assert header["left"] == header["right"] == 80
    assert 1920-header["right"]-header["width"]-header["gap"] < 1920-header["right"]-header["width"]
    brand_profiles.save(data, "studio", {**config, "background": "#eeeeee", "apply_to_content": False})
    assert prepare_request(saved) == saved
    assert prepare_request(historical) == historical
    with pytest.raises(BrandError):
        brand_profiles.save(data, "studio", {**config, "apply_to_content": "yes"})

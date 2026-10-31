"""Production failures: adapt presentation while preserving the supplied content."""
from copy import deepcopy
import json

import pytest

from g1.price_layout import price_layout
from g1.tests.test_video_ir_v0_2_runtime import base_ir, make_root
from g1.video_ir import validate_video_ir, ValidationFault
from g2 import explanation, information_motion, visual_language


@pytest.mark.parametrize("before,after,change", [("55.95", "61.2", "5.25"), ("23.4", "8.3", "15.1"),
                                                   ("218900", "230000", "11100"),
                                                   ("1234567890", "2345678901", "11111111")])
@pytest.mark.parametrize("large", [False, True])
def test_saved_numeric_failures_fit_without_rewriting_values(before, after, change, large, tmp_path):
    props = {"title": "变化", "unit": "%", "staged": True,
             "rows": [{"label": "指标", "before": before, "after": after, "change": change, "direction": "up"}]}
    original = deepcopy(props)
    information_motion.validate_text_fit({"id": "card_2_p1", "component": "price_change", "props": props}, large)
    root = make_root(tmp_path)
    ir = base_ir(root)
    ir["presentation"] = {"preset": "explanation", **({"density": "large"} if large else {})}
    ir["style"]["preset"] = "minimal_light"
    ir["scenes"][0].update(component="price_change", props=props)
    ir["scenes"][0]["beats"][0]["target_id"] = "line:0"
    compiled = validate_video_ir(ir, root)
    geometry = compiled["scenes"][0]["price_layout"]
    assert geometry == price_layout(props, large)
    assert geometry["number_size"] >= 88 and geometry["height"] <= 440
    assert compiled["scenes"][0]["props"] == props == original
    assert "price_layout" not in ir["scenes"][0]  # derived output, not a new model field


def test_common_short_numbers_keep_the_existing_large_treatment():
    assert price_layout({"rows": [{"label": "订阅", "before": "20", "after": "25", "change": "5", "direction": "up"}]})["number_size"] == 180


def test_repeated_icons_keep_the_first_complete_object_and_action():
    props = {"title": "上下文", "lines": ["延迟和成本由你承担"], "visuals": [
        {"target": "line:0", "icon": "clock", "action": "process", "phrase": "塞满之后"},
        {"target": "line:0", "icon": "cost", "phrase": "成本由你承担"}]}
    original = deepcopy(props)
    fixed = visual_language.enrich("concept_reveal", props)
    assert fixed["visuals"] == [original["visuals"][0]]
    assert fixed["lines"] == original["lines"] and props == original
    assert visual_language.enrich("concept_reveal", fixed) == fixed
    explanation.validate({"pages": [{"id": "one", "component": "concept_reveal", "props": fixed}]},
                         {"groups": [{"id": "one"}]})


def test_missing_icon_reference_is_not_confused_with_a_duplicate(tmp_path):
    root = make_root(tmp_path)
    ir = base_ir(root)
    ir["presentation"] = {"preset": "explanation"}
    ir["style"]["preset"] = "minimal_light"
    props = {"title": "文件", "lines": ["文件处理"],
             "visuals": [{"target": "line:3", "icon": "file"}, {"target": "line:3", "icon": "cost"}]}
    ir["scenes"][0].update(component="concept_reveal", props=visual_language.enrich("concept_reveal", props))
    ir["scenes"][0]["beats"][0]["target_id"] = "line:0"
    with pytest.raises(ValidationFault, match="VISUAL_OBJECT_REFERENCE"):
        validate_video_ir(ir, root)


def test_planner_receives_the_actual_five_node_layout_limit():
    payload = json.loads(explanation.messages({"title": "关系", "groups": []})[1]["content"])
    assert payload["component_props_schemas"]["flow_diagram"]["properties"]["nodes"]["maxItems"] == 5


def test_numeric_cues_allow_new_before_old_and_a_spoken_delta_in_words():
    text = "K3.1 Pro 是61.2%，K3 是55.95%，涨了五个多百分点。"
    scene = {"component": "price_change", "props": {"staged": True, "rows": [
        {"label": "得分", "before": "55.95", "after": "61.2", "change": "5.25", "direction": "up"}]},
        "narration_segment_ids": ["n1"]}
    cues = information_motion.automatic_cues(scene, {"n1": {"id": "n1", "text": text, "start_ms": 0, "end_ms": 8000}})
    by_target = {c["target_id"]: c for c in cues}
    assert by_target["line:0"]["character_index"] == text.index("55.95")
    assert by_target["line:1"]["character_index"] == text.index("61.2")
    assert by_target["line:2"]["character_index"] == text.index("涨了")


def test_numeric_cues_match_thousands_separators_without_short_number_substrings():
    text = "住房净值从218,900美元变成230,000美元，增加11,100美元。"
    scene = {"component": "price_change", "props": {"staged": True, "rows": [
        {"label": "住房净值", "before": "218900", "after": "230000", "change": "11100", "direction": "up"}]},
        "narration_segment_ids": ["n1"]}
    cues = information_motion.automatic_cues(scene, {"n1": {"id": "n1", "text": text, "start_ms": 0, "end_ms": 8000}})
    assert [c["quote"] for c in cues[1:]] == ["218,900", "230,000", "11,100"]

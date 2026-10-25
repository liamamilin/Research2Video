import copy

import pytest

from g2.visual_narrative import compile_plan, validate_plan, merge_scene_repair, validate_scene_repair
from g2.run_g2 import G2Fault


def context():
    return {"source_text": "月球会影响海水。地球也在自转。", "narration_plan": {"segments": [
        {"id": "n1", "text": "月球会影响海水。"}, {"id": "n2", "text": "地球也在自转。"}]}}


def plan():
    return {"scenes": [{"id": "mechanism", "title": "机制示意", "takeaway": "月球会影响海水",
        "source_quotes": ["月球会影响海水"], "narration_segment_ids": ["n1", "n2"], "objects": [
            {"id": "earth", "kind": "sphere", "surface": "earth", "x": 50, "y": 50, "width": 20,
             "motion": {"type": "rotate", "degrees": 360}},
            {"id": "coast", "kind": "marker", "parent_id": "earth", "x": 10, "y": 0, "width": 1}]}]}


def test_accepts_grouped_object_movement_without_executable_model_content():
    validate_plan(plan(), context())


@pytest.mark.parametrize("change,code", [
    (lambda p: p["scenes"][0]["objects"][1].update(parent_id="missing"), "MISSING_VISUAL_PARENT"),
    (lambda p: p["scenes"][0]["objects"][0].update(parent_id="coast"), "VISUAL_PARENT_CYCLE"),
    (lambda p: p["scenes"][0].update(narration_segment_ids=["n2", "n1"]), "VISUAL_COVERAGE"),
    (lambda p: p["scenes"][0].update(source_quotes=["月球只在近侧拉动海水"]), "UNSUPPORTED_VISUAL_QUOTE"),
    (lambda p: p["scenes"][0]["objects"][1].update(id="earth"), "DUPLICATE_VISUAL_OBJECT"),
    (lambda p: p["scenes"][0]["objects"][0].update(motion={"type": "rotate", "start": .5, "end": .1}), "VISUAL_MOTION_WINDOW"),
    (lambda p: p["scenes"][0]["objects"][0].update(motion={"type": "orbit"}), "VISUAL_ORBIT_CENTER"),
    (lambda p: p["scenes"][0]["objects"][0].update(label="javascript:alert(1)"), "UNSAFE_VISUAL_TEXT"),
    (lambda p: p["scenes"][0]["objects"][0].update(code="fetch('/secret')"), "INVALID_VISUAL_PLAN"),
])
def test_rejects_invalid_references_coverage_or_executable_content(change, code):
    value = plan()
    change(value)
    if code == "UNSUPPORTED_VISUAL_QUOTE":
        validate_plan(value, context())
        return
    with pytest.raises(G2Fault, match=code):
        validate_plan(value, context())


def test_compiles_speech_boundaries_to_full_contiguous_frame_timeline():
    value = plan()
    second = copy.deepcopy(value["scenes"][0])
    value["scenes"][0]["narration_segment_ids"] = ["n1"]
    second.update(id="rotation", narration_segment_ids=["n2"])
    value["scenes"].append(second)
    alignment = {"segments": [{"id": "n1", "start_ms": 0, "end_ms": 100},
                              {"id": "n2", "start_ms": 200, "end_ms": 900}],
                 "duration_ms": 1000, "duration_frames": 30}
    result = compile_plan(value, alignment)
    assert result["scenes"][0]["end_frame"] == 5
    assert result["scenes"][1]["start_frame"] == 5
    assert result["scenes"][1]["end_frame"] == result["total_frames"] == 30


def test_safe_mixed_case_ids_preserve_exact_parent_references():
    value = plan()
    value["scenes"][0]["objects"][0]["id"] = "Earth"
    value["scenes"][0]["objects"][1]["parent_id"] = "Earth"
    validate_plan(value, context())


def test_overall_vertical_level_motion_is_supported_as_data():
    value = plan()
    value["scenes"][0]["objects"][0]["motion"] = {"type": "oscillate", "dy": 10, "cycles": 1}
    validate_plan(value, context())


def test_scene_repair_preserves_other_scenes_and_speech_coverage():
    original = plan()
    other = copy.deepcopy(original["scenes"][0])
    other.update(id="other", narration_segment_ids=["n3"])
    original["scenes"].append(other)
    repaired = {"scenes": [copy.deepcopy(original["scenes"][0])]}
    repaired["scenes"][0]["title"] = "修订后的说明"
    result = merge_scene_repair(original, "mechanism", repaired)
    assert result["scenes"][1] == original["scenes"][1]
    assert original["scenes"][0]["title"] == "机制示意"
    repaired["scenes"][0]["narration_segment_ids"] = ["n2"]
    with pytest.raises(G2Fault, match="VISUAL_REPAIR_COVERAGE"):
        merge_scene_repair(original, "mechanism", repaired)


def test_scene_repair_cannot_replace_a_different_scene():
    value = plan()
    ctx = {**context(), "previous_visual_scene": copy.deepcopy(value["scenes"][0])}
    value["scenes"][0]["id"] = "wrong"
    with pytest.raises(G2Fault, match="VISUAL_REPAIR_SCOPE"):
        validate_scene_repair(value, ctx)

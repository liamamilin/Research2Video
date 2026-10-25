import copy

import pytest

from g2 import content_video as content
from g1.video_ir import ValidationFault, validate_video_ir
from g1.tests.test_video_ir_v0_2_runtime import base_ir, make_root
from g2.tests.test_process_diagram import actual_layout, example


def outline_case():
    source = "读者先提交借书申请。\n\n有库存时保留图书。没有库存时进入候补。"
    context = {"units": content.source_units(source)}
    plan = {"title": "借书流程", "core_message": "根据库存分流借书申请", "chapters": [
        {"id": "c1", "title": "提交申请", "question": "如何开始？", "source_unit_ids": ["u1"],
         "points": [{"id": "p1", "point": "读者先申请", "source_quotes": ["读者先提交借书申请。"]}]},
        {"id": "c2", "title": "库存分流", "question": "有书和无书怎么办？", "source_unit_ids": ["u2", "u3"],
         "points": [{"id": "p1", "point": "有库存则保留", "source_quotes": ["有库存时保留图书。"]},
                    {"id": "p2", "point": "没有库存则候补", "source_quotes": ["没有库存时进入候补。"]}]}]}
    return source, context, plan


def test_source_units_preserve_original_spelling_and_whitespace():
    source, context, plan = outline_case()
    assert "".join(u["text"] + u["separator"] for u in context["units"]) == source
    assert content.chapter_source(plan["chapters"][1], context["units"]) == "有库存时保留图书。没有库存时进入候补。"
    content.validate_outline(plan, context)


@pytest.mark.parametrize("change,code", [
    (lambda p: p["chapters"][1]["source_unit_ids"].pop(), "CONTENT_SOURCE_COVERAGE"),
    (lambda p: p["chapters"][1]["source_unit_ids"].append("u1"), "CONTENT_SOURCE_COVERAGE"),
    (lambda p: p["chapters"].reverse(), "CONTENT_CHAPTER_ORDER"),
    (lambda p: p["chapters"][0]["points"][0].update(source_quotes=["没有库存时进入候补。"]), "CONTENT_SOURCE_QUOTE"),
])
def test_outline_rejects_omission_duplication_reordering_and_wrong_provenance(change, code):
    _, context, plan = outline_case()
    change(plan)
    if code in {'CONTENT_SOURCE_COVERAGE', 'CONTENT_SOURCE_QUOTE'}:
        content.validate_outline(plan, context)
        return
    with pytest.raises(content.ContentFault) as exc:
        content.validate_outline(plan, context)
    assert exc.value.code == code


def narration_case():
    _, context, outline = outline_case()
    chapter = outline["chapters"][1]
    ctx = {"chapter": chapter, "source_text": content.chapter_source(chapter, context["units"])}
    plan = {"segments": [
        {"id": "n1", "text": "有库存的时候，系统就为申请保留图书。", "point_ids": ["p1"], "source_quotes": ["有库存时保留图书。"]},
        {"id": "n2", "text": "如果没有库存，申请就进入候补队列。", "point_ids": ["p2"], "source_quotes": ["没有库存时进入候补。"]}]}
    return ctx, plan


def test_narration_rejects_missing_points_and_does_not_require_four_to_six_segments():
    context, plan = narration_case()
    content.validate_narration(plan, context)
    plan["segments"].pop()
    content.validate_narration(plan, context)


def test_narration_rejects_repeated_paragraphs_and_cross_chapter_points():
    context, plan = narration_case()
    plan["segments"][1]["text"] = plan["segments"][0]["text"]
    content.validate_narration(plan, context)
    plan["segments"][1]["point_ids"] = ["p9"]
    with pytest.raises(content.ContentFault, match="CONTENT_POINT_REFERENCE"):
        content.validate_narration(plan, context)


def content_ir(tmp_path):
    root = make_root(tmp_path)
    ir = base_ir(root)
    graph, _ = example()
    ir["scenes"][0]["props"] = {"nodes": [{"id": n["id"], "label": n["label"]} for n in graph["scenes"][0]["nodes"]],
        "edges": [{k: e[k] for k in ["id", "from", "to", "label"]} for e in graph["scenes"][0]["edges"]],
        "paths": [], "title": "缓存为何更快", "takeaway": "命中后返回", "layout": actual_layout(tmp_path, graph)["scenes"][0]["layout"]}
    ir["scenes"][0]["beats"][0]["target_id"] = "node:request"
    ir["video"]["duration_ms"] = 240000
    ir["scenes"][0]["duration_ms"] = 240000
    ir["content_structure"] = {"mode": "content", "chapters": [{"id": "c1", "title": "缓存", "narration_segment_ids": ["n1"]}]}
    return root, ir


def test_long_timeline_requires_explicit_valid_chapter_extension(tmp_path):
    root, ir = content_ir(tmp_path)
    result = validate_video_ir(ir, root)
    assert result["total_frames"] == 7200
    assert result["content_structure"] == ir["content_structure"]
    legacy = copy.deepcopy(ir)
    legacy.pop("content_structure")
    with pytest.raises(ValidationFault, match="SCHEMA_VALIDATION"):
        validate_video_ir(legacy, root)
    ir["content_structure"]["chapters"][0]["narration_segment_ids"] = ["missing"]
    with pytest.raises(ValidationFault, match="CONTENT_NARRATION_COVERAGE"):
        validate_video_ir(ir, root)


def test_long_timeline_cannot_bypass_component_validation(tmp_path):
    root, ir = content_ir(tmp_path)
    ir["scenes"][0]["component"] = "title_card"
    with pytest.raises(ValidationFault, match="CONTENT_COMPONENT_UNSUPPORTED"):
        validate_video_ir(ir, root)


def test_cue_endpoints_can_be_derived_without_changing_graph_content():
    from g2 import process_diagram as diagram
    plan, context = example()
    original_edges = copy.deepcopy(plan["scenes"][0]["edges"])
    plan["scenes"][0]["cues"][0]["node_ids"] = ["request"]
    diagram.complete_cue_endpoints(plan)
    diagram.validate_plan(plan, context)
    assert plan["scenes"][0]["edges"] == original_edges
    plan["scenes"][0]["cues"][0]["edge_ids"] = ["missing"]
    diagram.complete_cue_endpoints(plan)
    with pytest.raises(diagram.DiagramPlanFault):
        diagram.validate_plan(plan, context)


def test_retry_reuses_only_identical_input_and_revalidates_prior_output(tmp_path, monkeypatch):
    import json
    from g2 import run_g2 as pipeline
    _, context, plan = outline_case()
    attempts = tmp_path / "jobs/job/attempts"
    previous = attempts / "1/g2-run"
    current = attempts / "2/g2-run"
    requests = previous / "artifacts/llm_calls"
    requests.mkdir(parents=True)
    plan_path = previous / "artifacts/content-outline-plan.json"
    plan_path.write_text(json.dumps(plan))
    request_path = requests / "content-outline-attempt-1.request.json"
    request_path.write_text(json.dumps({"messages": content.outline_messages(context)}))
    monkeypatch.setattr(pipeline, "PROJECT", tmp_path)
    monkeypatch.setattr(pipeline, "RUN_ROOT", current)
    monkeypatch.setattr(pipeline, "ARTIFACTS", current / "artifacts")
    monkeypatch.setattr(pipeline, "CONTENT_MODE", True)
    monkeypatch.setenv("AUTOVIDEO_G2_PREVIOUS_RUN_DIR", str(previous))
    reused = pipeline.reuse_validated_plan("content-outline", content.outline_messages, content.validate_outline, context)
    assert reused == plan
    evidence = json.loads((current / "artifacts/reused-plans.json").read_text())
    assert evidence["plans"][0]["new_api_calls"] == 0
    modified = copy.deepcopy(context)
    modified["units"][0]["text"] = "不相同的素材"
    assert pipeline.reuse_validated_plan("content-outline", content.outline_messages, content.validate_outline, modified) is None
    plan["chapters"][0]["source_unit_ids"] = ["u99"]
    plan_path.write_text(json.dumps(plan))
    assert pipeline.reuse_validated_plan("content-outline", content.outline_messages, content.validate_outline, context) is None


def test_diagram_identifier_length_matches_registered_component_contract(tmp_path):
    from g2 import process_diagram as diagram
    plan, context = example()
    eid = 'e_confirmation_attendance'
    plan["scenes"][0]["edges"][0]["id"] = eid
    plan["scenes"][0]["cues"][0]["edge_ids"] = [eid]
    diagram.validate_plan(plan, context)
    layout = actual_layout(tmp_path, plan)
    assert eid in {edge["id"] for edge in layout["scenes"][0]["layout"]["edges"]}

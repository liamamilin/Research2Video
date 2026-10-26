from __future__ import annotations

import copy
import json
import shutil
import subprocess

import pytest

from g2 import process_diagram as diagram
from g1.video_ir import ValidationFault, validate_video_ir
from g1.tests.test_video_ir_v0_2_runtime import base_ir, make_root


def example():
    source = "请求先查询缓存。命中缓存就返回数据。"
    narration = {"segments": [{"id": "n1", "text": "请求先查询缓存。"},
                               {"id": "n2", "text": "命中缓存就返回数据。"}]}
    plan = {"unsupported_reason": None, "scenes": [{"id": "lookup", "title": "缓存为何更快", "takeaway": "命中缓存就返回数据",
        "narration_segment_ids": ["n1", "n2"],
        "nodes": [{"id": "request", "label": "请求", "source_quote": "请求先查询缓存"},
                  {"id": "cache", "label": "缓存", "source_quote": "请求先查询缓存"},
                  {"id": "result", "label": "返回数据", "source_quote": "命中缓存就返回数据"}],
        "edges": [{"id": "lookup", "from": "request", "to": "cache", "label": "", "source_quote": "请求先查询缓存"},
                  {"id": "hit", "from": "cache", "to": "result", "label": "命中", "source_quote": "命中缓存就返回数据"}],
        "cues": [{"segment_id": "n1", "quote": "请求先查询缓存", "node_ids": ["request", "cache"], "edge_ids": ["lookup"]},
                 {"segment_id": "n2", "quote": "命中缓存就返回数据", "node_ids": ["cache", "result"], "edge_ids": ["hit"]}]}]}
    return plan, {"source_text": source, "narration_plan": narration}


def actual_layout(tmp_path, plan):
    request = tmp_path / "request.json"
    request.write_text(json.dumps(diagram.layout_request(plan), ensure_ascii=False))
    result = subprocess.run([shutil.which("node"), str(diagram.LAYOUT_SCRIPT), str(request)],
                            capture_output=True, text=True, check=True, timeout=20)
    return json.loads(result.stdout)


def test_mixed_flow_layout_accepts_edges_without_optional_labels():
    plan, _ = example()
    del plan['scenes'][0]['edges'][0]['label']
    edge = diagram.layout_request(plan)['scenes'][0]['edges'][0]
    assert edge['label'] == '' and edge['label_width'] == 24


def test_source_grounded_graph_layout_and_registered_ir(tmp_path):
    plan, context = example()
    diagram.validate_plan(plan, context)
    layout = actual_layout(tmp_path, plan)
    alignment = {"rows": [{"id": "n1", "text": context["narration_plan"]["segments"][0]["text"], "start_ms": 100, "end_ms": 450},
                          {"id": "n2", "text": context["narration_plan"]["segments"][1]["text"], "start_ms": 500, "end_ms": 900}]}
    board = diagram.storyboard_from_layout(plan, layout, alignment)
    root = make_root(tmp_path)
    ir = base_ir(root)
    ir["scenes"][0].update(board["scenes"][0])
    ir["narration"]["segments"] = [{k: row[k] for k in ["id", "text", "start_ms", "end_ms"]} for row in alignment["rows"]]
    compiled = validate_video_ir(ir, root)
    assert compiled["total_frames"] == 30
    assert ir["scenes"][0]["props"]["title"] == "缓存为何更快"
    assert len(compiled["beats"]) == 12
    assert layout["scenes"][0]["layout"]["engine"] == "elkjs-0.12.0"
    assert all(node["x"] >= 0 for node in layout["scenes"][0]["layout"]["nodes"])


@pytest.mark.parametrize("change,code", [
    (lambda p: p["scenes"][0]["nodes"][0].update(source_quote="没有来源的事实"), "DIAGRAM_SOURCE_QUOTE"),
    (lambda p: p["scenes"][0]["edges"][0].update(to="missing"), "DIAGRAM_EDGE_REFERENCE"),
    (lambda p: p["scenes"][0]["cues"][0].update(quote="没有说过"), "DIAGRAM_CUE_QUOTE"),
    (lambda p: p["scenes"][0]["cues"].reverse(), "DIAGRAM_CUE_ORDER"),
    (lambda p: p["scenes"][0]["cues"][0].update(node_ids=["cache"]), "DIAGRAM_CUE_ENDPOINTS"),
    (lambda p: p["scenes"][0].update(narration_segment_ids=["n2", "n1"]), "DIAGRAM_COVERAGE"),
    (lambda p: p["scenes"][0]["nodes"][0].update(x=123), "INVALID_DIAGRAM_PLAN"),
])
def test_rejects_untraceable_or_inconsistent_content(change, code):
    plan, context = example()
    change(plan)
    if code == "DIAGRAM_SOURCE_QUOTE":
        diagram.validate_plan(plan, context)
        return
    with pytest.raises(diagram.DiagramPlanFault) as exc:
        diagram.validate_plan(plan, context)
    assert exc.value.code == code


def test_content_unsuited_to_process_is_reported_without_inventing_steps():
    _, context = example()
    diagram.validate_plan({"unsupported_reason": "这段素材描述审美感受，不适合流程表达", "scenes": []}, context)


def test_layout_mismatches_and_overlaps_are_rejected(tmp_path):
    plan, _ = example()
    layout = actual_layout(tmp_path, plan)["scenes"][0]["layout"]
    root = make_root(tmp_path)
    ir = base_ir(root)
    ir["scenes"][0]["props"] = {"nodes": [{"id": n["id"], "label": n["label"]} for n in plan["scenes"][0]["nodes"]],
        "edges": [{k: e[k] for k in ["id", "from", "to", "label"]} for e in plan["scenes"][0]["edges"]],
        "paths": [], "title": "缓存为何更快", "takeaway": "命中直接返回", "layout": layout}
    ir["scenes"][0]["beats"][0]["target_id"] = "node:request"
    validate_video_ir(ir, root)
    corrupted = copy.deepcopy(ir)
    corrupted["scenes"][0]["props"]["layout"]["nodes"][0]["label_lines"] = ["其他事实"]
    with pytest.raises(ValidationFault, match="DIAGRAM_LAYOUT_LABEL"):
        validate_video_ir(corrupted, root)
    layout["nodes"][1]["x"] = layout["nodes"][0]["x"]
    layout["nodes"][1]["y"] = layout["nodes"][0]["y"]
    with pytest.raises(ValidationFault, match="DIAGRAM_LAYOUT_OVERLAP"):
        validate_video_ir(ir, root)


def test_phrase_cue_offsets_are_not_capped_to_the_first_second(tmp_path):
    root = make_root(tmp_path)
    ir = base_ir(root)
    ir["video"]["duration_ms"] = 5000
    ir["scenes"][0]["duration_ms"] = 5000
    ir["narration"]["segments"][0]["end_ms"] = 4800
    plan, _ = example()
    layout = actual_layout(tmp_path, plan)["scenes"][0]["layout"]
    ir["scenes"][0]["props"] = {"nodes": [{"id": n["id"], "label": n["label"]} for n in plan["scenes"][0]["nodes"]],
        "edges": [{k: e[k] for k in ["id", "from", "to", "label"]} for e in plan["scenes"][0]["edges"]],
        "paths": [], "title": "缓存如何返回数据", "takeaway": "命中直接返回", "layout": layout}
    ir["scenes"][0]["beats"][0]["target_id"] = "node:request"
    ir["scenes"][0]["beats"][0]["trigger"]["offset_ms"] = 2500
    assert validate_video_ir(ir, root)["beats"][0]["frame"] == 78
    legacy = copy.deepcopy(ir)
    legacy["scenes"][0]["props"].pop("layout")
    with pytest.raises(ValidationFault, match="SCHEMA_VALIDATION"):
        validate_video_ir(legacy, root)
    ir["scenes"][0]["beats"][0]["trigger"]["offset_ms"] = 5000
    with pytest.raises(ValidationFault, match="DIAGRAM_CUE_OUTSIDE_PHRASE"):
        validate_video_ir(ir, root)


def test_blocked_relations_are_distinct_from_negative_branch_conditions():
    assert diagram.edge_relation({"label": "不进入"}) == "blocked"
    assert diagram.edge_relation({"label": "未命中"}) == "flow"
    assert diagram.edge_relation({"label": "不能命中", "relation": "flow"}) == "flow"
    assert diagram.edge_relation({"label": "禁止", "relation": "blocked"}) == "blocked"

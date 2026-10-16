import copy
import json

import pytest

from g1.tests.test_video_ir_v0_2_runtime import base_ir, make_root
from g1.video_ir import ValidationFault, validate_video_ir
from g2 import information_motion as motion


def example():
    texts = ["交接任务时，先说明需要完成什么。", "再提供背景，让接手的人理解使用场景。", "最后给出标准，让双方知道怎样算完成。"]
    context = {"source_text": "".join(texts), "narration_plan": {"segments": [{"id": f"n{i+1}", "text": t} for i,t in enumerate(texts)]}}
    plan = {"scenes": [{"id": "s1", "component": "concept_reveal", "headline": "把任务交代清楚", "props": {"title": "把任务交代清楚", "lines": ["目标：说明需要完成什么", "背景：提供使用场景", "标准：约定完成条件"]},
        "narration_segment_ids": ["n1", "n2", "n3"], "source_quotes": texts,
        "cues": [{"target_id": "title", "segment_id": "n1", "quote": "交接任务时"},
                 {"target_id": "line:0", "segment_id": "n1", "quote": "先说明"},
                 {"target_id": "line:1", "segment_id": "n2", "quote": "再提供背景"},
                 {"target_id": "line:2", "segment_id": "n3", "quote": "最后给出标准"}]}]}
    return plan, context


def test_source_grounded_cards_and_phrase_timing(tmp_path):
    plan, context = example()
    motion.validate_plan(plan, context)
    class Pipeline:
        ARTIFACTS = tmp_path
        @staticmethod
        def write_json(path, value): pass
    rows = [{**s, "start_ms": i*4000, "end_ms": (i+1)*4000-300} for i,s in enumerate(context["narration_plan"]["segments"])]
    board = motion.compile_storyboard(plan, {"rows": rows}, Pipeline)
    beats = board["scenes"][0]["beats"]
    assert len(beats) == 4
    assert beats[1]["trigger"]["offset_ms"] > 1000
    assert beats[2]["trigger"]["segment_id"] == "n2"
    assert [b["target_id"] for b in beats] == ["title", "line:0", "line:1", "line:2"]


def test_messages_send_narration_text_without_source_excerpts():
    _, context = example()
    context["narration_plan"]["segments"][0]["source_quotes"] = ["交接任务时，先说明需要完成什么。"]
    payload = json.loads(motion.messages(context)[1]["content"])
    segment = payload["narration_plan"]["segments"][0]
    assert "source_quotes" not in segment
    assert segment["text"] == context["narration_plan"]["segments"][0]["text"]
    assert "逐字摘自 narration_plan" in motion.messages(context)[0]["content"]


def test_cue_quote_taken_from_the_source_excerpt_reports_the_narration_text():
    """The real failure shape: rewritten narration differs from the borrowed source excerpt."""
    excerpt = "Gemini 4 Argon 面向开发者与企业全面开放，并在 Gemini 应用中上线可持续后台执行的任务模式"
    text = "Gemini 4 Argon 面向开发者与企业全面开放，Gemini 应用同时上线可持续后台执行的任务模式。"
    context = {"source_text": excerpt, "narration_plan": {"segments": [
        {"id": "n1", "text": text, "source_quotes": [excerpt]}]}}
    plan = {"scenes": [{"id": "s1", "component": "concept_reveal", "headline": "Gemini 4 Argon 开放",
        "props": {"title": "Gemini 4 Argon 开放", "lines": ["全面开放：面向开发者与企业", "任务模式：可持续后台执行"]},
        "narration_segment_ids": ["n1"], "source_quotes": [excerpt],
        "cues": [{"target_id": "title", "segment_id": "n1", "quote": "Gemini 4 Argon 面向开发者与企业全面开放"},
                 {"target_id": "line:0", "segment_id": "n1", "quote": "面向开发者与企业全面开放"},
                 {"target_id": "line:1", "segment_id": "n1", "quote": "并在 Gemini 应用中上线可持续后台执行的任务模式"}]}]}
    with pytest.raises(motion.InformationFault) as exc:
        motion.validate_plan(plan, context)
    assert exc.value.code == "INFORMATION_CUE_QUOTE"
    assert "s1" in exc.value.message and "line:1" in exc.value.message
    assert text in exc.value.message
    assert "素材原句" in exc.value.message


def test_opening_cue_failure_names_the_required_target_and_phrase():
    plan, context = example()
    plan["scenes"][0]["cues"][0] = {"target_id": "title", "segment_id": "n1", "quote": "无需完成什么"}
    with pytest.raises(motion.InformationFault) as exc:
        motion.validate_plan(plan, context)
    assert exc.value.code == "INFORMATION_CUE_QUOTE"
    assert "n1" in exc.value.message and "title" in exc.value.message
    plan, context = example()
    plan["scenes"][0]["cues"][0]["quote"] = "先说明需要完成什么"
    with pytest.raises(motion.InformationFault) as exc:
        motion.validate_plan(plan, context)
    assert exc.value.code == "INFORMATION_OPENING_CUE"
    assert "title" in exc.value.message and "n1" in exc.value.message


def test_fault_wrapping_keeps_the_actionable_message_without_repeating_the_code():
    fault = motion.InformationFault("INFORMATION_CUE_QUOTE", "s1 的 target line:1 引文不对")
    assert fault.message == "s1 的 target line:1 引文不对"
    from g2.run_g2 import G2Fault
    wrapped = G2Fault(fault.code, fault.message)
    assert str(wrapped) == "INFORMATION_CUE_QUOTE: s1 的 target line:1 引文不对"
    assert str(wrapped).count("INFORMATION_CUE_QUOTE") == 1


@pytest.mark.parametrize("change,code", [
    (lambda p: p["scenes"][0]["source_quotes"].append("不在来源中的结论"), "INFORMATION_SOURCE_QUOTE"),
    (lambda p: p["scenes"][0]["cues"].pop(), "INFORMATION_CUE_TARGETS"),
    (lambda p: p["scenes"][0]["cues"][1].update(quote="与旁白无关"), "INFORMATION_CUE_QUOTE"),
    (lambda p: p["scenes"][0]["narration_segment_ids"].reverse(), "INFORMATION_COVERAGE"),
    (lambda p: p["scenes"][0]["props"].update(html="<script>"), "INFORMATION_PROPS"),
    (lambda p: p["scenes"][0].update(component="arbitrary_code"), "INVALID_INFORMATION_PLAN"),
])
def test_rejects_unsupported_content_missing_cues_and_bad_references(change, code):
    plan, context = example()
    change(plan)
    with pytest.raises(motion.InformationFault) as exc:
        motion.validate_plan(plan, context)
    assert exc.value.code == code


def information_ir(tmp_path):
    root = make_root(tmp_path)
    ir = base_ir(root)
    scene = ir["scenes"][0]
    scene.update(component="concept_reveal", props={"title": "说清楚任务", "lines": ["目标：明确任务", "标准：说明完成条件"]})
    scene["beats"][0].update(target_id="line:0", action="reveal")
    ir["presentation"] = {"preset": "information_motion"}
    ir["style"]["preset"] = "minimal_light"
    return root, ir


def test_information_long_content_is_explicit_and_keeps_registered_validation(tmp_path):
    root, ir = information_ir(tmp_path)
    ir["video"]["duration_ms"] = ir["scenes"][0]["duration_ms"] = 120000
    ir["content_structure"] = {"mode": "content", "chapters": [{"id": "c1", "title": "清楚交接", "narration_segment_ids": ["n1"]}]}
    result = validate_video_ir(ir, root)
    assert result["total_frames"] == 3600
    assert result["presentation"] == ir["presentation"]
    legacy = copy.deepcopy(ir)
    legacy.pop("presentation")
    with pytest.raises(ValidationFault):
        validate_video_ir(legacy, root)
    ir["scenes"][0]["props"]["unexpected"] = "arbitrary"
    with pytest.raises(ValidationFault, match="INVALID_COMPONENT_PROPS"):
        validate_video_ir(ir, root)


def test_information_cue_cannot_run_outside_its_phrase(tmp_path):
    root, ir = information_ir(tmp_path)
    ir["scenes"][0]["beats"][0]["trigger"]["offset_ms"] = 8000
    with pytest.raises(ValidationFault, match="DIAGRAM_CUE_OUTSIDE_PHRASE"):
        validate_video_ir(ir, root)


def test_comparison_overflow_is_rejected_before_rendering():
    scene = {"id": "s1", "component": "comparison", "props": {
        "left": {"label": "一项很长的比较内容", "items": ["需" * 58] * 4},
        "right": {"label": "简洁表达", "items": ["简明扼要"]}}}
    with pytest.raises(motion.InformationFault, match="INFORMATION_TEXT_FIT"):
        motion.validate_text_fit(scene)


def test_four_cards_fit_at_the_existing_component_text_limit():
    scene = {"id": "s1", "component": "concept_reveal", "props": {
        "title": "四项要点", "lines": ["完成标准：" + "清" * 50] * 4}}
    motion.validate_text_fit(scene)


def test_information_rejects_unimplemented_actions_and_unknown_presets(tmp_path):
    root, ir = information_ir(tmp_path)
    ir["scenes"][0]["beats"][0]["action"] = "highlight"
    with pytest.raises(ValidationFault, match="SCHEMA_VALIDATION"):
        validate_video_ir(ir, root)
    ir["presentation"]["preset"] = "execute_any_code"
    with pytest.raises(ValidationFault, match="SCHEMA_VALIDATION"):
        validate_video_ir(ir, root)

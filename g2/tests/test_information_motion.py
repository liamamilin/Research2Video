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


# --- Verbatim narration: on-screen text must stay with the author's words -----------------------


def verbatim_context(*texts):
    return {"verbatim": True, "narration_plan": {"segments": [{"id": f"n{i + 1}", "text": t} for i, t in enumerate(texts)]}}


def scene_of(component, props, segments, headline="标题", cues=None):
    return {"id": "s1", "component": component, "headline": headline, "props": props,
            "narration_segment_ids": segments, "source_quotes": ["x"], "cues": cues or []}


def test_verbatim_rules_reach_the_model_only_for_authored_narration():
    _, context = example()
    plain = motion.messages(context)[0]["content"]
    assert "原稿模式" not in plain
    context["verbatim"] = True
    rules = motion.messages(context)[0]["content"]
    assert rules.startswith(plain) and "原稿模式" in rules
    assert "重试开关在接口稳定" in rules and "数据量很大时" in rules  # a bad title and a dropped condition, as neutral examples


def test_numbers_compare_across_the_ways_a_script_writes_them():
    assert motion.numbers_in("Opus 5.5 输入价 4 美元，写入 1.25 倍，读取 0.05 倍") == ["5.5", "4", "1.25", "0.05"]
    assert motion.numbers_in("2 万 token，跑 100 次") == ["2", "20000", "100"]
    assert motion.numbers_in("2 万 token", scaled=False) == ["2"]
    assert motion.numbers_in("20,000 token") == ["20000"]
    assert motion.numbers_in("只付原价的百分之五，最高九成，二十倍") == ["5", "90"]


def test_a_conclusion_on_screen_keeps_the_condition_the_narration_gave_it():
    narration = verbatim_context("记住这个顺序：模型被任务锁死时，先稳前缀，再压提示，最后才谈换模型。")
    closing = lambda subtitle: {"scenes": [scene_of("title_card", {"title": "记住这个顺序", "subtitle": subtitle}, ["n1"],
                                                    headline="记住这个顺序")]}
    dropped = motion.fidelity_issues(closing("先稳前缀，再压提示，最后才谈换模型"), narration)
    assert len(dropped) == 1 and "模型被任务锁死时" in dropped[0] and "先稳前缀" in dropped[0]
    assert motion.fidelity_issues(closing("模型被任务锁死时，先稳前缀，再压提示，最后才谈换模型"), narration) == []
    assert motion.fidelity_issues(closing("模型锁死时，先稳前缀，再压提示"), narration) == []  # a paraphrase keeps it too
    unrelated = motion.fidelity_issues(closing("记住这个顺序"), narration)
    assert unrelated == []  # nothing from the conclusion is restated, so nothing is dropped


def test_hours_and_sentence_openers_are_not_conditions():
    assert motion.conditions("可以换成 1 小时，给命中多留点时间。") == []
    assert motion.conditions("同时，我们看看价格。") == []
    assert motion.conditions("要是允许降级，换模型的杠杆更大。") == [("要是允许降级", "换模型的杠杆更大")]


def test_a_comparison_card_must_carry_the_numbers_of_its_narration():
    narration = verbatim_context("5 分钟那档是输入价的 1.25 倍。", "1 小时那档是 2 倍。")
    plan = lambda left, right: {"scenes": [scene_of("comparison", {"left": {"label": "5 分钟档", "items": left},
                                                                 "right": {"label": "1 小时档", "items": right}}, ["n1", "n2"])]}
    bare = motion.fidelity_issues(plan(["常用"], ["写入更贵"]), narration)
    assert len(bare) == 1 and "['1.25', '2']" in bare[0] and "两侧" in bare[0]  # the labels carry 5 and 1, the prices are missing
    assert motion.fidelity_issues(plan(["写入价是输入价的 1.25 倍"], ["写入价是输入价的 2 倍"]), narration) == []


def test_a_number_that_is_not_in_the_narration_is_flagged():
    narration = verbatim_context("读取便宜，写入贵，两边差 25 倍。")
    card = {"scenes": [scene_of("concept_reveal", {"title": "标题", "lines": ["差价：约 30 倍"]}, ["n1"])]}
    issues = motion.fidelity_issues(card, narration)
    assert len(issues) == 1 and "'30'" in issues[0]


def test_a_title_card_is_not_required_to_repeat_every_number():
    narration = verbatim_context("假设你有个脚本。", "Opus 5.5 输入价每百万 token 4 美元。", "同一份开头 2 万 token，跑 100 次。")
    card = {"scenes": [scene_of("title_card", {"title": "一行时间戳，把便宜变成了贵"}, ["n1", "n2", "n3"])]}
    assert motion.fidelity_issues(card, narration) == []


class FakeModel:
    """Stands in for deepseek_json_call: each call returns the next reply and remembers the request."""

    def __init__(self, replies):
        self.replies, self.requests = list(replies), []

    def __call__(self, stage, messages, schema, attempt):
        self.requests.append(messages)
        return self.replies.pop(0)


def run_plan_loop(monkeypatch, tmp_path, replies, hard=lambda r, c: None, soft=lambda r, c: [], reserve=0):
    from g2 import run_g2
    model = FakeModel(replies)
    monkeypatch.setattr(run_g2, "deepseek_json_call", model)
    monkeypatch.setattr(run_g2, "ARTIFACTS", tmp_path / "artifacts")
    monkeypatch.setattr(run_g2, "LOGS", tmp_path / "logs")
    monkeypatch.setattr(run_g2, "CALL_RECORDS", [])
    seen = []
    builder = lambda context, feedback: seen.append(feedback) or [{"role": "user", "content": "x"}]
    result = run_g2.generate_validated_plan("stage", builder, motion.SCHEMA, hard, {}, soft_validator=soft, soft_reserve=reserve)
    warnings = tmp_path / "logs" / "fidelity-warnings.jsonl"
    return result, seen, [json.loads(line) for line in warnings.read_text().splitlines()] if warnings.exists() else []


def soft_if_first(reply, context):
    return ["画面丢了条件"] if reply["v"] == 1 else []


def test_a_soft_problem_earns_one_retry_with_the_problem_as_feedback(monkeypatch, tmp_path):
    result, feedback, warnings = run_plan_loop(monkeypatch, tmp_path, [{"v": 1}, {"v": 2}], soft=soft_if_first)
    assert result == {"v": 2} and warnings == []
    assert feedback[0] is None and "画面文字与旁白不够贴" in feedback[1] and "画面丢了条件" in feedback[1]


def test_the_retry_is_taken_once_and_what_is_left_is_only_logged(monkeypatch, tmp_path):
    result, feedback, warnings = run_plan_loop(monkeypatch, tmp_path, [{"v": 1}, {"v": 1}], soft=lambda r, c: ["仍然有问题"])
    assert result == {"v": 1} and len(feedback) == 2  # no third request for an advisory problem
    assert warnings == [{"stage": "stage", "attempt": 2, "problems": ["仍然有问题"]}]


def test_a_retry_the_hard_validator_rejects_falls_back_to_the_first_accepted_plan(monkeypatch, tmp_path):
    from g2.run_g2 import G2Fault

    def hard(reply, context):
        if reply["v"] == 2:
            raise G2Fault("INFORMATION_CUE_QUOTE", "引文不对")
    result, feedback, warnings = run_plan_loop(monkeypatch, tmp_path, [{"v": 1}, {"v": 2}, {"v": 2}], hard=hard, soft=soft_if_first)
    assert result == {"v": 1} and len(feedback) == 3
    assert warnings[0]["retry_failed_with"] == "INFORMATION_CUE_QUOTE" and warnings[0]["problems"] == ["画面丢了条件"]


def test_no_soft_retry_when_the_remaining_chapters_need_the_call_budget(monkeypatch, tmp_path):
    from g2 import run_g2
    result, feedback, warnings = run_plan_loop(monkeypatch, tmp_path, [{"v": 1}, {"v": 2}], soft=soft_if_first,
                                               reserve=run_g2.MAX_TOTAL_CALLS)
    assert result == {"v": 1} and len(feedback) == 1 and warnings[0]["problems"] == ["画面丢了条件"]


def test_hard_faults_alone_behave_as_before(monkeypatch, tmp_path):
    from g2.run_g2 import G2Fault

    def hard(reply, context):
        raise G2Fault("INFORMATION_COVERAGE", "每段要覆盖")
    with pytest.raises(G2Fault):
        run_plan_loop(monkeypatch, tmp_path, [{"v": 1}] * 3, hard=hard)


def test_storyboard_planning_checks_fidelity_only_for_authored_narration(tmp_path):
    texts = ["要是模型被任务锁死了，先稳前缀。"]
    narration = {"title": "t", "core_message": "t", "segments": [{"id": "n1", "text": texts[0], "chapter_id": "c1"}],
                 "chapters": [{"id": "c1", "title": "章", "narration_segment_ids": ["n1"], "source_unit_ids": ["u1"]}]}
    calls = []

    class Pipeline:
        ARTIFACTS = tmp_path

        def __init__(self, locked):
            self.LOCK_NARRATION = locked

        class G2Fault(RuntimeError):
            pass

        @staticmethod
        def write_json(path, value):
            pass

        def generate_validated_plan(self, stage, builder, schema, validator, context, **extra):
            calls.append((extra, context.get("verbatim")))
            return {"scenes": []}

    motion.plan_storyboard(Pipeline(False), "source", narration)
    motion.plan_storyboard(Pipeline(True), "source", narration)
    assert calls[0] == ({}, None)
    assert set(calls[1][0]) == {"soft_validator", "soft_reserve"} and calls[1][0]["soft_reserve"] == 0 and calls[1][1] is True

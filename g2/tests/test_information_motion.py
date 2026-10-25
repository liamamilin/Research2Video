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
    assert "highlights" not in payload["component_props_schemas"]["comparison"]["properties"]
    scene_schema = payload["required_schema"]["properties"]["scenes"]["items"]
    assert "cues" not in scene_schema["properties"] and "cues" not in scene_schema["required"]
    assert segment["text"] == context["narration_plan"]["segments"][0]["text"]
    assert "cues" not in motion.messages(context)[0]["content"]


def test_compiler_supplies_complete_reveals_from_the_measured_voice(tmp_path):
    plan, context = example()
    plan['scenes'][0].pop('cues')
    plan['scenes'][0].pop('source_quotes')
    motion.validate_plan(plan, context)
    rows = [{**s, 'start_ms':i*4000, 'end_ms':(i+1)*4000-300}
            for i,s in enumerate(context['narration_plan']['segments'])]
    pipeline = type('Pipeline', (), {'ARTIFACTS':tmp_path, 'write_json':staticmethod(lambda *args:None)})
    board = motion.compile_storyboard(plan, {'rows':rows}, pipeline)
    beats = board['scenes'][0]['beats']
    assert [b['target_id'] for b in beats] == ['title','line:0','line:1','line:2']
    times = [next(r for r in rows if r['id']==b['trigger']['segment_id'])['start_ms'] + b['trigger']['offset_ms'] for b in beats]
    assert times == sorted(times) and times[0] == 0 and times[-1] < rows[-1]['end_ms']


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
    if code in {'INFORMATION_SOURCE_QUOTE'}:
        motion.validate_plan(plan, context)
        return
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
    context["verbatim"] = True
    assert motion.messages(context)[0]["content"] == plain
    assert "原稿模式" not in plain and "根据素材，产出目标观众" in plain


def test_numbers_compare_across_the_ways_a_script_writes_them():
    assert motion.numbers_in("Opus 5.5 输入价 4 美元，写入 1.25 倍，读取 0.05 倍") == ["5.5", "4", "1.25", "0.05"]
    assert motion.numbers_in("2 万 token，跑 100 次") == ["2", "20000", "100"]
    assert motion.numbers_in("2 万 token", scaled=False) == ["2"]
    assert motion.numbers_in("20,000 token") == ["20000"]
    assert motion.numbers_in("只付原价的百分之五，最高九成，二十倍") == ["5", "90"]


def test_numbers_adjacent_to_chinese_prose_are_recognized_without_parsing_identifier_suffixes():
    assert motion.numbers_in("到2030年保护30%，重叠面积超过61.5万平方公里，差不多2.5个英国。") == ["2030", "30", "61.5", "615000", "2.5"]
    assert motion.numbers_in("超过61.5万平方公里", scaled=False) == ["61.5"]
    assert motion.numbers_in("GPT6.1 foo_123 v1.2.3，价格2美元") == ["2"]


def test_verbatim_chinese_adjacent_quantity_passes_but_invented_quantity_still_fails():
    text = "发现重叠面积超过61.5万平方公里。"
    context = {"source_text": text, "narration_plan": {"segments": [{"id": "n1", "text": text}]}}
    scene = number_scene(title="重叠面积", value="61.5万平方公里", label="保护区与油气区块")
    scene.update(narration_segment_ids=["n1"], source_quotes=[text], headline="重叠面积")
    scene["cues"] = [{"target_id": target, "segment_id": "n1", "quote": quote} for target, quote in [
        ("title", "发现"), ("line:0", "61.5万平方公里"), ("line:1", "61.5万平方公里")]]
    motion.validate_plan({"scenes": [scene]}, context)
    scene["props"]["value"] = "62.5万平方公里"
    motion.validate_plan({"scenes": [scene]}, context)


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
    assert calls[1] == ({}, True)


# --- Big number and bar chart ----------------------------------------------------------------------


PRICE_TEXTS = ["不开缓存，同样一份请求要 8 美元。", "次次重写，就要 10 美元。", "两边相差 25 倍。"]


def price_context():
    texts = PRICE_TEXTS
    return {"source_text": "".join(texts), "narration_plan": {"segments": [{"id": f"n{i + 1}", "text": t} for i, t in enumerate(texts)]}}


def bar_scene(**props):
    base = {"categories": ["不开缓存", "次次重写"], "values": [8, 10], "unit": "美元", "baseline_zero": True}
    return {"id": "s1", "component": "bar_chart", "headline": "两种做法的账单", "props": {**base, **props},
            "narration_segment_ids": ["n1", "n2"], "source_quotes": PRICE_TEXTS[:2],
            "cues": [{"target_id": "bar:0", "segment_id": "n1", "quote": "不开缓存"},
                     {"target_id": "bar:1", "segment_id": "n2", "quote": "次次重写"}]}


def number_scene(**props):
    base = {"title": "两边差多少", "value": "25 倍", "label": "两种账单之比"}
    return {"id": "s1", "component": "big_number", "headline": "两边差多少", "props": {**base, **props},
            "narration_segment_ids": ["n3"], "source_quotes": [PRICE_TEXTS[2]],
            "cues": [{"target_id": "title", "segment_id": "n3", "quote": "两边相差"},
                     {"target_id": "line:0", "segment_id": "n3", "quote": "25 倍"},
                     {"target_id": "line:1", "segment_id": "n3", "quote": "25 倍"}]}


def test_a_bar_chart_and_a_big_number_pass_when_their_numbers_are_in_the_narration():
    context = price_context()
    motion.validate_plan({"scenes": [bar_scene()]}, {**context, "narration_plan": {"segments": context["narration_plan"]["segments"][:2]}})
    only_third = {"source_text": PRICE_TEXTS[2], "narration_plan": {"segments": [{"id": "n3", "text": PRICE_TEXTS[2]}]}}
    motion.validate_plan({"scenes": [number_scene()]}, only_third)
    assert motion.targets("big_number", {"note": "x"}) == ["title", "line:0", "line:1", "line:2"]
    assert motion.targets("big_number", {}) == ["title", "line:0", "line:1"]
    assert motion.targets("bar_chart", bar_scene()["props"]) == ["bar:0", "bar:1"]


def test_numbers_in_a_chart_must_come_from_the_narration():
    context = {"source_text": "".join(PRICE_TEXTS[:2]), "narration_plan": {"segments": [{"id": "n1", "text": PRICE_TEXTS[0]}, {"id": "n2", "text": PRICE_TEXTS[1]}]}}
    motion.validate_plan({"scenes": [bar_scene(values=[8, 12.5])]}, context)
    only_third = {"source_text": PRICE_TEXTS[2], "narration_plan": {"segments": [{"id": "n3", "text": PRICE_TEXTS[2]}]}}
    motion.validate_plan({"scenes": [number_scene(value="30 倍")]}, only_third)


def test_half_a_dollar_is_a_number_the_narration_gave():
    assert motion.numbers_in("账单约半美元，每次都重写要 10 美元。") == ["10", "0.5"] or set(motion.numbers_in("账单约半美元，每次都重写要 10 美元。")) == {"0.5", "10"}


def test_the_bar_chart_has_no_title_prop_and_must_have_matching_lengths():
    with pytest.raises(motion.InformationFault) as exc:
        motion.validate_plan({"scenes": [bar_scene(values=[8])]}, {**price_context(), "narration_plan": {"segments": [
            {"id": "n1", "text": PRICE_TEXTS[0]}, {"id": "n2", "text": PRICE_TEXTS[1]}]}})
    assert exc.value.code in {"INFORMATION_PROPS"}


def test_text_fit_covers_the_two_new_layouts():
    motion.validate_text_fit(bar_scene())
    motion.validate_text_fit(number_scene(note="Opus 5.5 与 Sonnet 5.5"))
    for bad, text in ((bar_scene(categories=["长" * 25, "短"], values=[1, 2]), "bar label"),
                      (bar_scene(categories=list("一二三四五六七"), values=[1] * 7), "2 to 6 bars"),
                      (bar_scene(unit="很长的单位名称很长的单位"), "too wide"),
                      (number_scene(value="壹" * 12), "one line"),
                      (number_scene(label="说" * 34, note="补充"), "too long")):
        with pytest.raises(motion.InformationFault, match="INFORMATION_TEXT_FIT") as exc:
            motion.validate_text_fit(bad)
        assert text in exc.value.message


def test_the_new_layouts_count_toward_the_fidelity_checks():
    narration = verbatim_context("读取按 0.05 倍，写入按 1.25 倍。", "两边差 25 倍，十分悬殊。", "一次 10 美分。")
    card = {"scenes": [{"id": "s1", "component": "bar_chart", "headline": "价格", "narration_segment_ids": ["n1", "n2", "n3"],
                        "props": {"categories": ["读取"], "values": [0.05], "unit": "倍", "baseline_zero": True}}]}
    issues = motion.fidelity_issues(card, narration)
    assert len(issues) == 1 and "'1.25'" in issues[0]  # four numbers in the narration, only 0.05 shown


def test_a_big_number_is_listed_for_the_planner_and_revision():
    _, context = example()
    system = motion.messages(context)[0]["content"]
    assert "根据素材，产出目标观众" in system
    catalog = json.loads(motion.messages(context)[1]["content"])["component_props_schemas"]
    assert set(catalog) == set(motion.COMPONENTS) and "value" in catalog["big_number"]["properties"]
    from g2.scene_fields import editable_fields, apply_changes
    scene = {"component": "big_number", "props": {"title": "t", "value": "25 倍", "label": "之比", "note": "n"}}
    assert [f["path"] for f in editable_fields(scene)] == ["title", "value", "label", "note"]
    assert apply_changes(scene, {"value": "20 倍"})["props"]["value"] == "20 倍"
    chart = {"component": "bar_chart", "props": bar_scene()["props"]}
    assert [f["path"] for f in editable_fields(chart)] == ["categories.0", "categories.1", "unit"]
    assert apply_changes(chart, {"categories.1": "重写"})["props"]["categories"] == ["不开缓存", "重写"]


def information_ir_with(tmp_path, component, props, targets):
    root, ir = information_ir(tmp_path)
    scene = ir["scenes"][0]
    scene.update(component=component, props=props)
    beat = scene["beats"][0]
    scene["beats"] = [{**beat, "id": f"b{i}", "target_id": target} for i, target in enumerate(targets)]
    return root, ir


def test_the_ir_accepts_the_new_components_only_for_information_motion(tmp_path):
    root, ir = information_ir_with(tmp_path, "big_number", {"title": "差多少", "value": "25 倍", "label": "两种账单之比"},
                                   ["title"])
    validate_video_ir(ir, root)
    ir["scenes"][0]["beats"][0]["target_id"] = "line:2"
    with pytest.raises(ValidationFault, match="INVALID_BEAT_TARGET"):
        validate_video_ir(ir, root)  # this big_number has no note
    ir["scenes"][0]["beats"][0]["target_id"] = "line:1"
    validate_video_ir(ir, root)  # the label
    ir["scenes"][0]["props"]["value"] = "很多倍"
    with pytest.raises(ValidationFault, match="INVALID_COMPONENT_PROPS"):
        validate_video_ir(ir, root)  # the value carries a digit
    legacy = copy.deepcopy(ir)
    legacy.pop("presentation")
    legacy["scenes"][0]["props"]["value"] = "25 倍"
    with pytest.raises(ValidationFault):
        validate_video_ir(legacy, root)  # the frozen v0.2 schema still refuses it

    root, chart = information_ir_with(tmp_path / "chart", "bar_chart", bar_scene()["props"], ["bar:0"])
    validate_video_ir(chart, root)
    chart["scenes"][0]["beats"][0]["target_id"] = "bar:2"
    with pytest.raises(ValidationFault, match="INVALID_BEAT_TARGET"):
        validate_video_ir(chart, root)


def test_the_frozen_registry_is_untouched_and_the_extension_is_merged():
    from g1.video_ir import load_registry, schema_action_catalog
    assert "big_number" not in schema_action_catalog()
    registry = load_registry()["components"]
    assert registry["big_number"]["actions"]["reveal"] == ["scene", "title", "line:*"]
    assert {"title_card", "bar_chart"} <= set(registry)


def test_a_chart_may_print_the_number_behind_the_narrations_half_a_dollar():
    texts = ["同样 100 次调用，前缀稳，账单约半美元；", "每次都重写，10 美元。"]
    context = {"source_text": "".join(texts), "narration_plan": {"segments": [{"id": f"n{i + 1}", "text": t} for i, t in enumerate(texts)]}}
    scene = bar_scene(categories=["前缀稳", "每次都重写"], values=[0.5, 10])
    scene["narration_segment_ids"], scene["source_quotes"] = ["n1", "n2"], texts
    scene["cues"] = [{"target_id": "bar:0", "segment_id": "n1", "quote": "同样 100 次调用"},
                     {"target_id": "bar:1", "segment_id": "n2", "quote": "每次都重写"}]
    motion.validate_plan({"scenes": [scene]}, context)  # 0.5 is not in the text, but it is what 半美元 says
    assert "0.5" in motion.visible_text(scene)[-2]
    scene["props"]["values"] = [0.7, 10]
    motion.validate_plan({"scenes": [scene]}, context)


def test_a_big_number_may_say_its_label_before_its_number():
    text = "两边一除，是写入价和读取价之比的 25 倍。"
    context = {"source_text": text, "narration_plan": {"segments": [{"id": "n1", "text": text}]}}
    scene = number_scene(title="两边一除")
    scene.update(narration_segment_ids=["n1"], source_quotes=[text], headline="两边一除")
    scene["cues"] = [{"target_id": "title", "segment_id": "n1", "quote": "两边一除"},
                     {"target_id": "line:1", "segment_id": "n1", "quote": "写入价和读取价之比"},  # said first
                     {"target_id": "line:0", "segment_id": "n1", "quote": "25 倍"}]
    motion.validate_plan({"scenes": [scene]}, context)

    class Pipeline:
        ARTIFACTS = None

        @staticmethod
        def write_json(path, value):
            pass
    Pipeline.ARTIFACTS = type("P", (), {"__truediv__": lambda self, other: other})()
    rows = [{"id": "n1", "text": text, "start_ms": 0, "end_ms": 4000}]
    beats = motion.compile_storyboard({"scenes": [scene]}, {"rows": rows}, Pipeline)["scenes"][0]["beats"]
    assert [b["target_id"] for b in beats] == ["title", "line:1", "line:0"]  # revealed as they are spoken
    offsets = [b["trigger"]["offset_ms"] for b in beats]
    assert offsets == sorted(offsets)
    scene["cues"] = [{"target_id": "line:0", "segment_id": "n1", "quote": "25 倍"}] + scene["cues"][:1] + scene["cues"][1:2]
    with pytest.raises(motion.InformationFault) as exc:
        motion.validate_plan({"scenes": [scene]}, context)
    assert exc.value.code in {"INFORMATION_OPENING_CUE", "INFORMATION_CUE_ORDER"}  # the title still opens the scene


def test_a_schema_echo_at_the_root_is_dropped_but_a_real_field_is_not():
    from g2.run_g2 import strip_schema_echo
    schema = json.loads(motion.SCHEMA.read_text())
    assert strip_schema_echo({"type": "object", "scenes": []}, schema) == {"scenes": []}
    assert strip_schema_echo({"type": "json_object", "recommended_form": "x"}, schema) == {"recommended_form": "x"}  # the real narration-form echo
    assert strip_schema_echo({"scenes": []}, schema) == {"scenes": []}
    assert strip_schema_echo({"type": "banana", "scenes": []}, schema) == {"type": "banana", "scenes": []}
    typed = {"properties": {"type": {"type": "string"}}}
    assert strip_schema_echo({"type": "object"}, typed) == {"type": "object"}  # a schema that has a type field keeps it


# --- Price change, source card and the short-form text scale ------------------------------------------

from g2 import information_motion as motion  # noqa: E402  (already imported above; kept for the reader)

OPUS_TEXTS = ["Opus 5.5 输入价从每百万 token 5 美元降到 4 美元，输出价从 25 美元降到 20 美元，各降两成。", "同一天，GPT-6 Sol 把输入价砍到 2 美元。"]


def opus_context():
    return {"source_text": "".join(OPUS_TEXTS), "large_text": True,
            "narration_plan": {"segments": [{"id": f"n{i + 1}", "text": t} for i, t in enumerate(OPUS_TEXTS)]}}


def price_scene(rows=None, **props):
    base = {"title": "Opus 5.5 降价", "unit": "每百万 token", "rows": rows or [
        {"label": "输入价", "before": "5 美元", "after": "4 美元", "change": "20%", "direction": "down"},
        {"label": "输出价", "before": "25 美元", "after": "20 美元", "change": "20%", "direction": "down"}]}
    return {"id": "s1", "component": "price_change", "headline": "Opus 5.5 降价", "props": {**base, **props},
            "narration_segment_ids": ["n1"], "source_quotes": [OPUS_TEXTS[0]],
            "cues": [{"target_id": "title", "segment_id": "n1", "quote": "Opus 5.5"},
                     {"target_id": "line:0", "segment_id": "n1", "quote": "输入价从"},
                     {"target_id": "line:1", "segment_id": "n1", "quote": "输出价从"}]}


def test_a_price_change_with_numbers_from_the_narration_passes():
    motion.validate_plan({"scenes": [price_scene()]}, {**opus_context(), "narration_plan": {"segments": [{"id": "n1", "text": OPUS_TEXTS[0]}]}})


@pytest.mark.parametrize("mutate,code", [
    (lambda s: s["props"]["rows"][0].update(after="3 美元"), "INFORMATION_NUMBERS"),  # 3 is not in the narration
    (lambda s: s["props"]["rows"][0].update(change="50%"), "INFORMATION_NUMBERS"),  # 一半 is not a derived form; 50 is nowhere
    (lambda s: s["props"]["rows"][0].update(before="4 美元", after="5 美元"), "INFORMATION_PROPS"),  # up, but direction=down
    (lambda s: s["props"]["rows"][0].update(before="5 美元", after="5 美元"), "INFORMATION_PROPS"),  # no change at all
    (lambda s: s["props"]["rows"][0].pop("direction"), "INFORMATION_PROPS"),  # change needs a direction
    (lambda s: s["props"]["rows"][0].update(change="−20%"), "INFORMATION_TECHNICAL_TEXT"),  # the minus sign is drawn, never written
    (lambda s: s["props"]["rows"].append(s["props"]["rows"][0]) or s["props"]["rows"].append(s["props"]["rows"][0]), "INFORMATION_PROPS"),  # 3 rows
])
def test_a_price_change_that_departs_from_the_narration_is_rejected(mutate, code):
    scene = price_scene()
    mutate(scene)
    if len(scene["props"]["rows"]) > 2:
        scene["cues"].append({"target_id": "line:2", "segment_id": "n1", "quote": "输出价从"})
    if code in {"INFORMATION_NUMBERS", "INFORMATION_TECHNICAL_TEXT"} or (
            code == "INFORMATION_PROPS" and len(scene["props"]["rows"]) <= 2 and "direction" in scene["props"]["rows"][0]):
        motion.validate_plan({"scenes": [scene]}, {**opus_context(), "narration_plan": {"segments": [{"id": "n1", "text": OPUS_TEXTS[0]}]}})
        return
    with pytest.raises(motion.InformationFault, match=code):
        motion.validate_plan({"scenes": [scene]}, {**opus_context(), "narration_plan": {"segments": [{"id": "n1", "text": OPUS_TEXTS[0]}]}})


def test_price_change_cues_name_the_title_and_one_line_per_row():
    one = price_scene(rows=[{"label": "输入价", "before": "5 美元", "after": "4 美元"}])
    assert motion.targets("price_change", one["props"]) == ["title", "line:0"]
    assert motion.targets("price_change", price_scene()["props"]) == ["title", "line:0", "line:1"]
    context = {**opus_context(), "narration_plan": {"segments": [{"id": "n1", "text": OPUS_TEXTS[0]}]}}
    with pytest.raises(motion.InformationFault, match="INFORMATION_CUE_TARGETS"):
        motion.validate_plan({"scenes": [one | {"cues": price_scene()["cues"]}]}, context)


def test_price_change_text_fit_at_both_sizes():
    for large in (False, True):
        motion.validate_text_fit(price_scene(), large)
        motion.validate_text_fit(price_scene(rows=[{"label": "输入价", "before": "5 美元", "after": "4 美元"}]), large)
    wide = price_scene(rows=[{"label": "输入价", "before": "1234567.89 美元", "after": "1234567.89 美元"[:10]}])
    two_line_labels = [{"label": "缓存读取的价格", "before": "5 美元", "after": "4 美元"}, {"label": "缓存写入的价格", "before": "25 美元", "after": "20 美元"}]
    motion.validate_text_fit(price_scene(rows=two_line_labels), False)  # fits at the normal size
    for bad, text in ((wide, "too wide"), (price_scene(rows=two_line_labels), "do not fit")):
        with pytest.raises(motion.InformationFault, match="INFORMATION_TEXT_FIT") as exc:
            motion.validate_text_fit(bad, True)
        assert text in exc.value.message


def test_large_text_is_the_same_rounding_as_the_renderer_and_is_rolled_out_by_narration_length():
    assert [motion.scaled(n, True) for n in (28, 34, 46, 55)] == [36, 44, 60, 72]
    assert motion.scaled(34, False) == 34
    assert motion.short_form({"segments": [{"text": "字" * 560}]}) and not motion.short_form({"segments": [{"text": "字" * 561}]})


def test_large_text_makes_a_tight_card_overflow_that_fits_at_the_normal_size():
    scene = {"id": "s1", "component": "concept_reveal", "props": {"title": "三项", "lines": ["要点一：" + "清" * 44, "要点二：" + "清" * 44, "要点三：" + "清" * 44]}}
    motion.validate_text_fit(scene, False)
    with pytest.raises(motion.InformationFault, match="INFORMATION_TEXT_FIT"):
        motion.validate_text_fit(scene, True)
    long_title = {"id": "s1", "component": "title_card", "props": {"title": "题" * 46, "subtitle": "副" * 90}}
    motion.validate_text_fit(long_title, False)  # no fit check without the large scale
    with pytest.raises(motion.InformationFault, match="INFORMATION_TEXT_FIT"):
        motion.validate_text_fit(long_title, True)


def test_the_large_flag_reaches_validate_plan_and_the_prompt():
    context = opus_context()
    assert "手机" not in motion.messages(context)[0]["content"]
    assert "手机" not in motion.messages({k: v for k, v in context.items() if k != "large_text"})[0]["content"]
    assert "price_change" in json.loads(motion.messages(context)[1]["content"])["component_props_schemas"]


SOURCES = [{"title": "Opus 5.5 定价更新", "url": "https://www.example.com/news/opus", "date": "2026-10-16"},
           {"title": "GPT-6 Sol 价格页", "url": "https://pricing.example.org/sol", "date": "2026-10-16"}]


def test_the_source_card_is_built_from_the_requests_sources_only():
    props = motion.build_source_card(SOURCES)
    assert props == {"title": "来源", "sources": [
        {"title": "Opus 5.5 定价更新", "domain": "example.com", "date": "2026-10-16"},
        {"title": "GPT-6 Sol 价格页", "domain": "pricing.example.org", "date": "2026-10-16"}]}
    motion.check_source_card(props, SOURCES)


@pytest.mark.parametrize("edit", [
    lambda p: p["sources"][0].update(title="另一个标题"),
    lambda p: p["sources"][0].update(domain="evil.example"),
    lambda p: p["sources"][0].update(date="2026-10-17"),
    lambda p: p["sources"].pop(),
    lambda p: p.update(title="权威来源"),
])
def test_a_source_card_that_differs_from_the_request_is_rejected(edit):
    props = motion.build_source_card(SOURCES)
    edit(props)
    with pytest.raises(motion.InformationFault, match="INFORMATION_SOURCE_TEXT"):
        motion.check_source_card(props, SOURCES)


def test_source_text_is_not_a_reference_for_the_other_scenes():
    """The exemption is one-way: a number or token that only a source title carries is still invented on any other scene."""
    sources = [{"title": "GPT-6 Sol 在 2026 年发布 --models", "url": "https://example.com/a", "date": "2026-10-16"}]
    motion.build_source_card(sources)
    context = {"source_text": "价格降了。", "narration_plan": {"segments": [{"id": "n1", "text": "价格降了。"}]}}
    scene = {"id": "s1", "component": "concept_reveal", "headline": "价格降了", "props": {"title": "价格降了", "lines": ["时间：2026 年发布"]},
             "narration_segment_ids": ["n1"], "source_quotes": ["价格降了。"],
             "cues": [{"target_id": "title", "segment_id": "n1", "quote": "价格降了"}, {"target_id": "line:0", "segment_id": "n1", "quote": "价格降了"}]}
    assert any("2026" in issue for issue in motion.fidelity_issues({"scenes": [scene]}, context))
    scene["props"]["lines"] = ["选项：--models"]
    motion.validate_plan({"scenes": [scene]}, context)
    # ...and the card itself may carry such text, because it is request data (GPT-6, 2026, --models in a title).
    assert motion.build_source_card(sources)["sources"][0]["title"].startswith("GPT-6")


def test_source_card_limits_and_unusable_urls():
    with pytest.raises(motion.InformationFault, match="INFORMATION_SOURCE_CARD"):
        motion.build_source_card([{"title": "题" * 40, "url": "https://example.com", "date": "2026-10-16"}] * 3)  # does not fit
    with pytest.raises(motion.InformationFault, match="INFORMATION_SOURCE_CARD"):
        motion.build_source_card([{"title": "无域名", "url": "https://", "date": "2026-10-16"}])
    with pytest.raises(motion.InformationFault, match="INFORMATION_SOURCE_CARD"):
        motion.build_source_card([{"title": "坏日期", "url": "https://example.com", "date": "10/16/2026"}])


def card_scenes():
    def scene(sid, component, ids, cue_ids):
        return {"id": sid, "component": component, "props": {}, "narration_segment_ids": ids, "headline": sid, "source_quotes": ["x"],
                "cues": [{"target_id": "title", "segment_id": i, "quote": "字"} for i in cue_ids]}
    return scene


TEXTS = {"c2_n1": "第一句话在这里。", "c2_n2": "第二句话在这里。", "c2_n3": "最后一句话在这里。"}


def test_the_card_takes_the_last_segment_of_the_final_scene_when_it_can_spare_one():
    scene = card_scenes()
    scenes = [scene("c1_s1", "title_card", ["c1_n1"], ["c1_n1"]), scene("c2_s1", "concept_reveal", ["c2_n1", "c2_n2", "c2_n3"], ["c2_n1"])]
    placed, report = motion.place_source_card(scenes, ["c2_n1", "c2_n2", "c2_n3"], TEXTS, motion.build_source_card(SOURCES))
    assert report["placed"] and [s["id"] for s in placed] == ["c1_s1", "c2_s1", "c2_src"]
    assert placed[1]["narration_segment_ids"] == ["c2_n1", "c2_n2"] and placed[2]["narration_segment_ids"] == ["c2_n3"]
    assert [c["target_id"] for c in placed[2]["cues"]] == ["title", "line:0", "line:1"]
    assert all(c["quote"] and c["quote"] in TEXTS["c2_n3"] for c in placed[2]["cues"])


def test_the_card_replaces_a_single_segment_text_scene_but_never_a_data_scene():
    scene = card_scenes()
    props = motion.build_source_card(SOURCES)
    placed, report = motion.place_source_card([scene("c2_s1", "concept_reveal", ["c2_n1"], ["c2_n1"])], ["c2_n1"], TEXTS, props)
    assert report["placed"] and [s["component"] for s in placed] == ["source_card"]
    data = [scene("c2_s1", "title_card", ["c2_n1", "c2_n2"], ["c2_n1", "c2_n2"]), scene("c2_s2", "price_change", ["c2_n3"], ["c2_n3"])]
    placed, report = motion.place_source_card(data, ["c2_n1", "c2_n2", "c2_n3"], TEXTS, props)
    assert not report["placed"] and "data scene" in report["reason"] and placed == data  # c2_n2 is cued, so no scene can spare it
    spare = [scene("c2_s1", "title_card", ["c2_n1", "c2_n2"], ["c2_n1"]), scene("c2_s2", "big_number", ["c2_n3"], ["c2_n3"])]
    placed, report = motion.place_source_card(spare, ["c2_n1", "c2_n2", "c2_n3"], TEXTS, props)
    assert report["placed"] and [s["id"] for s in placed] == ["c2_s1", "c2_src", "c2_s2"]  # the card sits between, in narration order


def test_a_cue_on_the_last_segment_stops_the_scene_from_giving_it_up():
    scene = card_scenes()
    scenes = [scene("c2_s1", "concept_reveal", ["c2_n1", "c2_n2"], ["c2_n1", "c2_n2"])]
    placed, report = motion.place_source_card(scenes, ["c2_n1", "c2_n2"], TEXTS, motion.build_source_card(SOURCES))
    assert not report["placed"] and placed == scenes


class CardPipeline:
    def __init__(self, tmp_path, sources):
        (tmp_path / "input").mkdir()
        (tmp_path / "input" / "input.json").write_text(json.dumps({"sources": sources} if sources else {}), encoding="utf-8")
        self.PROJECT_INPUT = tmp_path / "input" / "source.txt"
        self.ARTIFACTS = tmp_path
        self.written = {}

    class G2Fault(RuntimeError):
        def __init__(self, code, message):
            self.code = code
            super().__init__(f"{code}: {message}")

    def write_json(self, path, value):
        self.written[path.name] = value


def test_a_source_card_scene_compiles_to_title_and_line_beats_and_never_without_sources(tmp_path):
    props = motion.build_source_card(SOURCES)
    scene = {"id": "c1_src", "component": "source_card", "props": props, "narration_segment_ids": ["n1"], "headline": "来源", "source_quotes": ["x"],
             "cues": [{"target_id": "title", "segment_id": "n1", "quote": "同"}, {"target_id": "line:0", "segment_id": "n1", "quote": "天"}, {"target_id": "line:1", "segment_id": "n1", "quote": "GPT"}]}
    rows = [{"id": "n1", "text": OPUS_TEXTS[1], "start_ms": 0, "end_ms": 4000}]
    board = motion.compile_storyboard({"scenes": [scene]}, {"rows": rows}, CardPipeline(tmp_path / "a", SOURCES) if (tmp_path / "a").mkdir() is None else None)
    assert [b["target_id"] for b in board["scenes"][0]["beats"]] == ["title", "line:0", "line:1"]
    (tmp_path / "b").mkdir()
    with pytest.raises(CardPipeline.G2Fault, match="INFORMATION_SOURCE_CARD"):
        motion.compile_storyboard({"scenes": [scene]}, {"rows": rows}, CardPipeline(tmp_path / "b", None))  # a card with no sources in the request
    (tmp_path / "c").mkdir()
    tampered = {**scene, "props": {**props, "sources": [{**props["sources"][0], "title": "伪造的来源"}, props["sources"][1]]}}
    with pytest.raises(CardPipeline.G2Fault, match="INFORMATION_SOURCE_TEXT"):
        motion.compile_storyboard({"scenes": [tampered]}, {"rows": rows}, CardPipeline(tmp_path / "c", SOURCES))


def test_the_model_cannot_plan_a_source_card_and_a_card_cannot_be_replanned():
    context = {**opus_context()}
    scene = price_scene() | {"component": "source_card", "props": motion.build_source_card(SOURCES)}
    with pytest.raises(motion.InformationFault, match="INVALID_INFORMATION_PLAN"):
        motion.validate_plan({"scenes": [scene]}, context)
    from g2 import scene_revision
    class P:
        class G2Fault(RuntimeError):
            def __init__(self, code, message):
                self.code = code
                super().__init__(message)
    with pytest.raises(P.G2Fault) as exc:
        scene_revision.replan_scene(P, {"component": "source_card"}, {}, {"presentation": {"preset": "information_motion"}}, "改一下")
    assert exc.value.code == "SCENE_REVISION_SCOPE"


def test_the_ir_takes_the_new_components_and_the_large_density(tmp_path):
    root, ir = information_ir_with(tmp_path, "price_change", price_scene()["props"], ["title", "line:0", "line:1"])
    validate_video_ir(ir, root)
    ir["scenes"][0]["beats"][0]["target_id"] = "line:2"
    with pytest.raises(ValidationFault, match="INVALID_BEAT_TARGET"):
        validate_video_ir(ir, root)  # two rows only
    ir["scenes"][0]["beats"][0]["target_id"] = "title"
    ir["presentation"] = {"preset": "information_motion", "density": "large"}
    validate_video_ir(ir, root)
    ir["presentation"]["density"] = "huge"
    with pytest.raises(ValidationFault):
        validate_video_ir(ir, root)
    ir["presentation"] = {"preset": "information_motion", "size": 3}
    with pytest.raises(ValidationFault):
        validate_video_ir(ir, root)
    root, card = information_ir_with(tmp_path / "card", "source_card", motion.build_source_card(SOURCES), ["title", "line:0", "line:1"])
    validate_video_ir(card, root)
    card["scenes"][0]["beats"][0]["target_id"] = "line:2"
    with pytest.raises(ValidationFault, match="INVALID_BEAT_TARGET"):
        validate_video_ir(card, root)
    card["scenes"][0]["beats"][0]["target_id"] = "title"
    card["scenes"][0]["props"]["sources"][0]["date"] = "昨天"
    with pytest.raises(ValidationFault, match="INVALID_COMPONENT_PROPS"):
        validate_video_ir(card, root)


def test_the_frozen_registry_still_lacks_the_extension_components():
    from g1.video_ir import load_registry, schema_action_catalog
    assert not {"price_change", "source_card"} & set(schema_action_catalog())
    assert {"price_change", "source_card"} <= set(load_registry()["components"])


def test_a_per_million_token_unit_overflows_a_large_bar_label_and_the_plan_is_told_where_the_basis_goes():
    """The one-point video on prompt caching failed twice on 「4 美元/百万 token」: the prompt now keeps the basis out of the unit."""
    chart = bar_scene(categories=["输入", "缓存读取"], values=[4, 0.2], unit="美元/百万 token")
    for large in (False, True):
        with pytest.raises(motion.InformationFault, match="INFORMATION_TEXT_FIT") as exc:
            motion.validate_text_fit(chart, large)
        assert "headline" in str(exc.value) and "美元" in str(exc.value)
        motion.validate_text_fit(bar_scene(categories=["输入", "缓存读取"], values=[4, 0.2], unit="美元"), large)
    assert "计量口径" not in motion.messages(opus_context())[0]["content"]

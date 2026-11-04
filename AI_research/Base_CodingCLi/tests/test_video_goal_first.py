"""The production default is material → script, with no content review loop."""
import json

import pytest

from core.video import credibility, store, writer
from tests.test_video_scripts import GOOD, REPORT, llm, script_json, video_env  # noqa: F401
from web.services import video_executor, video_render, video_workflow


def test_failed_writer_can_resume_from_saved_report_through_model_fallback(video_env, monkeypatch):
    from core.llm import ChatResponse, LLMClient, LLMConfig

    report = video_env / "report.md"
    report.write_text(REPORT)
    script = store.create_script("report_digest", {"title": "定时任务", "from_report": str(report)})
    store.update_script(script["id"], status="FAILED", report_path=str(report), error="content_filter", stage="写稿")
    calls = []
    generated = script_json(300, citations={(0, 0): [GOOD]})

    def answer(client, messages, **kwargs):
        calls.append((client._active_model(), messages))
        return ChatResponse(content="", finish_reason="content_filter", model="primary") if len(calls) == 1 else ChatResponse(
            content=generated, finish_reason="stop", model="backup")

    monkeypatch.setattr(LLMClient, "_make_client", lambda self: None)
    monkeypatch.setattr(LLMClient, "_chat_cancellable", answer)
    config = LLMConfig(model="primary", fallback_models=["backup"], base_url="http://llm.invalid/v1")
    assert video_workflow.run_rewrite(script["id"], previous_status="FAILED", llm_config=config) == "DRAFT"
    after = store.get_script(script["id"])
    version = store.get_version(script["id"], 1)
    assert after["report_path"] == str(report) and report.read_text() == REPORT
    assert after["error"] is None and after["stage"] is None
    assert [model for model, _ in calls] == ["primary", "backup"] and calls[0][1] == calls[1][1]
    assert REPORT in calls[0][1][1]["content"]
    assert version["sections"][0]["paragraphs"][0]["text"] == json.loads(generated)["sections"][0]["paragraphs"][0]["text"]
    assert version["meta"]["model"] == "backup"
    assert version["meta"]["model_switches"][0]["from"] == "primary"


@pytest.mark.parametrize("recipe", ["flash", "point_short", "deep_dive", "report_digest", "topic_digest",
                                    "action_list", "top_finding", "ai-developer-roundup-long",
                                    "ai-developer-comparison-long"])
def test_every_production_recipe_generates_directly_and_can_continue_without_an_audit(video_env, llm, recipe):
    report = video_env / "report.md"
    report.write_text(REPORT)
    payload = json.loads(script_json(3000, citations={(0, 0): [GOOD]}))
    payload["sections"][0]["paragraphs"][0]["text"] = "材料没有提供这个数字。这是自动作者的讲法。"
    llm.replies = [json.dumps(payload)]
    script = store.create_script(recipe, {"title": "素材试写", "from_report": str(report),
                                          "fields": {"目标受众": "开发者"}})
    assert video_workflow.run(script["id"], started_by="editor", llm_config=object()) == "DRAFT"
    script = store.get_script(script["id"])
    version = store.get_version(script["id"], 1)
    assert len(llm.calls) == 1
    assert not any((llm.fact_calls, llm.brief_calls, llm.audit_calls, llm.style_calls,
                    llm.reader_calls, llm.revise_calls, llm.editorial_calls, llm.headline_calls))
    assert version["sections"][0]["paragraphs"][0]["text"] == payload["sections"][0]["paragraphs"][0]["text"]
    assert credibility.label(version)["tier"] == "generated"
    assert version["meta"]["checks"]["model_ran"] is False
    assert version["meta"]["checks"]["summary"]["passed"] is False  # no fabricated review success
    assert video_executor.steps_of(script)[2]["state"] == "SKIPPED"
    decision = video_executor.advance_auto(script, {}, can_start_script=True, can_start_render=False)
    assert not decision["hold"] and not decision["took"]["script"]
    assert store.get_script(script["id"])["status"] == "APPROVED"


def test_writer_gets_the_material_and_audience_without_old_production_rules(llm):
    llm.replies = [script_json(300)]
    writer.write(report=REPORT, citation_check=None, topic={"title": "素材试写", "fields": {
        "目标受众": "开发者", "核心角度": "强制旧角度", "建议大纲": "强制旧大纲", "评分": "9/10"}},
        column_style="陪开发者看懂开源工具解决了什么问题", target_minutes=1, paragraph_units=(20, 40), llm_config=object())
    system, request = [m["content"] for m in llm.calls[0]]
    assert writer.GOAL in system and REPORT in request and "开发者" in request
    assert "陪开发者看懂开源工具解决了什么问题" in request
    assert not any(x in system + request for x in ("强制旧角度", "强制旧大纲", "时长偏好", "9/10"))


@pytest.mark.parametrize("mode", ["individual", "episode"])
@pytest.mark.parametrize("original", [None, "原始材料的完整操作过程"])
def test_all_default_writer_inputs_share_authoring_and_material_guidance(llm, mode, original):
    llm.replies = [script_json(300)]
    written = writer.write(report=REPORT, citation_check=None,
                           topic={"title": "本期内容", "content_mode": mode}, column_style="栏目方向",
                           target_minutes=1, llm_config=None, original_material=original)
    system, request = [m["content"] for m in llm.calls[0]]
    assert writer.AUTHORING_PROMPT in system
    assert "正文就是观众将听到的口播" in system and "让开场引起兴趣" in system
    assert "研究备忘仅作内部取材背景" in system and "制作备忘留在制作侧" in system
    assert REPORT in request and len(llm.calls) == 1 and not llm.audit_calls
    assert written.meta["prompt_version"] == writer.PROMPT_VERSION
    if original:
        assert original in request
        if mode == "episode":
            assert "各独立内容中的具体例子持续推进" in request
            assert "同一问题的章节沿用共同主例子" in request
            assert "让同一个具体任务在中段持续推进" not in request
            assert "配方参考片长约" not in request
        else:
            assert "让同一个具体任务在中段持续推进" in request


def test_recipe_edit_reaches_the_actual_workflow_author(video_env, llm):
    """Changing a recipe must change its author request, not only the settings screen."""
    report = video_env / "report.md"
    report.write_text(REPORT)
    path = video_env / "config/video_recipes/report_digest.yaml"
    original = path.read_text()
    first_direction = "让开发者发现开源工具之间有趣的联系"
    second_direction = "陪开发者从一个使用疑问走到理解"
    for direction in (first_direction, second_direction):
        import yaml
        data = yaml.safe_load(original)
        data["writing"]["style"] = direction
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
        llm.replies = [script_json(300)]
        script = store.create_script("report_digest", {"title": "素材试写", "from_report": str(report)})
        assert video_workflow.run(script["id"], llm_config=object()) == "DRAFT"
        assert direction in llm.calls[-1][1]["content"]
        assert store.get_version(script["id"], 1)["meta"]["creative_direction"] == direction
    assert first_direction not in llm.calls[-1][1]["content"]


def test_reopening_a_completed_generation_does_not_start_an_unrequested_audit(video_env, llm):
    report = video_env / "report.md"
    report.write_text(REPORT)
    llm.replies = [script_json(300)]
    script = store.create_script("report_digest", {"title": "素材试写", "from_report": str(report)})
    video_workflow.run(script["id"], llm_config=object())
    video_workflow.run(script["id"], llm_config=object())
    assert len(llm.calls) == 1 and not llm.audit_calls
    assert store.get_script(script["id"])["current_version"] == 1


def test_manual_edit_does_not_reintroduce_content_rules(video_env, llm):
    report = video_env / "report.md"
    report.write_text(REPORT)
    llm.replies = [script_json(300)]
    script = store.create_script("report_digest", {"title": "素材试写", "from_report": str(report)})
    video_workflow.run(script["id"], llm_config=object())
    number = video_workflow.save_edit(script["id"], title="修改稿", edited_by="editor", sections=[{
        "title": "正文", "paragraphs": [{"text": "材料没有提供数字。", "citations": []}]}])
    version = store.get_version(script["id"], number)
    assert version["meta"]["checks"]["mode"] == "on_request"
    assert video_workflow.rule_checks(version, None)["issues"] == []


def test_the_generated_episode_direction_reaches_the_video_request(video_env, llm):
    report = video_env / "report.md"
    report.write_text(REPORT)
    llm.replies = [script_json(300)]
    script = store.create_script("report_digest", {"title": "素材试写", "from_report": str(report),
                                                  "fields": {"目标受众": "开发者"}})
    video_workflow.run(script["id"], llm_config=object())
    version = store.get_version(script["id"], 1)
    video_executor.advance_auto(store.get_script(script["id"]), {}, can_start_script=True, can_start_render=False)
    render = video_render.create(store.get_script(script["id"]))
    intent = render["request"]["creative_intent"]
    assert version["meta"]["creative_direction"] in intent["purpose"]
    assert intent["audience"] == "开发者" and intent["focus"] == version["title"]


def _episode_json() -> str:
    paragraph = lambda text: {"text": text, "citations": []}
    return json.dumps({"title": "本期", "sections": [
        {"title": "开场：先看被打断的任务", "episode_role": "opening", "paragraphs": [paragraph("今天有两件事。")]},
        {"title": "额度上调", "episode_role": "item", "paragraphs": [paragraph("先看额度。")]},
        {"title": "收尾：今天先改哪一个", "episode_role": "closing", "paragraphs": [paragraph("我们下期见。")]}]},
        ensure_ascii=False)


def test_a_programme_opens_with_the_fixed_greeting_and_viewer_facing_titles(llm):
    llm.replies = [_episode_json()]
    written = writer.write(report=REPORT, citation_check=None, topic={"title": "本期", "content_mode": "episode"},
                           column_style="", target_minutes=1, llm_config=None, greeting="大家好，欢迎收看今日快讯。")
    system = llm.calls[0][0]["content"]
    assert "「大家好，欢迎收看今日快讯。」" in system and "不再另外问候" in system and "道别" in system
    assert "最抓人的那一条" in system and "直接切换" in system and "不重新打招呼" in system
    assert "不逐条复述" in system and writer.STYLE_RULE in system
    opening = written.sections[0]
    assert [p["text"] for p in opening["paragraphs"]] == ["大家好，欢迎收看今日快讯。", "今天有两件事。"]
    assert [s["title"] for s in written.sections] == [writer.OPENING_TITLE, "额度上调", writer.CLOSING_TITLE]
    assert written.length_check["sections"][0]["spoken_length"] >= len("大家好欢迎收看今日快讯今天有两件事")


def test_a_greeting_the_author_already_said_is_not_repeated_and_no_greeting_adds_nothing(llm):
    payload = json.loads(script_json(300))
    payload["sections"][0]["paragraphs"][0]["text"] = "大家好，欢迎收看AI 快讯。今天讲一件事。"
    llm.replies = [json.dumps(payload, ensure_ascii=False), script_json(300)]
    said = writer.write(report=REPORT, citation_check=None, topic={"title": "本期"}, column_style="",
                        target_minutes=1, llm_config=None, greeting="大家好，欢迎收看AI 快讯。")
    plain = writer.write(report=REPORT, citation_check=None, topic={"title": "本期"}, column_style="",
                         target_minutes=1, llm_config=None)
    assert [p["text"] for p in said.sections[0]["paragraphs"]][:1] == ["大家好，欢迎收看AI 快讯。今天讲一件事。"]
    assert "问候" not in llm.calls[1][0]["content"]
    assert plain.sections[0]["paragraphs"][0]["text"] == json.loads(script_json(300))["sections"][0]["paragraphs"][0]["text"]
    assert "起承转合" in llm.calls[1][0]["content"]


def test_a_greeting_the_author_worded_differently_is_replaced_not_heard_twice(llm):
    payload = json.loads(script_json(300))
    payload["sections"][0]["paragraphs"][0]["text"] = "大家好！欢迎来到本期快讯。今天讲一件事。"
    payload["sections"][0]["paragraphs"].insert(0, {"text": "哈喽，各位。", "citations": []})
    llm.replies = [json.dumps(payload, ensure_ascii=False)]
    written = writer.write(report=REPORT, citation_check=None, topic={"title": "本期"}, column_style="",
                           target_minutes=1, llm_config=None, greeting="大家好，欢迎收看AI 快讯。")
    assert [p["text"] for p in written.sections[0]["paragraphs"]][:2] == ["大家好，欢迎收看AI 快讯。", "今天讲一件事。"]
    payload["sections"][0]["paragraphs"] = [{"text": "大家好，今天聊三件事：额度、价格和账号。", "citations": []}]
    llm.replies = [json.dumps(payload, ensure_ascii=False)]
    written = writer.write(report=REPORT, citation_check=None, topic={"title": "本期"}, column_style="",
                           target_minutes=1, llm_config=None, greeting="大家好，欢迎收看AI 快讯。")
    assert [p["text"] for p in written.sections[0]["paragraphs"]] == ["大家好，欢迎收看AI 快讯。", "今天聊三件事：额度、价格和账号。"]


def test_the_greeting_comes_from_the_column_name_or_its_own_line_and_never_says_a_date():
    from core.video import episodes

    flash = {"column": "ai_flash", "column_name": "AI 快讯"}
    assert episodes.greeting({}, flash) == "大家好，欢迎收看今日快讯。"
    topic = {"episode": {"publication": {"label": "ChatGPT 订阅与支付", "title": "ChatGPT 订阅与支付｜2026年11月01日"}}}
    assert episodes.greeting(topic, {"column": "chatgpt_t07_subscription"}) == "大家好，欢迎收看ChatGPT 订阅与支付。"
    assert episodes.greeting(topic, {**flash, "greeting": "各位好，这里是今日快讯。"}) == "各位好，这里是今日快讯。"
    assert episodes.greeting({}, {}) == ""

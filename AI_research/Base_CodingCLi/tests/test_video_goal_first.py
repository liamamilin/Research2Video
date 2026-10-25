"""The production default is material → script, with no content review loop."""
import json

import pytest

from core.video import credibility, store, writer
from tests.test_video_scripts import GOOD, REPORT, llm, script_json, video_env  # noqa: F401
from web.services import video_executor, video_render, video_workflow


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

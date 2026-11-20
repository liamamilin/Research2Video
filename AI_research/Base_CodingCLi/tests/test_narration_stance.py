"""Narrow internal memos without speaking a broken join or inventing an official denial."""
import json

from core.video import article_writer, manuscript_workflow as workflow, production, writer
from core.video.narration_stance import broken_join, defects, repair
from tests.test_video_scripts import ROOT, llm, video_env  # noqa: F401
from web.services import video_executor, video_render, video_workflow

ESSAY = (
    "这里要说清楚：材料没有放完整的两篇作文全文，也没有给具体分数。"
    "我们说的是按评分标准推断的风险，不是它实际被打了多少分。"
)
GEMINI = "第二，材料没有写明订阅档位，而 Gemini 4 Hydrogen 在免费版里就有入口。"
SAMPLE = "单样本不能判断免费版和付费版谁更强。"
LIMIT = "我们说的是按评分标准推断的风险，不是它实际被打了多少分。"

KEEP_LIMITS = [
    "这次只有 12 个样本，不能推广到所有患者。",
    "这篇是预印本，结果还没有独立复现。",
    "评测条件没有披露，不能据此比较两家的速度。",
    "厂商称快了 30%，我们没有独立验证。",
    "故障已经标记 Resolved，不代表你每一个失败的请求都已经恢复。",
    "价格窗口仍是 3 月 31 日前，地区条件以结算页显示的为准。",
    "这个功能没有放到生产环境。",
    "材料没有放到生产环境。",
    "这种材料没有毒性。",
]


def test_essay_memo_is_removed_and_the_score_limit_stays():
    spoken, notes = repair(ESSAY)
    assert "材料没有放" not in spoken
    assert "这里要说清楚" not in spoken
    assert "也没有给具体分数" not in spoken
    assert LIMIT in spoken
    assert "官方" not in spoken and "这次没有讲" not in spoken and "不支持" not in spoken
    assert notes and notes[0]["needs_attention"] and not notes[0]["consequential"]
    assert not broken_join(spoken)


def test_subscription_gap_is_not_spoken_as_a_dangling_conjunction():
    spoken, notes = repair(GEMINI + SAMPLE)
    assert spoken == SAMPLE
    assert "第二，而" not in spoken
    assert "Gemini 4 Hydrogen 在免费版里就有入口" not in spoken
    assert notes[0]["needs_attention"] and not notes[0]["consequential"]
    assert "官方" not in spoken and "不支持" not in spoken
    bare, bare_notes = repair(GEMINI)
    assert bare == "" and bare_notes[0]["spoken_instead"] == ""
    assert "第二，而 Gemini 4 Hydrogen 在免费版里就有入口。" != bare


def test_existing_limits_and_real_placement_stay_verbatim():
    for text in KEEP_LIMITS:
        assert repair(text) == (text, []), text
        assert not defects(text)


def test_consequential_gap_holds_a_new_draft_and_an_approved_script_still_renders(video_env, llm, monkeypatch):
    from core.video import store
    report = video_env / "report.md"
    report.write_text("公开测试集上，折叠误差从 8 降到 3。")
    payload = {"title": "误差", "sections": [{"title": "结果", "paragraphs": [{
        "text": "新模型把蛋白质折叠的误差从 8 降到 3。材料没有给对照组的样本量，所以我不会编。",
        "citations": [],
    }]}]}
    llm.replies = [json.dumps(payload, ensure_ascii=False)]
    script = store.create_script("report_digest", {"title": "误差", "from_report": str(report)})
    assert video_workflow.run(script["id"], llm_config=object()) == "DRAFT"
    saved = store.get_version(script["id"], 1)
    assert "误差从 8 降到 3" in saved["sections"][0]["paragraphs"][0]["text"]
    assert "材料没有给" not in saved["sections"][0]["paragraphs"][0]["text"]
    assert "我不会编" not in saved["sections"][0]["paragraphs"][0]["text"]
    assert saved["meta"]["narration_blocks_auto_approval"] is True
    assert saved["meta"]["narration_unresolved"]
    created = []
    monkeypatch.setattr(video_render, "create", lambda *args, **kwargs: created.append(True) or {"id": "r", "status": "QUEUED"})
    monkeypatch.setattr(video_render, "start", lambda *_args, **_kwargs: None)
    decision = video_executor.advance_auto(store.get_script(script["id"]), {}, can_start_script=True, can_start_render=True)
    assert store.get_script(script["id"])["status"] == "DRAFT"
    assert decision["hold"] and decision["item"]["gate"]["approved"] is False
    assert created == []

    approved = store.create_script("report_digest", {"title": "已定稿", "from_report": str(report)})
    store.add_version(approved["id"], title="已定稿",
                      sections=[{"title": "价格", "paragraphs": [{"text": "Plus 是每月 20 美元。", "citations": []}]}],
                      length_check={"status": "OK"}, edit_type="GENERATED",
                      meta={"checks": {"mode": "on_request", "model_ran": False, "issues": [], "summary": {}}})
    store.update_script(approved["id"], status="APPROVED", approved_version=1)
    video_executor.advance_auto(store.get_script(approved["id"]), {}, can_start_script=True, can_start_render=True)
    assert created == [True]
    assert store.get_script(approved["id"])["status"] == "APPROVED"


def test_current_rollout_leaves_t05_and_flash_daily_without_the_job_profile():
    config, jobs = str(ROOT / "config"), str(ROOT / "jobs")
    video = {}
    assert workflow.profile_for(
        {"production": "chatgpt_t05_comparison", "column_key": "chatgpt_acquisition", "topic": {}},
        config, jobs, video) is None
    each = workflow.profile_for(
        {"production": "flash_each", "column_key": "flash-nr",
         "topic": {"matrix_dedup": {"job": "monitoring/ai_flash_discovery"}}},
        config, jobs, video)
    daily = workflow.profile_for(
        {"production": "flash_daily", "column_key": "flash-nr",
         "topic": {"content_mode": "episode", "matrix_dedup": {"job": "monitoring/ai_flash_discovery"}}},
        config, jobs, video)
    assert each is workflow.JOB_PROMPTS["monitoring/ai_flash_discovery"]
    assert daily is None
    assert production.get(config, jobs, "chatgpt_t05_comparison").auto == 1
    assert "chatgpt_acquisition/t05_comparison" not in workflow.enabled_jobs(config, video)


def test_manual_edit_of_a_memo_cannot_be_auto_approved(video_env, llm):
    from core.video import store
    report = video_env / "report.md"
    report.write_text("公开测试集。")
    llm.replies = [json.dumps({"title": "原稿", "sections": [{"title": "正文", "paragraphs": [
        {"text": "先看已经成立的结果。", "citations": []}]}]}, ensure_ascii=False)]
    script = store.create_script("report_digest", {"title": "原稿", "from_report": str(report)})
    assert video_workflow.run(script["id"], llm_config=object()) == "DRAFT"
    number = video_workflow.save_edit(script["id"], title="修改稿", edited_by="editor", sections=[{
        "title": "正文", "paragraphs": [{"text": ESSAY, "citations": []}]}])
    version = store.get_version(script["id"], number)
    assert LIMIT in version["sections"][0]["paragraphs"][0]["text"]
    assert "材料没有放" not in version["sections"][0]["paragraphs"][0]["text"]
    assert version["meta"]["narration_unresolved"]
    assert version["meta"]["checks"]["mode"] == "on_request"
    decision = video_executor.advance_auto(store.get_script(script["id"]), {}, can_start_script=True, can_start_render=True)
    assert decision["hold"] and decision["item"]["gate"]["approved"] is False
    assert store.get_script(script["id"])["status"] == "DRAFT"
    video_executor.approve(script["id"], number, "editor")
    assert store.get_script(script["id"])["status"] == "APPROVED"


def test_read_aloud_keeps_every_character_of_the_memo():
    column = type("C", (), {"target_minutes": 1, "voice_id": None})()
    written = article_writer.draft(
        {"title": "原文", "text": ESSAY}, mode="read", kind="information",
        topic={"title": "原文"}, column=column, config=None)
    assert written.sections[0]["paragraphs"][0]["text"] == ESSAY
    assert written.meta["generation"] == "article_verbatim"


def test_polish_does_not_keep_a_model_broken_join(llm):
    from core.video import polish
    sections = [{"title": "比较", "paragraphs": [{"text": GEMINI + SAMPLE, "citations": []}]}]
    llm.polish_replies = [json.dumps({"paragraphs": [{
        "id": "S1P1", "text": "第二，而 Gemini 4 Hydrogen 在免费版里就有入口。" + SAMPLE}]}, ensure_ascii=False)]
    out, record = polish.polish(llm(None), sections)
    spoken = out[0]["paragraphs"][0]["text"]
    assert "第二，而" not in spoken
    assert SAMPLE in spoken
    assert record.get("rejected") or any(change.get("fallback") for change in record["changes"])


def test_revision_is_narrowed_before_it_is_stored(monkeypatch):
    draft = writer.WrittenScript(
        "标题", [{"title": "比较", "paragraphs": [{"text": "Gemini 在免费版里就有入口。" + SAMPLE}]}],
        {}, {})
    column = type("C", (), {"script_style": "讲清差别", "target_minutes": 1, "voice_id": None})()

    def chat(client, prompt, request, schema, *, temperature):
        if schema is workflow.FocusedReading:
            return schema(takeaway="入口", keep_quote="免费版", revision="把档位说顺", targets=["S1P1"])
        return schema(paragraphs=[{"id": "S1P1", "text": GEMINI + SAMPLE}])

    monkeypatch.setattr(workflow, "call_json", chat)
    monkeypatch.setattr(workflow, "LLMClient", lambda *args, **kwargs: object())
    revised = workflow.revise_passage(
        draft, report="免费版有入口。", topic={"title": "比较", "fields": {}},
        llm_config=object(), column=column)
    text = revised.sections[0]["paragraphs"][0]["text"]
    assert "第二，而" not in text and "材料没有写明" not in text
    assert SAMPLE in text
    assert revised.meta["narration_unresolved"]

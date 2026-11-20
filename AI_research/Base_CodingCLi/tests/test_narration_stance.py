"""Narrow internal memos without speaking a broken join or inventing an official denial."""
import json

import pytest

from core.video import article_writer, manuscript_workflow as workflow, polish, production, writer
from core.video.article_writer import draft as article_draft
from core.video.narration_stance import LISTENING_DIRECTION, broken_join, defects, launders, repair
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

NEGATIVES = [
    "Gemini、Mistral、Grok 的地区清单，这批材料里没有，所以我不能替它们下结论。你自己去官网查一下，比听我说更靠谱。",
    "Team 也含月度 API credits，不过材料没给具体额度。",
    "材料没有说豆包会代打电话、设预算上限、逐项确认付款或给通话转录。",
    "材料没有给 Code Mode 的跑分，所以我不会说它固定快多少。",
    "材料没有给具体倍率数字，我也不会编。",
    "还有两点材料没交代，别自己脑补。",
    "我不会替它们打包票。也不能替它们下结论。你最好自己再看看官网，比听我讲稳妥。",
    "我没有乱说。",
]
KEEP = [
    "故障已经标记 Resolved，不代表你每一个失败的请求都已经恢复。",
    "这次只测了 3 个编程任务，不能推广到所有模型。",
    "写提示词时，把没核对的数字标成待核实。",
    "用户原话是「材料没有给具体倍率数字，我也不会编。」本期只比较已经给出的价格。",
    "教程分析这句不合格旁白：「还有两点材料没交代，别自己脑补。」",
    "这种材料没有毒性。",
    "Plus 是每月 20 美元，价格从 11 月 18 日起生效。",
]

def test_shared_direction_reaches_every_author_and_revision_prompt():
    assert LISTENING_DIRECTION in writer.AUTHORING_PROMPT
    assert LISTENING_DIRECTION in writer.SYSTEM_PROMPT
    assert LISTENING_DIRECTION in writer.EPISODE_PROMPT
    assert LISTENING_DIRECTION in writer.EXPLAINER_EPISODE_PROMPT
    for profile in workflow.JOB_PROMPTS.values():
        system = writer.author_system_prompt(
            {"content_mode": "episode"}, {}, episode_prompt=profile.episode_prompt, episode_lead=profile.episode_lead,
        )
        assert LISTENING_DIRECTION in system
    assert LISTENING_DIRECTION in polish.SYSTEM_PROMPT

def test_source_attribution_is_dropped_while_facts_remain():
    assert repair("支持的模型，材料点了四个名字：GPT-6.2 Astra、GPT-6.2 Sol。")[0] == (
        "支持的模型，有四个名字：GPT-6.2 Astra、GPT-6.2 Sol。")
    assert repair("但公开材料说脚本没有执行。")[0] == "但脚本没有执行。"
    assert repair("材料显示周配额掉得快。")[0] == "周配额掉得快。"
    kept = "一位开发者的实测记录里写了两件事。"
    assert repair(kept) == (kept, [])


def test_sequel_asides_are_dropped_while_keeping_the_action():
    text = ("Canva 给 Free 和 Pro 用户上了两个开关，不额外收费。"
            "别的视频讲的是它发布了什么，这条只讲你今天怎么用上：从哪进、按什么顺序按、按完会发生什么。")
    assert repair(text)[0] == (
        "Canva 给 Free 和 Pro 用户上了两个开关，不额外收费。"
        "从哪进、按什么顺序按、按完会发生什么。")
    assert repair("今天不重复讲它上线，直接讲你打开后怎么用。")[0] == "直接讲你打开后怎么用。"

def test_examples_keep_facts_and_do_not_invent_absence():
    spoken = {text: repair(text)[0] for text in NEGATIVES}
    assert spoken[NEGATIVES[0]] == ""
    assert spoken[NEGATIVES[1]] == "Team 也含月度 API credits。"
    assert spoken[NEGATIVES[2]] == ""
    assert spoken[NEGATIVES[3]] == ""
    assert "这次比较没有包括" not in "".join(spoken.values())
    assert "这次没有讲" not in "".join(spoken.values())
    assert "官方" not in "".join(spoken.values())
    mixed = repair("材料没给具体额度，Team 也含月度 API credits。")[0]
    assert mixed == "Team 也含月度 API credits。"
    withdrawn, withdrawn_notes = repair("五家都能在中国区用，不过材料没给 Gemini 的地区，所以我不能下结论。")
    assert withdrawn == "" and withdrawn_notes[0]["needs_attention"]
    comparison = repair("ChatGPT 和 Claude 能在中国区用。Gemini、Mistral、Grok 的地区清单，这批材料里没有，所以我不能替它们下结论。")[0]
    assert comparison == "ChatGPT 和 Claude 能在中国区用。"
    kept_fact = repair("上限是 5 小时，不过材料没给具体倍率，我也不会编。")[0]
    assert kept_fact == "上限是 5 小时。"
    piled = repair("先看免费额度。我不会替它们打包票。也不能替它们下结论。你最好自己再看看官网，比听我讲稳妥。材料没有给具体倍率数字，我也不会编。")[0]
    assert piled == "先看免费额度。"
    for text in KEEP:
        assert repair(text) == (text, [])
    assert launders(NEGATIVES[2], "豆包不会代打电话。")
    assert launders("材料没给具体额度。", "官方没有公布具体额度。")
    scope, scope_notes = repair("五家都能直接开通，不过材料没给 Gemini 的地区，所以不能说五家都能。")
    assert scope == "所以不能说五家都能。"
    assert not any(note["needs_attention"] for note in scope_notes)
    assert "这次没有讲" not in scope and "这次比较没有包括" not in scope

def test_paragraph_keeps_later_facts_and_does_not_grow_a_disclaimer():
    paragraph = (
        "Team 也含月度 API credits。"
        "不过材料没给具体额度。"
        "页面上先按 20 美元这一档来看。"
        "价格可能在下一次账单日调整，目前还没验证。"
        "实验只测了室温，这种样品材料没有覆盖低温。"
        "所以不能说五家都能直接开通。"
    )
    spoken, notes = repair(paragraph)
    assert spoken == (
        "Team 也含月度 API credits。"
        "页面上先按 20 美元这一档来看。"
        "价格可能在下一次账单日调整，目前还没验证。"
        "实验只测了室温，这种样品材料没有覆盖低温。"
        "所以不能说五家都能直接开通。"
    )
    assert "材料没给" not in spoken
    assert "这次没有讲" not in spoken and "这次比较没有包括" not in spoken
    assert notes and notes[0]["spoken_instead"] == ""
    extra = repair("底稿没写明企业版价格，Team 仍是每月 20 美元。写提示词时把没核对的数标成待核实。")[0]
    assert extra == "Team 仍是每月 20 美元。写提示词时把没核对的数标成待核实。"
    quoted = "讲师把不合格旁白念出来：「材料没有给具体倍率数字，我也不会编。」下一句才是操作：先点开引用。"
    assert repair(quoted) == (quoted, [])

def test_sections_keep_sibling_facts_when_one_paragraph_is_only_a_gap():
    from core.video.narration_stance import settle_sections
    sections = [{"title": "比较", "paragraphs": [
        {"text": "ChatGPT 和 Claude 能在中国区用。", "citations": ["https://example.com/a"]},
        {"text": NEGATIVES[0], "citations": []},
        {"text": "DeepSeek 的免费额度先到顶。价格可能还会变，目前还没验证。", "citations": ["https://example.com/b"]},
    ]}]
    out, record = settle_sections(sections)
    spoken = [paragraph["text"] for paragraph in out[0]["paragraphs"]]
    assert spoken == [
        "ChatGPT 和 Claude 能在中国区用。",
        "DeepSeek 的免费额度先到顶。价格可能还会变，目前还没验证。",
    ]
    assert out[0]["paragraphs"][0]["citations"] == ["https://example.com/a"]
    assert any(item.get("needs_attention") for item in record["gaps"])
    assert "这次没有讲" not in "\n".join(spoken)

def test_direction_prefixes_planning_and_revision_without_replacing_the_job():
    assert workflow.PLAN.startswith(LISTENING_DIRECTION) and workflow.PLAN.count(LISTENING_DIRECTION) == 1
    assert "viewer_value" in workflow.PLAN
    assert workflow.READ.count(LISTENING_DIRECTION) == 1
    assert "沿用上游" in writer.AUTHORING_PROMPT and "事实" in writer.AUTHORING_PROMPT
    assert writer.AUTHORING_PROMPT.count(LISTENING_DIRECTION) == 1
    assert "不要编官方没公布" in LISTENING_DIRECTION
    assert LISTENING_DIRECTION in polish.SYSTEM_PROMPT
    from core.video import audit
    assert audit.REVISE_PROMPT.startswith(LISTENING_DIRECTION)
    assert "根据提供的反馈修改成稿" in audit.REVISE_PROMPT
    for profile in workflow.JOB_PROMPTS.values():
        read = workflow._with_direction(profile.read)
        revise = workflow._with_direction(profile.revise)
        assert read.count(LISTENING_DIRECTION) == 1
        assert revise.count(LISTENING_DIRECTION) == 1
        assert profile.read.strip() in read
        assert profile.revise.strip() in revise
        assert not profile.draft.startswith("这次没有讲")

def test_audit_keeps_a_useful_sentence_when_the_rewrite_launders_or_empties(monkeypatch):
    from core.video import audit
    sections = [{"title": "价格", "paragraphs": [
        {"text": "Team 也含月度 API credits。", "citations": []},
        {"text": "材料没有说豆包会代打电话。", "citations": []},
    ]}]

    def call_json(client, prompt, request, schema, *, temperature):
        assert prompt.startswith(LISTENING_DIRECTION)
        return schema(paragraphs=[
            {"id": "S1P1", "text": "官方没有公布 Team 的额度。", "citations": []},
            {"id": "S1P2", "text": "豆包不会代打电话。", "citations": []},
        ])

    monkeypatch.setattr(audit, "call_json", call_json)
    out, _changed = audit.revise(object(), sections, "报告", {"S1P1": ["额度"], "S1P2": ["能力"]})
    assert out[0]["paragraphs"][0]["text"] == "Team 也含月度 API credits。"
    assert "evidence" not in out[0]["paragraphs"][0]
    assert out[0]["paragraphs"][1]["text"] == "材料没有说豆包会代打电话。"
    assert out[0]["paragraphs"][1]["evidence"] == "NEEDS_REVIEW"
    assert "不要自动补「这次没有讲」" in out[0]["paragraphs"][1]["review_reason"]
    assert "官方没公布" in out[0]["paragraphs"][1]["review_reason"]

def test_polish_rejects_laundering_and_a_failed_call_still_keeps_the_fact(llm):
    sections = [{"title": "价格", "paragraphs": [
        {"text": "Team 也含月度 API credits，不过材料没给具体额度。", "citations": []},
        {"text": "ChatGPT 和 Claude 能在中国区用。", "citations": []},
    ]}]
    llm.polish_replies = [json.dumps({"paragraphs": [{
        "id": "S1P1", "text": "Team 也含月度 API credits，官方没有公布具体额度。",
    }]})]
    out, record = polish.polish(llm(None), sections)
    assert out[0]["paragraphs"][0]["text"] == "Team 也含月度 API credits。"
    assert "官方" not in out[0]["paragraphs"][0]["text"]
    assert record["rejected"]
    failed = [{"title": "比较", "paragraphs": [
        {"text": NEGATIVES[0], "citations": []},
        {"text": "DeepSeek 的免费额度先到顶。", "citations": []},
    ]}]
    llm.polish_replies = ["不是 JSON"] * 5
    out, record = polish.polish(llm(None), failed)
    spoken = "\n".join(p["text"] for s in out for p in s["paragraphs"])
    assert "我不能替" not in spoken and "DeepSeek" in spoken and "这次比较没有包括" not in spoken
    assert "error" in record
    again, second = polish.polish(llm(None), out)
    assert again == out and not any(item["rule"] == "audience_stance" for item in second["findings"])

def test_repeated_paraphrases_collapse_without_a_second_model_call(llm):
    sections = [{"title": "条目", "paragraphs": [
        {"text": NEGATIVES[6], "citations": []},
        {"text": "先看已经公布的免费额度。", "citations": []},
    ]}]
    out, record = polish.polish(llm(None), sections)
    spoken = "\n".join(p["text"] for s in out for p in s["paragraphs"])
    assert "打包票" not in spoken and "先看已经公布的免费额度。" in spoken
    assert record["gaps"]
    before = len(llm.polish_calls)
    polish.polish(llm(None), out)
    assert len(llm.polish_calls) == before

def test_flash_each_and_flash_daily_share_the_author_and_only_each_has_the_job_profile():
    each = production.get(str(ROOT / "config"), str(ROOT / "jobs"), "flash_each")
    daily = production.get(str(ROOT / "config"), str(ROOT / "jobs"), "flash_daily")
    manual = production.get(str(ROOT / "config"), str(ROOT / "jobs"), "ai_media_deep_dive")
    assert each.usable and daily.usable and each.reverify is False and daily.reverify is False
    assert each.recipe == daily.recipe == "flash-nr"
    assert each.split == "each" and daily.split == "whole"
    assert manual.auto == 1 and manual.usable
    job = "monitoring/ai_flash_discovery"
    config = {"manuscript_jobs": [job]}
    each_profile = workflow.profile_for(
        {"production": "flash_each", "topic": {"matrix_dedup": {"job": job}}, "column_key": "flash-nr"},
        str(ROOT / "config"), str(ROOT / "jobs"), config)
    daily_profile = workflow.profile_for(
        {"production": "flash_daily", "topic": {"content_mode": "episode", "matrix_dedup": {"job": job}},
         "column_key": "flash-nr"},
        str(ROOT / "config"), str(ROOT / "jobs"), config)
    assert each_profile is workflow.JOB_PROMPTS[job]
    assert daily_profile is None
    for binding in (each, daily, manual):
        system = writer.author_system_prompt({"title": binding.key}, {})
        assert LISTENING_DIRECTION in system

def test_read_aloud_keeps_every_character_and_an_only_gap_does_not_erase_the_script():
    source = {"title": "原文", "text": "材料没有给具体倍率数字，我也不会编。"}
    column = type("C", (), {"target_minutes": 1, "voice_id": None})()
    written = article_draft(source, mode="read", kind="information", topic={"title": "原文"}, column=column, config=None)
    assert written.sections[0]["paragraphs"][0]["text"] == source["text"]
    only, record = __import__("core.video.narration_stance", fromlist=["settle_sections"]).settle_sections(
        [{"title": "只剩这句", "paragraphs": [{"text": source["text"]}]}])
    assert record["kept_original"] and only[0]["paragraphs"][0]["text"] == source["text"]

def _science_excerpt() -> dict:
    import yaml
    return yaml.safe_load((ROOT / "tests/fixtures/narration/science_chain_excerpt.yaml").read_text())

def test_science_chain_draft_revision_and_polish_keep_the_result(llm, monkeypatch):
    excerpt = _science_excerpt()
    style = excerpt["writing"]["style"]
    live_recipe = ROOT / "config/video_recipes/chain_ai_science_updates.yaml"
    live_job = ROOT / "jobs/chains/ai_science_updates.yaml"
    if live_recipe.is_file():
        import yaml
        live = yaml.safe_load(live_recipe.read_text())
        assert live["writing"]["pipeline"] == excerpt["writing"]["pipeline"]
        assert excerpt["writing"]["style"] in live["writing"]["style"]
    if live_job.is_file():
        assert excerpt["prompt_requirement"] in live_job.read_text(encoding="utf-8")
    draft = {
        "title": "蛋白质折叠误差下降",
        "sections": [{"title": "结果", "paragraphs": [{
            "text": "新模型把蛋白质折叠的误差从 8 降到 3。材料没有给对照组的样本量，所以我不会编。这个变化只在这一个公开测试集上成立，还没验证别的数据集。",
            "citations": [],
        }]}],
    }
    llm.replies = [json.dumps(draft, ensure_ascii=False)]
    written = writer.write(
        report="公开测试集上，折叠误差从 8 降到 3。", citation_check=None,
        topic={"title": "蛋白质折叠", "fields": {"目标受众": "关心这项实验的人"}},
        column_style=style, target_minutes=1.25, llm_config=object(),
    )
    assert LISTENING_DIRECTION in llm.calls[0][0]["content"]
    assert "沿用上游" in llm.calls[0][0]["content"]
    spoken = written.sections[0]["paragraphs"][0]["text"]
    assert "误差从 8 降到 3" in spoken and "还没验证" in spoken
    assert "材料没有给" not in spoken and "这次没有讲" not in spoken and "官方" not in spoken
    assert written.meta.get("narration_gaps")
    assert written.meta.get("narration_blocks_auto_approval") is True
    assert written.sections[0]["paragraphs"][0]["evidence"] == "NEEDS_REVIEW"

    def chat(client, prompt, request, schema, *, temperature):
        if prompt == workflow.FOCUSED_READ:
            return schema(takeaway="误差下降", keep_quote="误差从 8 降到 3",
                          revision="留下测试集范围", targets=["S1P1"])
        assert prompt == workflow._with_direction(workflow.FOCUSED_WRITE)
        assert prompt.count(LISTENING_DIRECTION) == 1
        return schema(paragraphs=[{"id": "S1P1", "text": "误差从 8 降到 3。材料没给对照组样本量，所以我不会编。这个变化只在公开测试集上成立。"}])

    monkeypatch.setattr(workflow, "call_json", chat)
    monkeypatch.setattr(workflow, "LLMClient", lambda *args, **kwargs: object())
    column = type("C", (), {"script_style": style, "target_minutes": 1.25, "voice_id": "zf_xiaoxiao"})()
    revised = workflow.revise_passage(
        written, report="公开测试集上，折叠误差从 8 降到 3。", topic={"title": "蛋白质折叠"},
        llm_config=object(), column=column,
    )
    # Revision already settles memo speech; polish may have nothing left to strip.
    assert revised.meta.get("narration_gaps") and revised.meta.get("narration_blocks_auto_approval") is True
    polished, record = polish.polish(llm(None), revised.sections)
    text = polished[0]["paragraphs"][0]["text"]
    assert "误差从 8 降到 3" in text and "公开测试集" in text
    assert "材料没给" not in text and "我不会编" not in text
    assert "这次没有讲" not in text and "官方" not in text
    assert (record.get("gaps") and record.get("blocks_auto_approval") is True) or (
        revised.sections[0]["paragraphs"][0].get("evidence") == "NEEDS_REVIEW"
        or any(p.get("evidence") == "NEEDS_REVIEW"
               for s in revised.sections for p in s.get("paragraphs") or [])
    )
    assert excerpt["writing"]["pipeline"] == "single"
    assert excerpt["prompt_requirement"] == "说明适用范围和尚待验证的部分"
    assert "chains/ai_science_updates" not in workflow.JOB_PROMPTS
    assert LISTENING_DIRECTION in writer.author_system_prompt({"title": "科研进展"}, {})


KEEP_LIMITS = [
    "这次只有 12 个样本，不能推广到所有患者。",
    "这篇是预印本，结果还没有独立复现。",
    "评测条件没有披露，不能据此比较两家的速度。",
    "厂商称快了 30%，我们没有独立验证。",
    "故障已经标记 Resolved，不代表你每一个失败的请求都已经恢复。",
]

LOCAL_KEEP_LIMITS = [
    "这次只有 12 个样本，不能推广到所有患者。",
    "这篇是预印本，结果还没有独立复现。",
    "评测条件没有披露，不能据此比较两家的速度。",
    "厂商称快了 30%，我们没有独立验证。",
    "故障已经标记 Resolved，不代表你每一个失败的请求都已经恢复。",
]

def test_audience_limits_stay_and_a_bare_comparison_is_not_cleared():
    for text in LOCAL_KEEP_LIMITS:
        assert repair(text) == (text, []), text
    bare, notes = repair("新模型把蛋白质折叠的误差从 8 降到 3。材料没有给对照组的样本量，所以我不会编。")
    assert bare == "新模型把蛋白质折叠的误差从 8 降到 3。"
    assert notes[0]["consequential"] and notes[0]["needs_attention"]
    assert "官方" not in bare and "这次没有讲" not in bare
    faster, faster_notes = repair("A 比 B 快一倍。材料没给评测条件，所以我不会编。")
    assert faster == "A 比 B 快一倍。"
    assert faster_notes[0]["consequential"]
    assert "官方" not in faster
    small = repair("这项研究只有 12 个样本，材料没给企业版价格。")[0]
    assert small == "这项研究只有 12 个样本。"
    lookup, lookup_notes = repair("Team 也含月度 API credits，不过材料没给具体额度。")
    assert lookup == "Team 也含月度 API credits。"
    assert not any(note.get("consequential") for note in lookup_notes)


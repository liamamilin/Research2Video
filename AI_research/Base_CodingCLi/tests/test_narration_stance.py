"""New drafts drop self-exoneration. Approved words and real conditions stay."""
import json
from pathlib import Path

import pytest

from core.research import _AGENT_SYSTEM_PROMPT, _FETCH_SYSTEM_PROMPT, _FINALIZE_PROMPT, _PIPELINE_SYSTEM_PROMPT
from core.video import audit, editorial, polish, production, writer
from core.video.adapt import draft, facts, plan, review
from core.video.narration_stance import PRIORITY, defects, launders, narrow, settle_sections, settle_version
from tests.test_video_scripts import REPORT, llm  # noqa: F401

ROOT = Path(__file__).parents[1]
NEGATIVES = [
    "Gemini、Mistral、Grok 的地区清单，这批材料里没有，所以我不能替它们下结论。你自己去官网查一下，比听我说更靠谱。",
    "Team 也含月度 API credits，不过材料没给具体额度。",
    "材料没有说豆包会代打电话、设预算上限、逐项确认付款或给通话转录。",
    "材料没有给 Code Mode 的跑分，所以我不会说它固定快多少。",
    "材料没有给具体倍率数字，我也不会编。",
    "还有两点材料没交代，别自己脑补。",
    "我没有乱说。",
]
POSITIVES = [
    "故障已经标记 Resolved，不代表你每一个失败的请求都已经恢复。",
    "这次实验不能推广到所有模型。",
    "写提示词时，把没核对的数字标成待核实。",
    "用户原话是「材料没有给具体倍率数字，我也不会编。」本期只比较已经给出的价格。",
    "教程分析这句不合格旁白：「还有两点材料没交代，别自己脑补。」",
    "这种材料没有毒性。",
    "材料没有毒性。",
    "材料缺少导电性。",
    "官方标称在实验室测试中续航最高72小时。",
    "下一次更新可能会改入口。",
    "Plus 是每月 20 美元，价格从 11 月 18 日起生效。",
    "这个套餐只在美国区可买。",
    "我还没找到 Anthropic 的官方公告。",
    "可能、尚未、不是、未验证这四个词单独出现，并不自动算病句。",
]


def _sections(*paragraphs):
    return [{"title": "比较", "paragraphs": [{"text": text, "citations": ["https://example.com/a"]} for text in paragraphs]},
            {"title": "还在讲的事", "paragraphs": [{"text": "DeepSeek 的免费额度先到顶。", "citations": []}]}]


def test_priority_reaches_every_authoring_and_review_prompt():
    prompts = [
        writer.SYSTEM_PROMPT, writer.EPISODE_PROMPT, writer.TUTORIAL_PROMPT, writer.AUTHORING_PROMPT,
        polish.SYSTEM_PROMPT, editorial.BRIEF_PROMPT, editorial.REWRITE_PROMPT, editorial.COPYEDIT_PROMPT,
        editorial.COMPARE_PROMPT, draft.SYSTEM_PROMPT, draft.REVISE_PROMPT, plan.SYSTEM_PROMPT,
        audit.PROMPT, audit.STYLE_PROMPT, audit.READER_PROMPT, audit.REVISE_PROMPT,
        review.NEWCOMER_PROMPT, review.FACT_AUDIT_PROMPT,
    ]
    for prompt in prompts:
        assert PRIORITY in prompt
    assert "research_notes" in facts.SYSTEM_PROMPT and "官方没公布" in facts.SYSTEM_PROMPT
    for prompt in (_AGENT_SYSTEM_PROMPT, _PIPELINE_SYSTEM_PROMPT, _FETCH_SYSTEM_PROMPT, _FINALIZE_PROMPT):
        assert "官方没公布" in prompt and "自我说明" in prompt


def test_supplied_negatives_are_narrowed_and_positives_stay():
    for text in NEGATIVES:
        after, notes = narrow(text)
        assert defects(text)
        assert notes
        assert "官方没公布" not in after and "官方没有公布" not in after
        assert "不会代打" not in after and "不支持" not in after
        assert "我不会编" not in after and "别自己脑补" not in after
    assert narrow("Team 也含月度 API credits，不过材料没给具体额度。")[0] == "Team 也含月度 API credits。"
    assert narrow("上限是 5 小时，不过材料没给具体倍率，我也不会编。")[0] == "上限是 5 小时。"
    for text in POSITIVES:
        assert not defects(text)
        assert narrow(text) == (text, [])


def test_missing_evidence_is_not_rewritten_into_absence():
    before = "材料没有说豆包会代打电话、设预算上限、逐项确认付款或给通话转录。"
    assert launders(before, "豆包不会代打电话，也不支持设预算上限。")
    assert launders("材料没给具体额度。", "官方没有公布具体额度。")
    assert not launders("材料没有说跑分。", "这次实验不能推广到所有模型。")
    settled, record = settle_sections(_sections(before))
    spoken = settled[0]["paragraphs"][0]["text"]
    assert spoken == "DeepSeek 的免费额度先到顶。" or "不会" not in "".join(
        p["text"] for s in settled for p in s["paragraphs"])
    assert "没有改写成官方未公布" in record["gaps"][0]["record"]
    assert "不会" not in spoken and "不支持" not in spoken


def test_a_section_that_is_only_a_gap_is_omitted_and_the_script_is_kept():
    settled, record = settle_sections(_sections(NEGATIVES[0]))
    assert all(NEGATIVES[0] not in p["text"] for s in settled for p in s["paragraphs"])
    assert any("DeepSeek" in p["text"] for s in settled for p in s["paragraphs"])
    assert record["gaps"]
    only, kept = settle_sections([{"title": "只剩备忘", "paragraphs": [{"text": NEGATIVES[0]}]}])
    assert kept["kept_original"] and only[0]["paragraphs"][0]["text"] == NEGATIVES[0]


def test_repeated_caveats_converge_and_a_second_pass_does_not_call_the_model(llm):
    sections = _sections(
        "材料没有给 Code Mode 的跑分，所以我不会说它固定快多少。材料没有给具体倍率数字，我也不会编。",
        "还有两点材料没交代，别自己脑补。还有两点材料没交代，别自己脑补。",
    )
    llm.polish_replies = [json.dumps({"paragraphs": [
        {"id": "S1P1", "text": "材料没有给 Code Mode 的跑分，所以我不会说它固定快多少。"},
    ]}, ensure_ascii=False)]
    out, record = polish.polish(llm(None), sections)
    spoken = "\n".join(p["text"] for s in out for p in s["paragraphs"])
    assert "我不会说" not in spoken and "我也不会编" not in spoken and "别自己脑补" not in spoken
    assert record["rejected"] and record["gaps"]
    again, second = polish.polish(llm(None), out)
    assert again == out and second["findings"] == [] and len(llm.polish_calls) == 1


def test_model_laundering_is_rejected_and_a_failed_call_still_narrows(llm):
    sections = _sections("Team 也含月度 API credits，不过材料没给具体额度。")
    llm.polish_replies = [json.dumps({"paragraphs": [
        {"id": "S1P1", "text": "Team 也含月度 API credits，官方没有公布具体额度。"},
    ]}, ensure_ascii=False)]
    out, record = polish.polish(llm(None), sections)
    assert out[0]["paragraphs"][0]["text"] == "Team 也含月度 API credits。"
    assert "官方" not in out[0]["paragraphs"][0]["text"]
    assert record["rejected"][0]["reason"] == "把材料缺口说成官方没有公布"
    failed = _sections("材料没有给具体倍率数字，我也不会编。")
    llm.polish_replies = ["不是 JSON"] * 5
    out, record = polish.polish(llm(None), failed)
    assert "error" in record
    assert all("我也不会编" not in p["text"] for s in out for p in s["paragraphs"])
    assert any("DeepSeek" in p["text"] for s in out for p in s["paragraphs"])


def test_a_clean_positive_draft_makes_no_polish_call(llm):
    sections = _sections(*POSITIVES[:4])
    out, record = polish.polish(llm(None), sections)
    assert out == sections and record["findings"] == [] and llm.polish_calls == []


def test_a_missing_model_still_narrows_a_new_draft(monkeypatch):
    from core.llm import LLMClient
    from core.video.writer import WrittenScript

    def boom(self, *args, **kwargs):
        raise RuntimeError("ai.model is not configured")

    monkeypatch.setattr(LLMClient, "__init__", boom)
    written = WrittenScript("比较", _sections(NEGATIVES[4]), {"target_minutes": 1}, {})
    polish.apply(written, None)
    spoken = "\n".join(p["text"] for s in written.sections for p in s["paragraphs"])
    assert "我也不会编" not in spoken and "DeepSeek" in spoken
    assert written.meta["narration_gaps"]


def test_approved_and_verbatim_manuscripts_are_not_rewritten():
    version = {"sections": _sections(NEGATIVES[1]), "meta": {"generation": "article_verbatim"}}
    out, record = settle_version(version, script_status="APPROVED")
    assert record["locked"] and out[0]["paragraphs"][0]["text"] == NEGATIVES[1]
    verbatim, locked = settle_version({"sections": version["sections"], "meta": {"generation": "article_verbatim"}})
    assert locked["locked"] and verbatim[0]["paragraphs"][0]["text"] == NEGATIVES[1]


def test_tutorial_episode_and_single_prompts_share_the_priority(llm):
    llm.replies = [
        json.dumps({"title": "教程", "sections": [{"title": "准备", "episode_role": "opening", "paragraphs": [{"text": "先准备输入。", "citations": []}]}]}),
        json.dumps({"title": "单条", "sections": [{"title": "正文", "paragraphs": [{"text": "先看额度。", "citations": []}]}]}),
    ]
    writer.write(report=REPORT, citation_check=None, topic={"title": "教程", "content_mode": "episode"},
                 column_style="", target_minutes=1, llm_config=None, tutorial=True)
    writer.write(report=REPORT, citation_check=None, topic={"title": "单条"}, column_style="", target_minutes=1, llm_config=None)
    assert PRIORITY in llm.calls[0][0]["content"] and "工具教程" in llm.calls[0][0]["content"]
    assert PRIORITY in llm.calls[1][0]["content"]


def test_every_usable_production_binding_shares_the_new_draft_boundary():
    bindings = production.load_all(str(ROOT / "config"), str(ROOT / "jobs"))
    assert bindings
    seen = set()
    for key, binding in bindings.items():
        if not binding.usable:
            continue
        seen.add(binding.recipe)
        assert binding.recipe
    # Archived one-off jobs are snapshots. They are not a live writer.
    archived = list((ROOT / "jobs" / "archive").rglob("*.yaml"))
    assert archived
    active_prompts = []
    for path in (ROOT / "jobs").rglob("*.yaml"):
        if "archive" in path.parts:
            continue
        active_prompts.append(path.read_text(encoding="utf-8"))
    for prompt in active_prompts:
        assert "告诉观众你没有乱说" not in prompt
        assert "口播要说材料没有" not in prompt
    for path in (ROOT / "config" / "content_columns").glob("*.yaml"):
        notes = path.read_text(encoding="utf-8")
        assert "口播要说材料没有" not in notes
        assert "告诉观众你没有乱说" not in notes
    assert seen


def test_autovideo_generated_narration_uses_the_same_priority_and_authored_text_is_unchanged():
    path = ROOT.parent.parent / "autovideo" / "g2" / "narration_stance.py"
    if not path.is_file():
        pytest.skip("AutoVideo is not beside Research")
    import importlib.util
    spec = importlib.util.spec_from_file_location("av_narration_stance", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.PRIORITY == PRIORITY
    for text in NEGATIVES + POSITIVES:
        assert [item["quote"] for item in module.defects(text)] == [item["quote"] for item in defects(text)]
    assert module.generated_problem(NEGATIVES[3])
    assert not module.generated_problem(POSITIVES[0])

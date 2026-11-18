"""New drafts keep usable facts and say when a comparison does not include something."""
import json
from pathlib import Path

import pytest

from core.video import manuscript_workflow as workflow
from core.video import polish, production, writer
from core.video.article_writer import draft as article_draft
from core.video.narration_stance import LISTENING_DIRECTION, launders, repair
from tests.test_video_scripts import llm  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
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


def test_examples_keep_facts_and_do_not_invent_absence():
    spoken = {text: repair(text)[0] for text in NEGATIVES}
    assert spoken[NEGATIVES[0]] == "这次比较没有包括Gemini、Mistral、Grok的地区。"
    assert spoken[NEGATIVES[1]] == "Team 也含月度 API credits。"
    assert spoken[NEGATIVES[2]] == "这次没有讲豆包会不会代打电话、设预算上限、逐项确认付款或给通话转录。"
    assert spoken[NEGATIVES[3]] == "这次比较没有包括Code Mode的跑分。"
    assert spoken[NEGATIVES[4]] == ""
    assert "官方" not in "".join(spoken.values())
    assert "豆包不会" not in spoken[NEGATIVES[2]] and "不支持" not in spoken[NEGATIVES[2]]
    mixed = repair("材料没给具体额度，Team 也含月度 API credits。")[0]
    assert mixed == "Team 也含月度 API credits。"
    withdrawn = repair("Gemini 也能在这些地区用，不过材料没给具体清单，所以我不能下结论。")[0]
    assert "也能在这些地区用" not in withdrawn
    comparison = repair("ChatGPT 和 Claude 能在中国区用。Gemini、Mistral、Grok 的地区清单，这批材料里没有，所以我不能替它们下结论。")[0]
    assert comparison.startswith("ChatGPT 和 Claude 能在中国区用。")
    assert "这次比较没有包括Gemini、Mistral、Grok的地区。" in comparison
    kept_fact = repair("上限是 5 小时，不过材料没给具体倍率，我也不会编。")[0]
    assert kept_fact == "上限是 5 小时。"
    for text in KEEP:
        assert repair(text) == (text, [])
    assert launders(NEGATIVES[2], "豆包不会代打电话。")
    assert launders("材料没给具体额度。", "官方没有公布具体额度。")


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
    assert "我不能替" not in spoken and "DeepSeek" in spoken and "这次比较没有包括" in spoken
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
    manual = production.get(str(ROOT / "config"), str(ROOT / "jobs"), "deep_dive-2")
    assert each.usable and daily.usable and each.reverify is False and daily.reverify is False
    assert each.recipe == daily.recipe == "flash-nr"
    assert each.split == "each" and daily.split == "whole"
    assert manual.auto == 0 and manual.usable
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


def test_live_science_chain_uses_the_single_writer_when_the_overlay_is_present():
    recipe = Path("/tmp/science-overlay/AI_research/Base_CodingCLi/config/video_recipes/chain_ai_science_updates.yaml")
    job = Path("/tmp/science-overlay/AI_research/Base_CodingCLi/jobs/chains/ai_science_updates.yaml")
    if not recipe.is_file():
        pytest.skip("science-chain overlay is not on this machine")
    import yaml
    loaded = yaml.safe_load(recipe.read_text())
    assert loaded["writing"]["pipeline"] == "single"
    prompt = yaml.safe_load(job.read_text())["prompt"]
    assert "适用范围" in prompt and "尚待验证" in prompt
    assert "chains/ai_science_updates" not in workflow.JOB_PROMPTS
    assert LISTENING_DIRECTION in writer.author_system_prompt({"title": "科研进展"}, {})

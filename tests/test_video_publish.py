from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.video import columns as video_columns
from core.video import publish

ROOT = Path(__file__).resolve().parents[1]
REPORT = """### 6. 来源清单
1. Gemini 4 定价页 | 一手 | 2026-10-13 | https://ai.google.dev/pricing
2. 论坛帖子 | 二手 | 2026-10-16 | https://discuss.ai.google.dev/t/1
3. 没有日期的博客 | 二手 | | https://blog.example.com/a
4. 没人引用的来源 | 二手 | 2026-10-01 | https://example.com/unused
"""


def column():
    return video_columns.get(str(ROOT / "config"), "flash")


def timeline(starts, titles=None, mode="packaged"):
    titles = titles or [f"第{i + 1}章" for i in range(len(starts))]
    return {"timeline": mode, "chapters": [{"id": f"c{i}", "title": t, "start_ms": int(s * 1000)}
                                           for i, (s, t) in enumerate(zip(starts, titles))]}


def version(**meta):
    return {
        "version": 2,
        "title": "Gemini 4 Argon：限时价 12 月翻倍",
        "sections": [{"title": "开场", "paragraphs": [
            {"text": "Google 开放了 Gemini 4 Argon，输入价 2 美元。但这只是限时价。",
             "citations": ["https://ai.google.dev/pricing", "https://blog.example.com/a"]},
            {"text": "论坛有人实测缓存命中为零。", "citations": ["https://discuss.ai.google.dev/t/1"]}]}],
        "meta": meta,
    }


SCRIPT = {"id": "s1", "status": "APPROVED", "approved_version": 2, "topic": {"title": "Argon 涨价"}}


def test_chapters_start_at_zero_and_use_minutes_and_seconds_with_titles_free_of_special_punctuation():
    lines = publish.chapter_lines(timeline([1.5, 29.333, 49.933], ["限时价，12 月翻倍", "缓存折扣！可能拿不到", "现在该做什么"]))
    assert [(c["time"], c["title"]) for c in lines] == [
        ("00:00", "限时价 12 月翻倍"), ("00:29", "缓存折扣 可能拿不到"), ("00:49", "现在该做什么")]
    assert publish.clock(3725) == "1:02:05"


def test_chapters_need_three_that_are_five_seconds_apart_and_a_packaged_timeline():
    assert publish.chapter_lines(timeline([0, 30])) == []
    assert publish.chapter_lines(timeline([0, 3, 30, 60]))[1]["seconds"] == 30  # 3 s after the first is dropped
    assert publish.chapter_lines(timeline([0, 3, 4, 60])) == []
    assert publish.chapter_lines(timeline([0, 30, 60], mode="content")) == []
    assert publish.chapter_lines(None) == []


def test_the_real_flash_renders_chapters_make_a_pack():
    path = ROOT / "state" / "video_renders" / "d922c751debe4db5a328174a3c6a8131" / "chapters.json"
    if not path.is_file():
        pytest.skip("the first flash render is not on this machine")
    lines = publish.chapter_lines(json.loads(path.read_text(encoding="utf-8")))
    assert [c["time"] for c in lines] == ["00:00", "00:29", "00:49"]


def test_the_pack_has_title_description_references_tags_and_cover_text():
    pack = publish.build(SCRIPT, version(), column(), REPORT, timeline([1.5, 29, 50]))
    assert pack["title"] == "Gemini 4 Argon：限时价 12 月翻倍"
    lines = pack["description"].split("\n")
    assert lines[0] == "Google 开放了 Gemini 4 Argon，输入价 2 美元。"
    assert "00:00 第1章" in lines and "00:29 第2章" in lines
    assert "· Gemini 4 定价页（2026-10-13） https://ai.google.dev/pricing" in lines
    assert "· 没有日期的博客 https://blog.example.com/a" in lines
    assert "unused" not in pack["description"]  # only what the script cites
    assert pack["tags"][:3] == ["快讯", "开发者", "AI"] and "Gemini" in pack["tags"] and len(pack["tags"]) <= 10
    assert pack["cover"] == {"headline": "Gemini 4 Argon", "badge": "快讯"}
    assert pack["chapter_note"] and any("没有发布日期" in w for w in pack["warnings"])
    assert not any("定稿" in w for w in pack["warnings"])


def test_an_adapted_versions_summary_and_details_lead_the_description():
    meta = {"description": {"summary": "限时价到 11 月 30 日，之后翻倍。", "details": ["输入 2 → 4 美元"], "open_questions": ["缓存何时修复"]}}
    pack = publish.build(SCRIPT, version(**meta), column(), REPORT, None)
    assert pack["description"].startswith("限时价到 11 月 30 日，之后翻倍。\n\n数字和口径：\n· 输入 2 → 4 美元\n\n还没确定：\n· 缓存何时修复")
    assert pack["chapters"] == [] and "还没有出片，所以没有章节时间。" in pack["warnings"]


def test_warnings_say_what_a_person_must_check_before_uploading():
    long = {**version(), "title": "长" * 81, "checks": {"summary": {"serious": 2}}}
    pack = publish.build({**SCRIPT, "status": "DRAFT"}, long, column(), REPORT, timeline([0, 30]))
    warnings = " ".join(pack["warnings"])
    for expected in ("还没有定稿", "超过 80", "2 个严重的事实问题", "不足 3 个章节"):
        assert expected in warnings


def test_a_description_over_the_limit_drops_the_extras_before_the_references():
    meta = {"description": {"summary": "摘要。", "details": ["数" * 100] * 30}}
    pack = publish.build(SCRIPT, version(**meta), column(), REPORT, None)
    assert len(pack["description"]) <= publish.DESCRIPTION_MAX and "参考来源" in pack["description"]
    assert "数字和口径" not in pack["description"]


def test_tags_skip_prices_and_a_long_title_is_cut_at_its_first_clause_for_the_cover():
    title = "Gemini 4 Argon 上线：限时输入 $2 输出 $10"
    assert not any(t.startswith("$") or t[0].isdigit() for t in publish.tags(title, column()))
    assert publish.cover_text("系统提示里一行时间戳，让缓存白开了", column())["headline"] == "系统提示里一行时间戳"
    assert publish.cover_text("短标题", column())["headline"] == "短标题"


def test_a_summary_cut_for_length_ends_at_a_comma_not_in_the_middle_of_a_word():
    long = "这周讲几个大模型怎么挑" + "，Claude Opus 5.5 智能指数最高" * 6 + "，最快开始返回第一段，最便宜的是另一个。后面还有。"
    out = publish.summary({"sections": [{"paragraphs": [{"text": long}]}]})
    assert len(out) <= publish.SUMMARY_MAX + 1 and out.endswith("。") and "，。" not in out
    assert out == long[:out.index("。")] + "。" and long.startswith(out[:-1])
    no_comma = publish.summary({"sections": [{"paragraphs": [{"text": "超" * 150 + "。"}]}]})
    assert no_comma == "超" * publish.SUMMARY_MAX + "…"
    short = publish.summary({"sections": [{"paragraphs": [{"text": "很短的一句。第二句。"}]}]})
    assert short == "很短的一句。"

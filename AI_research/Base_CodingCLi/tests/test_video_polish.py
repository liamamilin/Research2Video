import json

from core.video import polish, writer
from tests.test_video_scripts import REPORT, llm, video_env  # noqa: F401

GREETING = "大家好，欢迎收看今日快讯。"


def _p(text):
    return {"text": text, "citations": ["https://example.com/a"], "evidence": "CITED", "note": None}


def _sections(*items):
    return [{"title": writer.OPENING_TITLE, "episode_role": "opening", "paragraphs": [_p(GREETING), _p("今天讲两件事。")]},
            *({"title": f"第{i}条", "episode_role": "item", "paragraphs": [_p(text) for text in texts]}
              for i, texts in enumerate(items, 1)),
            {"title": writer.CLOSING_TITLE, "episode_role": "closing", "paragraphs": [_p("我们下期见。")]}]


def _reply(**texts):
    return json.dumps({"paragraphs": [{"id": key, "text": text} for key, text in texts.items()]}, ensure_ascii=False)


def test_final_polish_keeps_connected_opening_and_applies_body_edit(llm, monkeypatch):
    monkeypatch.setattr(polish.persona, "load", lambda: {"name": "洛洛", "opening_greeting": "大家好"})
    opening = "这份报告能直接交吗？大家好，我是洛洛，今天就来看看怎么验。材料里写过一种做法。"
    sections = _sections(["材料里写过一种做法。"])
    sections[0]["paragraphs"] = [_p(opening)]
    llm.polish_replies = [_reply(S1P1="这份报告能直接交吗？先看看怎么验。", S2P1="先从报告里的引用查起。")]
    result, record = polish.polish(llm(None), sections)
    assert result[0]["paragraphs"][0]["text"] == opening
    assert result[1]["paragraphs"][0]["text"] == "先从报告里的引用查起。"
    assert [item["id"] for item in record["rejected"]] == ["S1P1"]


def test_the_program_finds_production_notes_and_stock_endings():
    sections = _sections(["素材给的信息是 MIT，254 stars。", "视频里应该说清楚：效果要自己验。"],
                         ["这条的要点是：别急着改配置。"])
    found = polish.findings(sections, greeting=GREETING)
    assert [(f["id"], f["rule"]) for f in found] == [
        ("S2P1", "production_note"), ("S2P2", "production_note"), ("S3P1", "stock_ending")]
    # Talking about videos and footage is not a production note, and the greeting is never flagged.
    assert polish.findings(_sections(["这个模型能生成一段会说话的视频，口播也更自然。", "生成的视频应该更稳定，画面要展示口型。",
                                      "素材没有版权就别用，素材显示在时间线上。"]), greeting=GREETING) == []


def test_a_clean_draft_makes_no_model_call(llm):
    sections = _sections(["先看额度：每周上限变成 5 小时。"])
    out, record = polish.polish(llm(None), sections, greeting=GREETING)
    assert out is sections and record["findings"] == [] and llm.polish_calls == []


def test_repeated_comparisons_alone_leave_the_authors_scope_intact(llm):
    sections = _sections(["变化不是功能，是计费方式。", "这不是模型更新，是安装通道调整。",
                         "这次不是 API 定价变化，是零售促销。"])
    out, record = polish.polish(llm(None), sections, greeting=GREETING)
    assert out is sections
    assert record["findings"] == [] and llm.polish_calls == []


def test_only_flagged_paragraphs_change_and_keep_their_citations_and_place(llm):
    sections = _sections(["先看额度。", "这条记住的就一件事：上限按周算，5 小时窗口还在。"])
    llm.polish_replies = [_reply(S2P2="说到底，上限从此按周来算，而那个 5 小时窗口还在。", S2P1="不该改的段落")]
    out, record = polish.polish(llm(None), sections, greeting=GREETING)
    assert out[1]["paragraphs"][1]["text"] == "说到底，上限从此按周来算，而那个 5 小时窗口还在。"
    assert out[1]["paragraphs"][1]["citations"] == ["https://example.com/a"]
    assert out[1]["paragraphs"][0]["text"] == "先看额度。"
    assert [len(s["paragraphs"]) for s in out] == [len(s["paragraphs"]) for s in sections]
    assert record["changes"] == [{"id": "S2P2", "where": "第1条 · 第 2 段", "rules": ["stock_ending"],
                                  "before": "这条记住的就一件事：上限按周算，5 小时窗口还在。",
                                  "after": "说到底，上限从此按周来算，而那个 5 小时窗口还在。"}]
    assert record["remaining"] == []
    request = llm.polish_calls[0][1]["content"]
    assert "[S2P2]" in request and "固定句式" in request


def test_a_rewrite_that_changes_a_number_is_rejected(llm):
    sections = _sections(["这条的要点是：上限从 5 小时变成每周 40 小时。"])
    llm.polish_replies = [_reply(S2P1="上限从 5 小时变成每周 50 小时。")]
    out, record = polish.polish(llm(None), sections, greeting=GREETING)
    assert out[1]["paragraphs"][0]["text"] == "这条的要点是：上限从 5 小时变成每周 40 小时。"
    assert record["changes"] == [] and record["rejected"][0]["reason"] == "改写改动了数字"


def test_a_failed_call_keeps_the_draft(llm):
    sections = _sections(["视频里应该说清楚：效果要自己验。"])
    llm.polish_replies = ["不是 JSON"] * 5
    out, record = polish.polish(llm(None), sections, greeting=GREETING)
    assert out[1]["paragraphs"][0]["text"] == "视频里应该说清楚：效果要自己验。"
    assert record["changes"] == [] and "error" in record


def test_a_fresh_programme_draft_is_polished_and_the_record_is_saved(llm):
    paragraph = lambda text: {"text": text, "citations": []}
    llm.replies = [json.dumps({"title": "本期", "sections": [
        {"title": "开场", "episode_role": "opening", "paragraphs": [paragraph("今天讲一件事。")]},
        {"title": "额度上调", "episode_role": "item", "paragraphs": [paragraph("这条的要点是：额度按周算。")]},
        {"title": "收尾", "episode_role": "closing", "paragraphs": [paragraph("我们下期见。")]}]}, ensure_ascii=False)]
    llm.polish_replies = [_reply(S2P1="以后额度按周来算，安排任务时心里要有这本账。")]
    written = writer.write(report=REPORT, citation_check=None, topic={"title": "本期", "content_mode": "episode"},
                           column_style="", target_minutes=1, llm_config=None, greeting=GREETING)
    polish.apply(written, None, greeting=GREETING)
    assert written.sections[1]["paragraphs"][0]["text"] == "以后额度按周来算，安排任务时心里要有这本账。"
    assert written.sections[0]["paragraphs"][0]["text"] == GREETING
    assert written.meta["polish"]["changes"][0]["rules"] == ["stock_ending"]
    assert written.meta["polish"]["usage"]["requests"] == 1
    assert written.length_check["duration_policy"] == "content"
    assert written.length_check["sections"][1]["spoken_length"] > len("这条的要点额度按周算")


def test_a_closing_rewrite_keeps_its_goodbye(llm):
    sections = _sections(["先看额度。"])
    sections[-1]["paragraphs"] = [_p("口播里要说清楚验收。好，今天就到这里，感谢收看，我们下期见。")]
    llm.polish_replies = [_reply(S3P1="真正值得记住的是验收。")]
    out, record = polish.polish(llm(None), sections, greeting=GREETING)
    assert out[2]["paragraphs"][0]["text"] == "真正值得记住的是验收。好，今天就到这里，感谢收看，我们下期见。"


def test_research_material_mentioned_to_the_viewer_is_a_production_note():
    found = polish.findings(_sections(["素材里实测齿音从 13.2 dB 降到 5.9 dB。", "材料里也明确说周限额没有变。"]))
    assert [f["rule"] for f in found] == ["production_note", "production_note"]


def test_a_reworded_goodbye_is_not_added_twice(llm):
    sections = _sections(["先看额度。"])
    sections[-1]["paragraphs"] = [_p("口播里要说清楚验收。感谢收看，我们下期见。")]
    llm.polish_replies = [_reply(S3P1="值得记住的是验收。感谢收看，下期见。")]
    out, _ = polish.polish(llm(None), sections, greeting=GREETING)
    assert out[2]["paragraphs"][0]["text"] == "值得记住的是验收。感谢收看，下期见。"


def test_a_bug_in_polishing_keeps_the_draft(llm, monkeypatch):
    written = writer.WrittenScript(title="本期", sections=_sections(["这条的要点是：按周算。"]), length_check={})
    monkeypatch.setattr(polish, "polish", lambda *a, **k: 1 / 0)
    polish.apply(written, None, greeting=GREETING)
    assert written.sections[1]["paragraphs"][0]["text"] == "这条的要点是：按周算。"
    assert written.meta["polish"]["error"].startswith("ZeroDivisionError")


def test_stock_memorable_lines_made_up_bridges_and_disclaimer_hedges_are_found():
    sections = _sections(["如果说上一条讲的是入口，那么这一条讲的是能力。", "最值得记住的是：先看你卡在哪。"],
                         ["接下来这条处理的是订阅。", "具体额度以你实际界面为准。"])
    found = polish.findings(sections, greeting=GREETING)
    assert [(f["id"], f["rule"]) for f in found] == [
        ("S2P1", "stock_bridge"), ("S2P2", "stock_ending"), ("S3P1", "stock_bridge"), ("S3P2", "hedge")]
    # a real relation said plainly, and a closing that names the one thing to take away, are left alone
    assert polish.findings(_sections(["和上一条一样，这也是价格变化。"]), greeting=GREETING) == []
    closing = _sections()
    closing[-1]["paragraphs"].insert(0, _p("今天最值得记住的是：额度是钱，不是配额。"))
    assert polish.findings(closing, greeting=GREETING) == []

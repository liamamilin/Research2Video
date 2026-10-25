"""Story inputs and listening comparisons must not inherit the report's outline."""

import json
from types import SimpleNamespace

import pytest

from core.llm import ChatResponse
from core.video import editorial
from core.video.adapt.common import AdaptError
from tests.test_video_scripts import GOOD, _write, llm, script_json  # noqa: F401


SHEET = {
    "question": "内部选题是否写错",
    "answer": "先纠正报告中的三处问题，再介绍完整历史",
    "facts": [
        {"id": "F1", "claim": "项目创建于某年", "sources": [GOOD], "confidence": "厂商自述"},
        {"id": "F2", "claim": "工具新增离线保存", "sources": [GOOD], "confidence": "厂商自述",
         "caveat": "离线保存需打开本地存储"},
    ], "concepts": [],
}


class Queue:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return ChatResponse(content=json.dumps(self.replies.pop(0), ensure_ascii=False))


def selection(**changes):
    return {"angle": "离线时能否保住改动", "viewer_value": "减少返工", "throughline": "改动保存后如何继续编辑",
            "facts": ["F2"], "opening": "断网以后，刚改的东西还在吗？",
            "beats": [{"point": "改动能保存", "facts": ["F2"], "connection": "先解决会不会丢"}],
            "material": "不应该夹带的完整历史", **changes}


def test_fact_packet_excludes_research_corrections_and_unselected_history():
    client = Queue(selection())
    direction = editorial.brief(client, editorial.source_material(SHEET), sheet=SHEET)
    assert "先纠正报告" not in client.calls[0][1]["content"]
    assert "项目创建于某年" not in direction["material"]
    assert "不应该夹带" not in direction["material"]
    assert "工具新增离线保存" in direction["material"]
    assert "离线保存需打开本地存储" in direction["material"]
    assert GOOD in direction["material"]


def test_unknown_fact_selection_is_retried_without_falling_back_to_the_report():
    client = Queue(selection(facts=["F999"], beats=[]), selection())
    result = editorial.brief(client, editorial.source_material(SHEET), sheet=SHEET)
    assert len(client.calls) == 2 and result["facts"] == ["F2"]
    assert "有效的 facts 编号" in client.calls[1][1]["content"]
    with pytest.raises(AdaptError, match="有效编号"):
        editorial.packet({"facts": []}, SHEET)


def test_beat_sources_are_validated_as_part_of_the_selected_packet():
    with pytest.raises(AdaptError):
        editorial.brief(Queue(selection(beats=[{"point": "虚构变化", "facts": ["F999"]}]),
                             selection(beats=[{"point": "虚构变化", "facts": ["F999"]}])),
                        editorial.source_material(SHEET), sheet=SHEET)


def test_selected_packet_follows_the_narrative_rather_than_the_reports_fact_order():
    client = Queue(selection(facts=["F1", "F2"], beats=[
        {"point": "先讲具体变化", "facts": ["F2"], "connection": "为什么值得听"},
        {"point": "必要时带入背景", "facts": ["F1"], "connection": "背景解释这个变化"},
    ]))
    direction = editorial.brief(client, editorial.source_material(SHEET), sheet=SHEET)
    assert [f["id"] for f in json.loads(direction["material"])["facts"]] == ["F2", "F1"]


def test_opening_sources_precede_background_beats_in_the_writer_packet():
    client = Queue(selection(opening_facts=["F2"], facts=["F1"],
                             beats=[{"point": "需要时解释历史", "facts": ["F1"]}]))
    direction = editorial.brief(client, editorial.source_material(SHEET), sheet=SHEET)
    assert [f["id"] for f in json.loads(direction["material"])["facts"]] == ["F2", "F1"]
    with pytest.raises(AdaptError):
        editorial.brief(Queue(selection(opening_facts=["F404"]), selection(opening_facts=["F404"])),
                        editorial.source_material(SHEET), sheet=SHEET)


def test_copyedit_receives_existing_words_but_never_internal_review_prose():
    client = Queue(selection(edit_mode="copyedit", edit_reason="主线成立，只需要接好两句话"),
                   {"title": "离线改动能保住", "sections": [{"title": "保存", "paragraphs": [
                       {"text": "断网后，改动先留在本机。", "citations": [GOOD], "facts": ["F2"]}]}]})
    original = [{"title": "保存", "paragraphs": [{"text": "断网。改动仍保存在本机。", "citations": [GOOD],
                "facts": ["F2"], "evidence": "CITED", "note": "内部审核意见，不应进入正文"}]}]
    proposal = editorial.rewrite(client, title="离线保存", sections=original,
                                 material=editorial.source_material(SHEET), direction=selection(),
                                 feedback={"S1P1": ["衔接不顺"]}, column_style="自然说明工具变化",
                                 target_minutes=1, sheet=SHEET)
    assert client.calls[1][0]["content"] == editorial.COPYEDIT_PROMPT
    assert "断网。改动仍保存在本机。" in client.calls[1][-1]["content"]
    assert all("内部审核意见" not in call[-1]["content"] and "CITED" not in call[-1]["content"] for call in client.calls)
    assert proposal["editorial_brief"]["edit_mode"] == "copyedit"


def test_opening_and_transitions_reach_the_writer_but_unused_options_do_not(llm):
    llm.replies = [script_json(300)]
    _write(source_sheet=SHEET)
    assert len(llm.calls) == 1 and not llm.brief_calls and not llm.fact_calls
    assert "主编选材" not in llm.calls[0][-1]["content"]


def test_an_old_topic_angle_does_not_compete_with_the_selected_story(llm):
    llm.replies = [script_json(300)]
    _write(source_sheet=SHEET, topic={"title": "工具更新", "fields": {"核心角度": "介绍完整版本履历", "对比对象": "手机；电脑", "对比维度": "改动保存"}})
    request = llm.calls[0][-1]["content"]
    assert "介绍完整版本履历" not in request and "工具更新" in request
    assert "手机；电脑" in request and "改动保存" in request
    assert not llm.brief_calls


def test_listening_comparison_ignores_headings_and_review_metadata():
    client = Queue({"choice": "A", "reason": "A 直接进入关心的问题，B 开场念履历", "listening": {"A": "想继续听", "B": "走神"}})
    sections = [{"title": "资料表的正式栏目", "paragraphs": [{"text": "断网以后，改动还在吗？",
                                                    "citations": [GOOD], "evidence": "CITED"}]}]
    editorial.compare(client, before_title="离线编辑", before=sections,
                       after_title="更新介绍", after=sections, column_style="给普通观众解释工具变化")
    request = client.calls[0][1]["content"]
    assert "资料表的正式栏目" not in request and "CITED" not in request
    assert "断网以后，改动还在吗" in request


def test_both_workflow_listeners_must_follow_transitions_without_section_labels():
    from core.video import audit
    from core.video.adapt import review

    sections = [{"title": "因为合并才能接着协作", "paragraphs": [{"text": "它还支持合并。"}]},
                {"title": "回到断网保存", "paragraphs": [{"text": "刚才的改动还在。"}]}]
    client = Queue({"paragraphs": [{"id": "S2P1", "lost": ["为什么又回到保存？"]}]},
                   {"paragraphs": [{"id": "S2P1", "lost": ["为什么又回到保存？"]}]})
    findings = audit.reader_issues(client, sections, "普通观众")
    style = SimpleNamespace(label="自然解释", tone=SimpleNamespace(guidance="按内容说清楚"))
    reading = review.newcomer(client, sections, {}, style)
    for call in client.calls:
        request = call[-1]["content"]
        assert "因为合并才能接着协作" not in request and "回到断网保存" not in request
        assert "[S1P1] 它还支持合并。" in request and "[S2P1] 刚才的改动还在。" in request
    assert findings[0]["section"] == 1 and findings[0]["paragraph"] == 0
    assert reading["issues"][0]["section"] == 1 and reading["issues"][0]["paragraph"] == 0

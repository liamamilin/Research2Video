"""Quality survives editorial budget overruns; factual failures still require review."""

import copy

import pytest

from core.video import writer
from core.errors import CancelledError
from core.video.adapt import draft, facts, review
from core.video.adapt import plan as planning
from tests.test_video_adapt import (BATCH, CLEAN, GOOD_READ, REPORT, adapt, column, draft_json, finding,
                                    llm, plan_json, sheet_of, style)  # noqa: F401


def test_both_writers_receive_content_without_planning_quotas(llm):
    import json

    col = column(sections=(17, 19), paragraph_units=(1357, 2468), required_roles=("固定结构角色",))
    sheet = sheet_of()
    plan = plan_json()
    for beat in plan["beats"]:
        beat.update(budget=876543, role="固定结构角色")
    planning_request = planning._request(sheet, col, {}, style(), 6)
    draft.write(llm(None), plan, sheet, style(), col)
    legacy_request = writer._request(REPORT, {}, col.script_style, 6, 876543, (1357, 2468))
    for request in (planning_request, llm.request("draft"), legacy_request):
        assert not any(quota in request for quota in ("876543", "1357", "2468", "固定结构角色", "17–19"))
    assert "缓存写入是输入价的 1.25 倍" in llm.request("draft")
    assert "主编思路" not in llm.request("draft")


def test_research_notes_are_preserved_without_becoming_author_material():
    from core.video import editorial

    note = "本次摘要未收录许可证，之后可以打开仓库继续研究。"
    raw = {"facts": [{"id": "F1", "claim": "断网时改动保存在本机", "confidence": "厂商自述",
                      "sources": [BATCH], "caveat": "需启用本地存储"}], "research_notes": [note]}
    sheet, _ = facts.normalize(raw, {BATCH})
    assert sheet["research_notes"] == [note]
    for material in (editorial.source_material(sheet), editorial.packet({"facts": ["F1"]}, sheet),
                     planning._request(sheet, column(), {}, style(), 6)):
        assert note not in material
        assert "断网时改动保存在本机" in material and "需启用本地存储" in material


def test_long_dense_draft_and_low_expression_scores_do_not_trigger_rewrites(llm):
    script = draft_json()
    script["sections"][0]["paragraphs"][0]["text"] = "讲" * 160 + "价格分别为 4 美元、2 美元、1 美元。"
    script["sections"][0]["paragraphs"][0]["facts"] = ["F1", "F2"]
    llm.queues["draft"] = [script]
    reading = copy.deepcopy(llm.queues["newcomer"][0])
    reading["scores"] = {"hook": 1, "clarity": 2, "pacing": 1, "style": 1}
    reading["paragraphs"] = [{"id": "S1P1", "questions": ["表达可以更清楚"], "lost": []}]
    llm.queues["newcomer"] = [reading]
    adapted, _ = adapt(column=column(target_minutes=1))
    assert "revise" not in llm.steps()
    assert adapted.sections[0]["paragraphs"][0]["text"] == script["sections"][0]["paragraphs"][0]["text"]
    assert adapted.length_check["status"] == "OVER"
    assert adapted.meta["checks"]["summary"]["passed"] is True
    numerical = [i for i in adapted.meta["checks"]["issues"] if i["rule"] in ("C4", "C5", "C6", "E4")]
    assert numerical == []  # counts remain metadata, not editorial findings


def test_missing_optional_reader_audit_does_not_prevent_factual_verification(llm, monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("reader unavailable")

    monkeypatch.setattr(review, "newcomer", unavailable)
    adapted, _ = adapt()
    assert "audit" in llm.steps()
    checks = adapted.meta["checks"]
    assert checks["model_ran"] and checks["summary"]["passed"]
    assert not checks["audits_complete"]
    assert any("表达审计没有完成" in p for p in adapted.meta["problems"])


def test_missing_fact_audit_does_not_become_a_success(llm, monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("fact reviewer unavailable")

    monkeypatch.setattr(review, "fact_audit", unavailable)
    adapted, _ = adapt()
    assert adapted.sections and "revise" not in llm.steps()
    assert not adapted.meta["checks"]["model_ran"]
    assert not adapted.meta["checks"]["summary"]["passed"]


def test_adapted_editorial_candidate_repairs_facts_before_selection(llm):
    stuck = {**GOOD_READ, "paragraphs": [{"id": "S1P1", "lost": ["开场在念资料目录"]}]}
    candidate = {"title": "先看缓存写入的账单", "sections": [{"title": "写入", "paragraphs": [
        {"text": "缓存写入是免费的。", "facts": ["F1"]}]}]}
    llm.queues["newcomer"] = [stuck, GOOD_READ, GOOD_READ]
    llm.queues["editorial"] = [candidate]
    llm.queues["audit"] = [CLEAN, {**CLEAN, "issues": [finding("S1P1", "缓存写入是免费的。", problem="与写入价格冲突")]}, CLEAN]
    llm.queues["revise"] = [{"paragraphs": [{"id": "S1P1", "text": "缓存写入是输入价的 1.25 倍。", "facts": ["F1"]}]}]
    adapted, _ = adapt()
    attempt = adapted.meta["editorial_attempt"]
    assert attempt["applied"] and len(attempt["source_repairs"]) == 1
    assert adapted.sections[0]["paragraphs"][0]["text"] == "缓存写入是输入价的 1.25 倍。"
    assert llm.steps().count("compare") == 1 and llm.steps().count("audit") == 3
    assert "免费的" not in llm.request("compare")
    assert adapted.meta["plan"]["goals"] == []  # old section-specific quiz targets do not follow a new story


def test_adapted_editing_starts_with_the_story_when_facts_and_listening_both_need_work(llm):
    raw = draft_json()
    raw["sections"][0]["paragraphs"][0].update(text="缓存写入是免费的。", facts=["F1"])
    llm.queues["draft"] = [raw]
    llm.queues["newcomer"] = [{**GOOD_READ, "paragraphs": [{"id": "S1P1", "lost": ["开场像资料目录"]}]}, GOOD_READ]
    llm.queues["audit"] = [{**CLEAN, "issues": [finding("S1P1", "缓存写入是免费的。")]}, CLEAN]
    llm.queues["editorial"] = [{"title": "缓存先付写入费", "sections": [{"title": "账单", "paragraphs": [
        {"text": "缓存先付写入费，价格是输入价的 1.25 倍。", "facts": ["F1"]}]}]}]
    adapted, _ = adapt()
    assert adapted.meta["checks"]["summary"]["passed"]
    assert adapted.meta["editorial_attempt"]["applied"] and "revise" not in llm.steps()
    assert llm.steps().count("audit") == 2 and llm.steps().count("compare") == 1
    assert "编造的细节" in llm.request("rebrief")


def test_adapted_failed_joint_editing_falls_back_to_repairing_the_original(llm):
    raw = draft_json()
    raw["sections"][0]["paragraphs"][0].update(text="缓存写入是免费的。", facts=["F1"])
    llm.queues["draft"] = [raw]
    llm.queues["newcomer"] = [{**GOOD_READ, "paragraphs": [{"id": "S1P1", "lost": ["开场像资料目录"]}]}, GOOD_READ, GOOD_READ]
    bad = {**CLEAN, "issues": [finding("S1P1", "缓存写入是免费的。")]}
    llm.queues["audit"] = [bad, bad, CLEAN]
    llm.queues["editorial"] = [{"title": "错误候选", "sections": [{"title": "账单", "paragraphs": [
        {"text": "缓存写入是免费的。", "facts": ["F1"]}]}]}]
    llm.queues["revise"] = [{"paragraphs": []}, {"paragraphs": [{"id": "S1P1", "text": "缓存写入是输入价的 1.25 倍。", "facts": ["F1"]}]}]
    adapted, _ = adapt()
    assert adapted.meta["checks"]["summary"]["passed"]
    assert not adapted.meta["editorial_attempt"]["applied"] and "compare" not in llm.steps()
    assert adapted.sections[0]["paragraphs"][0]["text"] == "缓存写入是输入价的 1.25 倍。"


def test_adapted_editorial_passes_are_bounded_and_keep_all_attempts(llm, monkeypatch):
    from core.video import editorial

    monkeypatch.setattr(editorial, "MAX_ROUNDS", 2)
    llm.queues["newcomer"] = [{**GOOD_READ, "paragraphs": [{"id": "S1P1", "lost": ["转场还可更自然"]}]}]
    llm.queues["editorial"] = [
        {"title": "先算写入费用", "sections": [{"title": "账单", "paragraphs": [{"text": "缓存写入要付费。", "facts": ["F1"]}]}]},
        {"title": "写入后能否复用", "sections": [{"title": "复用", "paragraphs": [{"text": "缓存写入之后，还得看后续能否复用。", "facts": ["F1"]}]}]},
    ]
    adapted, _ = adapt()
    assert llm.steps().count("editorial") == 2 and len(adapted.meta["editorial_attempts"]) == 2
    assert all(item["applied"] for item in adapted.meta["editorial_attempts"])
    assert adapted.title == "写入后能否复用" and adapted.meta["checks"]["summary"]["passed"]


def test_adapted_workflow_can_choose_copyediting_for_a_local_transition(llm):
    llm.queues["newcomer"] = [{**GOOD_READ, "paragraphs": [{"id": "S1P1", "lost": ["开场两句接得不顺"]}]}, GOOD_READ]
    llm.queues["rebrief"][0].update(edit_mode="copyedit", edit_reason="保留主线，只接好两句话")
    llm.queues["editorial"] = [{"title": "缓存先付写入费", "sections": [{"title": "写入", "paragraphs": [
        {"text": "缓存要先写入，而写入会按输入价的 1.25 倍计费。", "facts": ["F1"]}]}]}]
    adapted, _ = adapt()
    assert llm.steps().count("copyedit") == 1 and "editorial" not in llm.steps()
    assert adapted.meta["editorial_attempt"]["applied"] and adapted.meta["checks"]["summary"]["passed"]


def test_adapted_workflow_can_try_another_story_after_rejecting_a_candidate(llm):
    stuck = {**GOOD_READ, "paragraphs": [{"id": "S1P1", "lost": ["主线还在罗列资料"]}]}
    llm.queues["newcomer"] = [stuck, stuck, GOOD_READ]
    llm.queues["editorial"] = [
        {"title": "缓存有很多优点", "sections": [{"title": "开场", "paragraphs": [{"text": "缓存值得关注。", "facts": ["F1"]}]}]},
        {"title": "缓存先付写入费", "sections": [{"title": "账单", "paragraphs": [{"text": "缓存先写入，写入会按输入价的 1.25 倍计费。", "facts": ["F1"]}]}]},
    ]
    llm.queues["compare"] = [{"choice": "A", "reason": "候选只剩口号，原稿还有具体内容"},
                             {"choice": "B", "reason": "从具体账单讲起，能跟上因果"}]
    adapted, _ = adapt()
    assert [a["applied"] for a in adapted.meta["editorial_attempts"]] == [False, True]
    assert "候选只剩口号" in llm.request("rebrief", 1)
    assert "候选只剩口号" not in llm.request("editorial", 1)
    assert adapted.title == "缓存先付写入费" and adapted.meta["checks"]["summary"]["passed"]


def test_relaxing_optional_checks_does_not_ignore_cancellation(llm, monkeypatch):
    def cancelled(*args, **kwargs):
        raise CancelledError("user stopped the workflow")

    monkeypatch.setattr(review, "newcomer", cancelled)
    with pytest.raises(CancelledError):
        adapt()
    assert "audit" not in llm.steps()


def test_recipe_section_count_and_roles_do_not_generate_warnings_or_retries(llm):
    plan = plan_json(beats=2)
    plan["must_cover"][0]["beat"] = 2
    llm.queues["plan"] = [plan]
    made, notes = planning.make(llm(None), sheet_of(), column(), {}, style(), 6)
    assert llm.steps() == ["plan"] and len(made["beats"]) == 2
    assert notes == []


def test_fact_extraction_keeps_conditions_and_sources_beyond_the_old_report_limit(llm):
    tail = "GPT-OSS 在 NVIDIA B200、AWS 上测试，相对标准 vLLM 最多提高 70%。"
    report = REPORT + "资料。" * 25000 + tail + "\n" + BATCH
    facts.extract(llm(None), report, {})
    assert tail in llm.request("facts")
    request = writer._request(report, {}, "快讯", 1, 250, (20, 90))
    assert request.endswith(tail + "\n" + BATCH)


@pytest.mark.parametrize("rule", ["C4", "C5", "C6", "E4", "S1", "N1", "N2", "N3"])
def test_expression_advice_never_becomes_an_automatic_revision_target(rule):
    item = review.issue(rule, review.MUST, "表达建议", 0, 0)
    if rule == "S1":
        item["audit"] = "communication"
    checks = {"issues": [item], "newcomer": {"scores": {"hook": 1}}}
    assert review.targets(copy.deepcopy(draft_json()["sections"]), checks) == {}

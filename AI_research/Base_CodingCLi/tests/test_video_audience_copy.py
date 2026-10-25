"""Research gaps must not become audience copy or unsupported source claims."""
from core.video.audience_copy import GUIDANCE, findings
from core.video import writer, audit
from core.video.adapt import review
from core.video.policy import is_core_issue
from tests.test_video_scripts import (GOOD, _login, _make, _roundup_budget, script_json,
                                      llm, research, video_env)  # noqa: F401
import json


def test_internal_gaps_are_flagged_without_banning_real_conditions_or_material_science():
    for text in ("材料没有给出量化续航、具体售价。", "现有资料未提供测试结果。", "缺少缺点结论。"):
        assert findings(text)
        assert writer.lint([{"paragraphs": [{"text": text}]}])
    for text in ("这种材料没有毒性。", "材料没有毒性。", "材料缺少导电性。", "官方标称在实验室测试中续航最高72小时。",
                 "开箱不能说明实际降噪效果。", "这款材料采用特殊工艺。"):
        assert not findings(text)


def test_source_omission_guidance_reaches_the_auditors():
    for prompt in (audit.PROMPT, audit.READER_PROMPT, review.NEWCOMER_PROMPT, review.FACT_AUDIT_PROMPT):
        assert GUIDANCE in prompt


def test_natural_first_person_and_introduced_sources_are_not_blanket_banned():
    for text in ("我还没找到 Anthropic 的官方公告。", "我的判断是它更适合单人使用。",
                 "据 The Verge 的报道，这次调整影响免费用户。这篇报道引用了官方声明。"):
        assert not findings(text)


def test_single_workflow_selects_title_even_when_body_has_no_editorial_issues(client, video_env, research, llm):
    payload = json.loads(script_json(300))
    payload["title"] = "自动作者的标题"
    llm.replies = [json.dumps(payload)]
    _login(client)
    script = _make(client, "point_short", "直接生成")
    assert script["version_shown"]["title"] == payload["title"]
    assert len(llm.calls) == 1 and not llm.headline_calls and not llm.audit_calls


def test_concrete_internal_copy_gets_repaired_and_reaudited(client, video_env, research, llm):
    payload = json.loads(script_json(300, citations={(0, 0): [GOOD]}))
    text = "材料没有提供数据。这是一个开源工具。"
    payload["sections"][0]["paragraphs"][0]["text"] = text
    llm.replies = [json.dumps(payload)]
    _login(client)
    script = _make(client, "point_short", "不按预设问题改稿")
    shown = script["version_shown"]
    assert shown["sections"][0]["paragraphs"][0]["text"] == text
    assert not llm.revise_calls and not llm.audit_calls and shown["checks"]["issues"] == []


def test_failed_fact_audit_preserves_pending_script_without_blind_rewrite(client, video_env, research, llm):
    payload = json.loads(script_json(_roundup_budget("point_short") - 5))
    para = payload["sections"][0]["paragraphs"][0]
    para.update(needs_review=True, review_reason="出处仍需核实")
    llm.replies = [json.dumps(payload)]
    llm.audit_replies = ["无效 JSON"] * 3
    _login(client)
    script = _make(client, "point_short", "审计失败不盲改")
    shown = script["version_shown"]
    assert not shown["checks"]["model_ran"]
    assert shown["sections"][0]["paragraphs"][0]["text"] == para["text"]
    assert shown["sections"][0]["paragraphs"][0]["evidence"] == "NEEDS_REVIEW"
    assert not llm.revise_calls


def test_unresolved_internal_copy_cannot_slip_through_automatic_approval(client, video_env, research, llm):
    from core.video import store
    from web.services import video_executor
    payload = json.loads(script_json(300))
    payload["sections"][0]["paragraphs"][0]["text"] = "材料没有提供数据。这是一个开源工具。"
    llm.replies = [json.dumps(payload)]
    _login(client)
    made = _make(client, "point_short", "不预设拦截")
    script = store.get_script(made["id"])
    decision = video_executor.advance_auto(script, {}, can_start_script=True, can_start_render=True)
    assert decision["hold"] is None and not decision["took"]["script"]
    assert store.get_script(script["id"])["status"] == "APPROVED"
    assert decision["item"]["gate"]["audited"] is False

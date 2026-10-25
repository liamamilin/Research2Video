"""Regression: shortening a performance claim must not erase its benchmark conditions."""

import copy
import json

import pytest

from core.llm import ChatResponse
from core.video import audit, credibility
from core.video.adapt import review
from core.video.claim_bounds import lost_conditions

SOURCE = "https://github.com/llm-d/llm-d/blob/main/README.md"
ORIGINAL = "官方说，在 GPT-OSS 模型和 B200 显卡的测试中，对比标准 vLLM，分离式部署的每秒输出 token 最多提升 70%。"
SHORTENED = "分离式部署把预填充和解码拆到不同机器上跑，对比标准 vLLM，最多提 70% 每秒输出 token。"
REPORT = ORIGINAL + "\n来源：" + SOURCE


class Reply:
    def __init__(self, result):
        self.result = result
        self.messages = []

    def chat(self, messages, **kwargs):
        self.messages.append(messages)
        return ChatResponse(content=json.dumps(self.result, ensure_ascii=False))


def paragraph(text=ORIGINAL, evidence="NEEDS_REVIEW"):
    return {"text": text, "citations": [SOURCE], "evidence": evidence, "review_reason": "需检查测试条件"}


def proof(**overrides):
    return {"id": "S1P1", "status": "verified", "reason": "当前数字、模型、硬件和对比基线与来源一致",
            "sources": [SOURCE], **overrides}


def test_removed_literal_parameters_require_contextual_review_instead_of_reverting_the_rewrite():
    assert {"GPT-OSS", "B200", "官方说", "测试中"} <= set(lost_conditions(ORIGINAL, SHORTENED, REPORT))
    sections = [{"title": "性能", "paragraphs": [paragraph()]}]
    revised, changed = audit.revise(Reply({"paragraphs": [{"id": "S1P1", "text": SHORTENED}]}),
                                     sections, REPORT, {"S1P1": ["精简型号和硬件参数"]})
    assert revised[0]["paragraphs"][0]["text"] == SHORTENED
    assert changed[0]["revision"]["accepted"] is True
    assert changed[0]["evidence"] == "NEEDS_REVIEW"
    assert "B200" in changed[0]["revision"]["lost_conditions"]
    assert sections[0]["paragraphs"][0].get("revision") is None


def test_complete_claim_may_be_removed_and_equivalent_qualifier_is_allowed():
    assert lost_conditions(ORIGINAL, "分离式部署把预填充和解码拆到不同机器上跑。", REPORT) == []
    assert lost_conditions(ORIGINAL, ORIGINAL.replace("最多", "最高").replace("70%", "70 %"), REPORT) == []


def test_an_entire_irrelevant_section_may_be_removed_but_the_script_cannot_be_emptied():
    from core.video.adapt.common import AdaptError

    sections = [{"title": "主线", "paragraphs": [paragraph("本期主要看运维改变了什么。", "CITED")]},
                {"title": "旁支数据", "paragraphs": [paragraph()]}]
    revised, _ = audit.revise(Reply({"paragraphs": [{"id": "S2P1", "text": ""}]}), sections, REPORT,
                              {"S2P1": ["这组性能数字不支持本期主线，可以整节删除"]})
    assert [s["title"] for s in revised] == ["主线"]
    assert len(sections) == 2
    with pytest.raises(AdaptError, match="整篇文稿"):
        audit.revise(Reply({"paragraphs": [{"id": "S1P1", "text": ""}]}), revised, REPORT,
                     {"S1P1": ["删除"]})


def test_deleting_context_sends_the_retained_numeric_claim_back_for_review():
    context = "AWS 的测试用 GPT-OSS 模型和 NVIDIA B200 显卡。"
    claim = "对比标准 vLLM，预填充和解码分开部署后，每秒输出 token 最多提升 70%。"
    sections = [{"paragraphs": [paragraph(context), paragraph(claim)]}]
    revised, changed = audit.revise(Reply({"paragraphs": [{"id": "S1P1", "text": ""}]}),
                                     sections, context + claim + SOURCE, {"S1P1": ["删掉测试参数"]})
    assert len(revised[0]["paragraphs"]) == 1
    assert revised[0]["paragraphs"][0]["text"] == claim
    assert all(p["evidence"] == "NEEDS_REVIEW" for p in changed)
    assert "B200" in changed[0]["revision"]["lost_conditions"]


def test_clearer_attributed_summary_can_pass_without_reciting_all_benchmark_parameters():
    shorter = "按官方公布的这组测试，对比标准 vLLM，分离式部署的每秒输出 token 最多提升 70%。"
    sections = [{"paragraphs": [paragraph(evidence="CITED")]}]
    revised, _ = audit.revise(Reply({"paragraphs": [{"id": "S1P1", "text": shorter}]}),
                              sections, REPORT, {"S1P1": ["把参数留在资料里，自然说明这是官方测试"]})
    current = revised[0]["paragraphs"][0]
    assert current["text"] == shorter and current["evidence"] == "NEEDS_REVIEW"
    audit.reconcile_evidence(revised, [], [proof(reason="准确归因官方的这组测试，未声称普遍收益或独立复测")])
    assert current["evidence"] == "CITED"
    assert sections[0]["paragraphs"][0]["text"] == ORIGINAL


def test_a_correct_paragraph_can_gain_a_trusted_citation_without_unnecessary_wording_changes():
    para = paragraph()
    para["citations"] = []
    sections = [{"paragraphs": [para]}]
    revised, changed = audit.revise(Reply({"paragraphs": [{"id": "S1P1", "citations": [SOURCE]}]}),
                                     sections, REPORT, {"S1P1": ["缺少出处"]}, trusted_sources={SOURCE})
    current = revised[0]["paragraphs"][0]
    assert changed == [current] and current["text"] == ORIGINAL and current["citations"] == [SOURCE]
    assert current["evidence"] == "NEEDS_REVIEW"  # adding a citation is not itself a fact verification
    audit.reconcile_evidence(revised, [], [proof()], required={"S1P1"})
    assert current["evidence"] == "CITED"


def test_a_rewrite_cannot_add_a_url_rejected_by_the_reports_citation_check():
    rejected = "https://example.org/not-retrieved"
    sections = [{"paragraphs": [paragraph()]}]
    revised, _ = audit.revise(Reply({"paragraphs": [{"id": "S1P1", "text": ORIGINAL, "citations": [rejected]}]}),
                              sections, REPORT + "\n" + rejected, {"S1P1": ["补出处"]}, trusted_sources={SOURCE})
    assert revised[0]["paragraphs"][0]["citations"] == [SOURCE]
    assert revised[0]["paragraphs"][0]["evidence"] == "NEEDS_REVIEW"
    assert "可信来源之外" in revised[0]["paragraphs"][0]["review_reason"]


def test_price_units_are_protected():
    before = "该模型每百万输入 token 收费 $5。"
    assert "每百万" in lost_conditions(before, "该模型每次输入收费 $5。", before)
    assert "输入" in lost_conditions(before, "该模型每百万输出 token 收费 $5。", before)


@pytest.mark.parametrize("verifications", [[], [proof(sources=[])], [proof(reason="")],
                                         [proof(sources=["https://example.org/untrusted"])],
                                         [proof(status="needs_review")]])
def test_empty_findings_or_untrusted_proof_cannot_clear_a_review_mark(verifications):
    sections = [{"paragraphs": [paragraph()]}]
    audit.reconcile_evidence(sections, [], verifications)
    para = sections[0]["paragraphs"][0]
    assert para["evidence"] == "NEEDS_REVIEW" and para["review_reason"]
    assert para["fact_verification"]["status"] == "needs_review"


def test_source_backed_explicit_proof_clears_only_the_verified_paragraph():
    sections = [{"paragraphs": [paragraph(), paragraph()]}]
    audit.reconcile_evidence(sections, [], [proof()])
    first, other = sections[0]["paragraphs"]
    assert first["evidence"] == "CITED" and "review_reason" not in first
    assert other["evidence"] == "NEEDS_REVIEW"
    assert first["fact_verification"]["sources"] == [SOURCE]


def test_a_fact_issue_overrides_a_positive_proof():
    sections = [{"paragraphs": [paragraph()]}]
    problem = review.issue("D2", review.SERIOUS, "测试条件丢失", 0, 0, source="model")
    audit.reconcile_evidence(sections, [problem], [proof()])
    assert sections[0]["paragraphs"][0]["review_reason"] == "测试条件丢失"


def test_rewritten_cited_paragraph_needs_a_fresh_explicit_verdict():
    sections = [{"paragraphs": [paragraph(evidence="CITED")]}]
    audit.reconcile_evidence(sections, [], [], required={"S1P1"})
    assert sections[0]["paragraphs"][0]["evidence"] == "NEEDS_REVIEW"


def test_explicit_nonfactual_verdict_can_release_a_transition_but_not_a_number():
    sections = [{"paragraphs": [paragraph("接着看部署方式。"), paragraph()]}]
    audit.reconcile_evidence(sections, [], [proof(status="not_factual", sources=[]),
                                          proof(id="S1P2", status="not_factual", sources=[])])
    assert sections[0]["paragraphs"][0]["evidence"] == "UNCITED"
    assert sections[0]["paragraphs"][1]["evidence"] == "NEEDS_REVIEW"


def test_scoped_fact_audit_rejects_findings_and_proofs_for_other_paragraphs():
    sections = [{"paragraphs": [paragraph(), paragraph()]}]
    client = Reply({"issues": [{"id": "S1P2", "rule": "D2", "problem": "范围外"}],
                    "verifications": [proof(), proof(id="S1P2")]})
    verifications = []
    assert audit.fact_issues(client, sections, REPORT, only={"S1P1"}, verifications=verifications) == []
    assert [v["id"] for v in verifications] == ["S1P1"]
    assert "本段可信引用" in client.messages[0][-1]["content"]
    request = client.messages[0][-1]["content"]
    assert "[S1P2]" in request.split("## 全文上下文", 1)[1].split("## 要检查的段落", 1)[0]
    assert "[S1P2]" not in request.split("## 要检查的段落", 1)[1]


def test_failed_post_rewrite_audit_keeps_a_review_mark():
    class Unavailable:
        def chat(self, *args, **kwargs):
            raise RuntimeError("offline")

    sections = [{"paragraphs": [paragraph(evidence="CITED")]}]
    checks, note = audit.audit(Unavailable(), sections, REPORT, rule_issues=[], verification_required={"S1P1"})
    assert checks["model_ran"] is False and checks["audits_complete"] is False
    assert sections[0]["paragraphs"][0]["evidence"] == "NEEDS_REVIEW"
    assert "事实审计没有完成" in note


def test_credibility_explains_pending_reason_and_incomplete_audits():
    sections = [{"paragraphs": [paragraph()]}]
    checks = review.assemble([], {"issues": []}, {"issues": []}, None, sections)
    version = {"sections": sections, "meta": {"checks": checks}}
    assert "需检查测试条件" in " ".join(credibility.label(version)["reasons"])
    clean = copy.deepcopy(version)
    clean["sections"][0]["paragraphs"][0]["evidence"] = "CITED"
    clean["meta"]["checks"]["audits_complete"] = False
    assert credibility.label(clean)["tier"] == "trusted"  # optional expression checks do not block facts

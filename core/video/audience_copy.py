"""Audience-facing copy checks, distinct from source-backed factual conditions."""
from __future__ import annotations

import re


GUIDANCE = '根据素材，产出目标观众愿意看、听得懂、想继续看的视频。'

_INTERNAL_GAP = re.compile(
    r"(?<![\u4e00-\u9fffA-Za-z0-9])(?:材料|这份材料|现有材料|现有资料|所给资料|这些资料|研究摘要|内部报告|本次摘要|资料|摘要)"
    r"(?:里|中)?(?:也|还|并)?(?:(?:没有|未|没)(?:提供|给出|说明|提及|列出|收录|写明|交代|讲明|讲清|讲|给|说)"
    r"|(?:没有|未|没)(?:相关|具体|量化|日常)?(?:测试)?(?:数据|价格|售价|结论|出处)"
    r"|缺少(?:相关|具体|测试)?(?:数据|证据|来源|结论))[^。！？；\n]*"
    r"|缺少缺点结论"
)

_SOURCE_ABSENCE = re.compile(
    r"(?:报道|报告|论文|原文|仓库|条目)(?:里|中)?(?:也|还|并)?(?:未|没|没有)"
    r"(?:给出|提供|披露|公开|说明|交代|列出|展开|写明|记录|附上)[^。！？；\n]*"
    r"|(?:具体容量|许可证|成片效果|研究方法|评测协议|样本量|消融结果|逐项分数)"
    r"(?:也|还)?(?:没写|未披露|没有披露|未公开|没有公开|未给出|没有给出)[^。！？；\n]*"
    r"|(?:许可证|成片效果)(?:也|还)?没有(?:公开)?说明[^。！？；\n]*"
)


def unverified_source_absences(version: dict) -> list[dict]:
    """A summary's missing field cannot certify that the actual source omits it.

    A recorded original-page review plus the paragraph's completed fact audit
    can support a real omission. Ordinary uncertainty and experimental bounds
    are deliberately outside this pattern.
    """
    meta = version.get("meta") or {}
    review = meta.get("source_review") or meta.get("editorial_review") or {}
    checked = set(review.get("checked_source_urls") or review.get("verified_source_urls") or [])
    recorded = bool(review.get("packet_sha256") or review.get("source_packet_sha256"))
    out = []
    for si, section in enumerate(version.get("sections", [])):
        for pi, paragraph in enumerate(section.get("paragraphs", [])):
            proof = paragraph.get("fact_verification") or {}
            sources = set(proof.get("sources") or [])
            if recorded and proof.get("status") == "verified" and sources and sources <= checked:
                continue
            for match in _SOURCE_ABSENCE.finditer(paragraph.get("text") or ""):
                out.append({"section": si, "paragraph": pi, "quote": match.group(),
                            "problem": "关于原来源缺少信息的断言尚无原文核验依据，不能用摘要缺项代替",
                            "suggestion": "核对原文后记录依据；无法核实时删除或收窄缺失断言，保留真正的研究范围和不确定性"})
    return out


def findings(text: str) -> list[dict[str, str]]:
    """Flag concrete process leaks, without banning words such as 材料 or 不能."""
    return [{"quote": match.group(),
             "problem": "内部资料缺口进入口播，观众不知道所指资料，且容易误解为原来源缺少信息",
             "suggestion": "将研究缺口留在审校记录；删除或收窄无依据的结论，保留与具体主张相关的事实限定"}
            for match in _INTERNAL_GAP.finditer(text)]


def section_findings(sections: list[dict]) -> list[dict]:
    """Locate current copy problems, independently of any saved model audit."""
    return [{**finding, "section": si, "paragraph": pi}
            for si, section in enumerate(sections)
            for pi, paragraph in enumerate(section.get("paragraphs", []))
            for finding in findings(paragraph.get("text") or "")]


def submission_problem(version: dict) -> str:
    """A local handoff check; never rewrite approved words or clear factual marks."""
    return ""

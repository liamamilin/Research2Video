"""One credibility label per script version: how far its words can be trusted, and why.

The unattended pipeline holds ``serious``, ``verify`` and ``unaudited`` after automatic repair and retries
(see web/services/video_executor.py HOLD_TIERS). Every list that leads to a publishing decision shows the label.
Tiers, worst first:

* serious    有严重问题   the fact audit found something serious
* verify     有待核实     a paragraph is marked as needing review (it names which)
* spoken     口播有问题   narration advice, shown without blocking the core fact gate
* unaudited  未审计       the model audit never ran, so the facts were not checked by anything
* partial    部分无出处   audited and nothing serious, but many paragraphs cite no source
* trusted    有出处可信   audited, nothing serious, most paragraphs cite a source

It is computed from the version's stored checks and paragraph marks on every read, so an edit or a finished audit
changes it with no migration. ``SPOKEN_RULES`` are the rules whose finding means a broken video.
"""

from __future__ import annotations

from core.video.adapt import review

SPOKEN_RULES = ("D5", "E3")
#: Below this share of cited paragraphs a version is only partly sourced.
CITED_SHARE = 0.6

TIERS = {
    "serious": "有严重问题",
    "verify": "有待核实",
    "spoken": "口播有问题",
    "unaudited": "未审计",
    "partial": "部分无出处",
    "trusted": "有出处可信",
    "generated": "按素材生成",
}
#: Worst first, for sorting and for "worse than".
ORDER = tuple(TIERS)


def label(version: dict | None, *, auditing: bool = False) -> dict:
    """{"tier", "text", "reasons": [...], "cited_share": float|None} for a stored version.

    ``auditing``: the audits are still running, so "unaudited" is only for now."""
    if not version:
        return {"tier": "unaudited", "text": TIERS["unaudited"], "reasons": ["还没有文稿"], "cited_share": None}
    if (version.get('meta') or {}).get('local_video'):
        return {'tier': 'generated', 'text': '本地视频', 'reasons': [], 'cited_share': None}
    sections = version.get("sections") or []
    checks = (version.get("meta") or {}).get("checks")
    checks = review.normalize(checks, len(sections)) if checks else None
    summary = (checks or {}).get("summary") or {}
    issues = review.current_issues((checks or {}).get("issues") or [], sections, version.get("meta"))
    marks = [p.get("evidence") for s in sections for p in s.get("paragraphs", [])]
    to_check = [f"第 {si + 1} 节第 {pi + 1} 段" + (f"（{p['review_reason'][:120]}）" if p.get("review_reason") else "")
                for si, s in enumerate(sections)
                for pi, p in enumerate(s.get("paragraphs", [])) if p.get("evidence") == "NEEDS_REVIEW"]
    cited = sum(1 for m in marks if m == "CITED")
    share = cited / len(marks) if marks else None
    if checks and checks.get("mode") == "on_request":
        return {"tier": "generated", "text": TIERS["generated"], "reasons": [], "cited_share": share}
    reviewed = sum(1 for m in marks if m == "NEEDS_REVIEW")
    reasons: list[str] = []
    fact_ran = bool(checks and checks.get("model_ran"))
    ran = fact_ran

    serious = int(summary.get("serious") or 0)
    spoken = [i for i in issues if i.get("source") == "audience_copy"
              or (i.get("rule") in SPOKEN_RULES and i.get("severity") in (review.MUST, review.SERIOUS))]
    if serious:
        reasons.append(f"事实审计有 {serious} 条严重问题")
    if reviewed:
        reasons.append(f"{reviewed} 段被标为需要核实：" + "、".join(to_check))
    if spoken:
        reasons.append("口播里会读出：" + "、".join(sorted({i["rule"] + (" 审稿用语" if i["rule"] == "D5" else " 不能说的话") for i in spoken})))
    if (version.get("meta") or {}).get("article"):
        pending = not checks or not checks.get("fidelity_checked") or checks.get("model_checks_stale")
        must = any(i.get("severity") in {"must", "serious"} for i in issues)
        tier = "serious" if serious else "verify" if reviewed or must else "unaudited" if pending else "generated"
        return {"tier": tier, "text": TIERS[tier] if tier != "generated" else "已核对原文",
                "reasons": reasons + (["当前版本需重新核对原文"] if pending else [])
                           + ["仅核对原文一致性与忠实度，未做外部事实核验"], "cited_share": share}
    if not ran:
        reasons.append("事实审计还在进行" if auditing else "文稿审计没有全部跑完" if fact_ran
                       else "事实审计没有跑完，没有人核对过事实")
    if share is not None and share < CITED_SHARE:
        reasons.append(f"只有 {round(share * 100)}% 的段落标了出处")
    if (version.get("length_check") or {}).get("status") not in (None, "OK"):
        reasons.append("长度偏离配方参考（仅提醒）")

    if serious:
        tier = "serious"
    elif reviewed:
        tier = "verify"
    elif spoken:
        tier = "spoken"
    elif not ran:
        tier = "unaudited"
    elif share is not None and share < CITED_SHARE:
        tier = "partial"
    else:
        tier = "trusted"
        reasons = [f"{round((share or 0) * 100)}% 的段落标了出处，事实审计没有发现严重问题"] + reasons
    return {"tier": tier, "text": TIERS[tier], "reasons": reasons, "cited_share": share}

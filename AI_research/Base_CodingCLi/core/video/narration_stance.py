"""Keep new narration on the subject, and keep the limits the audience can use.

A research memo is not said as "I will not make this up" or "go check the
website". It is also not rewritten into "the official source never published
it" or "the product cannot do that". Facts that were not withdrawn stay.
Quotations, tutorials, and real conditions stay.

A missing price or score is a lookup memo. A missing control group, sample
size, independent check, or disclosed evaluation condition can change whether
a comparison holds. That limit stays when it is already said to the audience.
When the only copy is an internal memo and a safe sentence cannot be made
without inventing an official statement, the paragraph is marked for another
draft and automatic approval stops. Already approved scripts are not rewritten.
"""
from __future__ import annotations

import copy
import re


LISTENING_DIRECTION = (
    "从材料里挑值得讲的发现和判断，用具体变化把它讲清。"
    "影响理解或用法的条件，直接说它会怎样改变结果。"
    "对照组、样本量、预印本、评测是否独立、条件有没有披露，会改变比较能不能成立，就照实说。"
    "研究和制作备忘留在稿外。"
    "材料里没有的细节就不要念；留下已经成立的事实。"
    "不要用「这次比较没有包括」或「这次没有讲」填掉删去的备忘。"
    "只有剩下的话会让人以为比较已经覆盖全部对象时，才保留准确的范围。"
    "拿不准就留在备注，不要编官方没公布，也不要把没写到说成做不到。"
)

_SENTENCE = re.compile(r"[^。！？]*[。！？]|[^。！？]+$")
_QUOTE = re.compile(r"「[^」]{0,300}」|“[^”]{0,300}”|『[^』]{0,300}』|\"[^\"\n]{0,300}\"")
_SPLIT = re.compile(r"[，,]")
_PERFORMANCE = re.compile(
    r"我(?:也)?不会(?:编|说|替)"
    r"|不能替[^。！？，,]{0,24}?下结论"
    r"|别自己脑补|不要自己脑补|别脑补"
    r"|你自己去官网|自己再看看官网|自己去官网"
    r"|比听我(?:说|讲)"
    r"|我没有乱说|不是我乱说|我没瞎编|不是我瞎说"
    r"|打包票"
)
_GAP = re.compile(
    r"(?:这批|这份|现有|所给|这些|本次|手头|内部)?"
    r"(?:材料|资料|摘要|底稿)"
    r"(?:里|中)?(?:也|还|并|都)?"
    r"(?:没有|没|未)"
    r"(?:给|说|交代|提供|列出|写明|查到|提到|讲|收录|(?=[，,。！？]|$))"
)
_WITHDRAWAL = re.compile(r"不能(?:替[^。！？，,]{0,24}?)?下结论|我(?:也)?不会(?:说|编)|打包票")
_OFFICIAL = re.compile(r"官方.{0,8}?(?:没有|未|没)(?:公布|披露|公开|发布)|并(?:未|没有)(?:公布|披露|公开)")
_DENIAL = re.compile(r"不会|不支持|并不具备|做不到")
_COMPLETENESS = re.compile(r"五家|这几家都|全部|每一家|所有(?:产品|这些)|都能")
_NEGATED_SCOPE = re.compile(r"不能|不是|并不|不代表|尚未|还没|不要以为|别当成|没法说")
_CONSEQUENTIAL = re.compile(
    r"对照组|样本量|样本数|个样本|小样本|预印本|非独立|未独立|独立复现|评测条件|评价条件|实验条件|未披露"
)
_AUDIENCE_LIMIT = re.compile(
    r"尚未|还没验证|还没有|不能推广|不代表|只在|仅在|预印本|非独立|未独立|未披露|不能据此|样本量|对照组|个样本|样本数"
)
_NOTE = "口播去掉了自我免责或取材备忘。没有补「这次没有讲」，也没有改写成官方未公布或能力不存在。"
REVIEW_REASON = "比较还缺对照组、样本量，或评测是否独立、条件有没有披露。已有的描述和范围可以留下，但不能当成可靠比较。请重写给观众的限制，不要编官方没公布。"


def _protect(text: str) -> tuple[str, list[str]]:
    quotes: list[str] = []

    def repl(match: re.Match) -> str:
        quotes.append(match.group())
        return f"«Q{len(quotes) - 1}»"

    return _QUOTE.sub(repl, text or ""), quotes


def _restore(text: str, quotes: list[str]) -> str:
    for index, quote in enumerate(quotes):
        text = text.replace(f"«Q{index}»", quote)
    return text


def _clauses(sentence: str) -> list[str]:
    return [part.strip() for part in _SPLIT.split(sentence) if part.strip()]


def _performance(clause: str) -> bool:
    return bool(_PERFORMANCE.search(clause))


def _gap(clause: str) -> bool:
    return bool(_GAP.search(clause)) and "毒性" not in clause and "导电" not in clause


def _misleading_completeness(clause: str) -> bool:
    """A positive claim that the comparison already covers every item.

    A negated limit such as 「不能说五家都能」 is the scope the audience needs,
    so it stays.
    """
    return bool(_COMPLETENESS.search(clause)) and not _NEGATED_SCOPE.search(clause)


def _consequential_gap(clause: str) -> bool:
    """An internal memo about evidence that can change whether a result holds."""
    return _gap(clause) and bool(_CONSEQUENTIAL.search(clause))


def defects(text: str) -> list[dict]:
    """Clauses that perform caution or read an internal gap aloud.

    Quoted speech is ignored. Scoped conditions such as 不代表 and 不能推广,
    and the words 可能/尚未/不是/未验证 by themselves, are not defects.
    """
    found = []
    protected, quotes = _protect(text)
    for sentence in [part.strip() for part in _SENTENCE.findall(protected) if part.strip()]:
        visible = _restore(sentence, quotes)
        if "«Q" in sentence and not _performance(sentence) and not _gap(sentence):
            continue
        for clause in _clauses(sentence):
            if _performance(clause):
                found.append({"quote": _restore(clause, quotes), "sentence": visible, "kind": "self_exoneration"})
            elif _gap(clause):
                found.append({"quote": _restore(clause, quotes), "sentence": visible, "kind": "internal_gap"})
    return found


def _unquoted(text: str) -> str:
    protected, _quotes = _protect(text)
    return re.sub(r"«Q\d+»", "", protected)


def launders(before: str, after: str) -> str:
    """A rewrite that turns a gap into a stronger public claim."""
    before_open, after_open = _unquoted(before), _unquoted(after)
    if _OFFICIAL.search(after_open) and not _OFFICIAL.search(before_open):
        return "把材料缺口说成官方没有公布"
    denial_after = _DENIAL.search(after_open.replace("会不会", ""))
    denial_before = _DENIAL.search(before_open.replace("会不会", ""))
    if _gap(before_open) and "说" in before_open and denial_after and not denial_before:
        return "把没写到说成能力不存在"
    for sentence in [part.strip() for part in _SENTENCE.findall(before_open) if part.strip()]:
        if not (re.search(r"所以", sentence) and _WITHDRAWAL.search(sentence)):
            continue
        for clause in _clauses(sentence):
            if _performance(clause) or _gap(clause) or len(clause) < 6:
                continue
            if clause in after_open:
                return "删掉了撤下的结论限制，留下了过满的判断"
    return ""


def repair(text: str) -> tuple[str, list[dict]]:
    """Drop self-exoneration and internal gap talk. Keep facts that still stand.

    Do not invent a replacement sentence such as 「这次比较没有包括」 or
    「这次没有讲」. When a completeness claim was withdrawn and nothing safe
    remains, the note asks for another draft instead of filling the gap.
    """
    if not defects(text):
        return text, []
    protected, quotes = _protect(text)
    kept, notes = [], []
    for raw in [part.strip() for part in _SENTENCE.findall(protected) if part.strip()]:
        visible = _restore(raw, quotes)
        if not defects(visible):
            kept.append(visible if visible.endswith(("。", "！", "？")) else visible + "。")
            continue
        clauses = _clauses(raw)
        retracts = any("所以" in clause and (_WITHDRAWAL.search(clause) or "不能下结论" in clause) for clause in clauses)
        facts = []
        for clause in clauses:
            if _performance(clause) or _gap(clause):
                continue
            if retracts or (_misleading_completeness(clause) and any(_gap(item) for item in clauses)):
                continue
            facts.append(_restore(clause, quotes).strip("。"))
        spoken = ""
        if facts:
            spoken = "，".join(part for part in facts if part)
            if not spoken.endswith(("。", "！", "？")):
                spoken += "。"
        consequential = any(_consequential_gap(clause) for clause in clauses)
        attention = consequential or (not facts and (retracts or bool(_COMPLETENESS.search(visible)) or bool(_gap(visible))))
        record = _NOTE
        if consequential:
            record += "缺的是会改变比较能不能成立的证据，没有编官方没公布。请重写给观众的限制。"
        elif attention:
            record += "剩下的话会让比较显得已经做完，或没有任何可念的事实。请重写这一处，不要自动补范围套话。"
        notes.append({
            "omitted": visible,
            "spoken_instead": spoken,
            "needs_attention": attention,
            "consequential": consequential,
            "record": record,
        })
        if spoken:
            kept.append(spoken)
    return "".join(kept), notes


def settle_sections(sections: list[dict], *, locked: bool = False) -> tuple[list[dict], dict]:
    """Repair defensive narration. ``locked`` returns the sections untouched."""
    record = {"locked": locked, "changes": [], "gaps": [], "unresolved": [], "kept_original": False}
    if locked:
        return sections, record
    out = copy.deepcopy(sections)
    for si, section in enumerate(out):
        kept = []
        for pi, paragraph in enumerate(section.get("paragraphs") or []):
            before = paragraph.get("text") or ""
            after, gaps = repair(before)
            if after == before:
                kept.append(paragraph)
                continue
            consequential = any(gap.get("consequential") for gap in gaps)
            if consequential:
                record["blocks_auto_approval"] = True
            if not after:
                record["changes"].append({"section": si, "paragraph": pi, "before": before, "after": ""})
                record["gaps"].extend(gaps)
                if consequential:
                    record["unresolved"].append({"section": si, "paragraph": pi, "reason": REVIEW_REASON})
                continue
            if launders(before, after):
                record["unresolved"].append({"section": si, "paragraph": pi, "reason": "收窄会把缺口说重，保留原文"})
                if consequential:
                    paragraph["evidence"] = "NEEDS_REVIEW"
                    paragraph["review_reason"] = REVIEW_REASON
                kept.append(paragraph)
                continue
            paragraph["text"] = after
            record["changes"].append({"section": si, "paragraph": pi, "before": before, "after": after})
            record["gaps"].extend(gaps)
            if consequential or any(gap.get("needs_attention") for gap in gaps):
                record["unresolved"].append({"section": si, "paragraph": pi, "reason": REVIEW_REASON if consequential else "需要重写，不要自动补范围套话"})
            if consequential:
                paragraph["evidence"] = "NEEDS_REVIEW"
                paragraph["review_reason"] = REVIEW_REASON
            kept.append(paragraph)
        section["paragraphs"] = kept
    out = [section for section in out if section.get("paragraphs")]
    if not out:
        record["kept_original"] = True
        record["changes"] = []
        record["unresolved"].append({"reason": "收窄后会删光文稿，保留原文"})
        return copy.deepcopy(sections), record
    return out, record


def apply_to_script(written) -> None:
    """Repair a fresh ``WrittenScript`` and keep the omitted lines in its meta."""
    if (written.meta or {}).get("generation") == "article_verbatim":
        return
    sections, record = settle_sections(written.sections)
    if record.get("kept_original") or not record["changes"]:
        written.meta["narration_stance"] = record
        return
    written.sections = sections
    written.meta["narration_gaps"] = record["gaps"]
    written.meta["narration_stance"] = record

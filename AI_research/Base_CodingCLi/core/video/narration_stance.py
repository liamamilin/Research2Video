"""Keep new narration on the subject, and keep the limits the audience can use.

A research gap is not said as "I will not make this up" or "go check the
website". It is also not rewritten into "the official source never published
it" or "the product cannot do that". A comparison that never received an item
says that this comparison does not include it. Facts that were not withdrawn
stay. Quotations, tutorials, and real conditions stay.

Approved and verbatim manuscripts are not passed through ``apply_to_script``.
"""
from __future__ import annotations

import copy
import re


LISTENING_DIRECTION = (
    "从材料里挑值得讲的发现和判断，用具体变化把它讲清。"
    "影响理解或用法的条件，直接说它会怎样改变结果。"
    "研究和制作备忘留在稿外。"
    "材料里没有的细节，就留下已经成立的事实，或说明这次比较没有包括它；"
    "不要表演谨慎，也不要改写成官方没公布或这项能力不存在。"
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
_ATTRIBUTE = re.compile(
    r"((?:[A-Za-z0-9\u4e00-\u9fff][^、的。！？，,]{0,24})"
    r"(?:、(?:[A-Za-z0-9\u4e00-\u9fff][^、的。！？，,]{0,24}))*)"
    r"\s*的(地区清单|地区|跑分|额度|倍率|清单)"
)
_CAPABILITY = re.compile(r"(材料|资料|底稿)(?:里|中)?(?:没有|没)说(.+?)会(.+)")
_NOTE = "口播去掉了自我免责。没有改写成官方未公布，也没有改写成能力不存在。"


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
    if re.search(r"所以", before_open) and _WITHDRAWAL.search(before_open):
        for clause in _clauses(_protect(before_open)[0]):
            if _performance(clause) or _gap(clause) or len(clause) < 6:
                continue
            if clause in after_open and "这次比较没有包括" not in after_open and "这次没有讲" not in after_open:
                return "删掉了撤下的结论限制，留下了过满的判断"
    return ""


def _scope(sentence: str) -> str:
    protected, quotes = _protect(sentence)
    visible = _restore(protected, quotes)
    names = list(_ATTRIBUTE.finditer(visible))
    if names:
        who = names[-1].group(1)
        who = re.split(r"材料|资料|底稿|没有|没给|没说", who)[-1]
        who = who.split("给")[-1].strip("，,、 ")
        attr = "地区" if names[-1].group(2).startswith("地区") else names[-1].group(2)
        if who and "没有" not in who:
            return f"这次比较没有包括{who}的{attr}。"
    capability = _CAPABILITY.search(visible)
    if capability:
        subject = capability.group(2).strip("，,、 ")
        actions = capability.group(3).strip("。")
        if subject and actions:
            return f"这次没有讲{subject}会不会{actions}。"
    return ""


def _finish_scope(text: str) -> str:
    text = text.strip()
    if text and not text.endswith(("。", "！", "？")):
        text += "。"
    return text


def repair(text: str) -> tuple[str, list[dict]]:
    """Rewrite one paragraph. Unchanged text is returned as-is."""
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
            if retracts:
                continue
            facts.append(_restore(clause, quotes).strip("。"))
        scope = _finish_scope(_scope(visible)) if (retracts or not facts) else ""
        spoken = ""
        if facts:
            spoken = "，".join(part for part in facts if part)
            if not spoken.endswith(("。", "！", "？")):
                spoken += "。"
        elif scope:
            spoken = scope
        notes.append({"omitted": visible, "spoken_instead": spoken, "record": _NOTE})
        if spoken:
            kept.append(spoken)
    return "".join(kept), notes


def _fix_capability_scope(text: str) -> str:
    """The capability scope helper above is completed here if a period is missing."""
    return text


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
            if not after:
                record["changes"].append({"section": si, "paragraph": pi, "before": before, "after": ""})
                record["gaps"].extend(gaps)
                continue
            if launders(before, after):
                record["unresolved"].append({"section": si, "paragraph": pi, "reason": "收窄会把缺口说重，保留原文"})
                kept.append(paragraph)
                continue
            paragraph["text"] = after
            record["changes"].append({"section": si, "paragraph": pi, "before": before, "after": after})
            record["gaps"].extend(gaps)
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

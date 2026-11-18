"""Keep new narration about the subject, not about the narrator's caution.

Spoken text explains the thing, how it happens, and what changes for the viewer.
A condition stays when it changes understanding or use. Research notes and
self-exoneration stay out of the narration. A missing detail is omitted or the
claim is narrowed. It is not performed as caution, and it is not rewritten into
"the official source never published it" or "the capability does not exist".

This module is the shared priority for new drafts. It does not rewrite a script
the caller marks as locked (an already approved or verbatim manuscript).
"""
from __future__ import annotations

import copy
import re


PRIORITY = (
    "口播优先讲清事情本身、过程和结果。"
    "影响理解或用法的条件，直接说它会怎样改变结果。"
    "研究和制作备忘留在稿外。"
    "材料里没有的细节，就收窄或略去这个说法，不要表演谨慎，也不要改写成官方没公布或这项能力不存在。"
    "不要说自己没有乱说、不会编、不能替人下结论，也不要让观众自己去官网核对你的谨慎。"
    "引用、教程示例，以及真正改变用法的范围、价格、日期和地区条件可以保留。"
    "栏目说明、研究底稿和选题里的取材备忘都不是口播；即使它们要求交代材料缺口，也不要说给观众。"
)

RESEARCH_NOTE = (
    "写给后续讲述者的缺口放在备注或字段里，不要写成可以照念的自我说明。"
    "没查到不要写成官方没公布，材料没写到的能力不要写成不存在。"
)

_SENTENCE = re.compile(r"[^。！？]*[。！？]|[^。！？]+$")
_QUOTE = re.compile(r"「[^」]{0,240}」|“[^”]{0,240}”|\"[^\"\n]{0,240}\"")
_GAP = re.compile(
    r"(?:这批|这份|现有|所给|这些|本次|手头|内部|研究)?"
    r"(?:材料|资料|摘要|底稿)"
    r"(?:里|中)?(?:也|还|并|都)?"
    r"(?:没有|没|未)"
    r"(?:给|说|交代|提供|列出|写明|写到|查到|提到|讲|收录|(?=[，,。！？；;]|$))"
)
_SELF = re.compile(
    r"我(?:也)?不会(?:编|说)"
    r"|我不能替[^。！？]{0,40}?下结论"
    r"|比听我说更靠谱"
    r"|别自己脑补|不要自己脑补"
    r"|你自己去官网查"
    r"|我没有乱说|不是我乱说"
    r"|缺少缺点结论"
)
_PHYSICAL = re.compile(r"(?:这种|这款|该类)材料(?:没有|缺少)(?:毒性|导电)")
_OFFICIAL_ABSENCE = re.compile(r"官方.{0,8}?(?:没有|未|没)(?:公布|披露|公开|发布)|并(?:未|没有)(?:公布|披露|公开)")
_DENIAL = re.compile(r"不会|不支持|并不具备|做不到")
_CONNECTOR = re.compile(r"(?:[，,]\s*)?(?:不过|但是|但|所以|因此)\s*$")
_DANGLING = re.compile(r"(?:清单|跑分|额度|倍率|数字|材料|资料)$")
_CLAIM = re.compile(r"含|是|为|变成|可以|会|显示|包括|支持|提供|收费|涨|降|到|能|用|卡在|等于")


def _protect_quotes(text: str) -> tuple[str, list[str]]:
    """Hide quoted speech, including its punctuation, before sentence and defect scans."""
    quotes: list[str] = []

    def repl(match: re.Match) -> str:
        quotes.append(match.group())
        return f"«Q{len(quotes) - 1}»"

    return _QUOTE.sub(repl, text or ""), quotes


def _restore(text: str, quotes: list[str]) -> str:
    for index, quote in enumerate(quotes):
        text = text.replace(f"«Q{index}»", quote)
    return text


def sentences(text: str) -> list[str]:
    protected, quotes = _protect_quotes(text)
    parts = [part.strip() for part in _SENTENCE.findall(protected) if part.strip()]
    return [_restore(part, quotes) for part in parts]


def defects(text: str) -> list[dict]:
    """Narration that performs caution or reads an internal gap aloud.

    A quoted phrase, a tutorial that shows the phrase, a physical material
    property, and a scoped condition (price, date, region, incident recovery,
    experiment reach) are not defects. Bare 可能/尚未/不是/未验证 are not defects.
    """
    found = []
    protected, quotes = _protect_quotes(text)
    for sentence in [part.strip() for part in _SENTENCE.findall(protected) if part.strip()]:
        visible = _restore(sentence, quotes)
        if _PHYSICAL.search(visible) and not _GAP.search(_PHYSICAL.sub("", visible)) and not _SELF.search(sentence):
            continue
        for pattern, kind in ((_GAP, "internal_gap"), (_SELF, "self_exoneration")):
            for match in pattern.finditer(sentence):
                found.append({
                    "quote": match.group(),
                    "sentence": visible,
                    "kind": kind,
                    "start": match.start(),
                    "end": match.end(),
                    "problem": "口播在表演谨慎或念研究备忘，观众听到的不是事情本身",
                    "suggestion": "留下影响用法的条件；没有证据就略去这个说法，并记在制作备注。不要改写成官方没公布，也不要改写成这项能力不存在",
                })
    return found


def launders(before: str, after: str) -> str:
    """A rewrite that turns a research gap into a stronger public claim."""
    if _OFFICIAL_ABSENCE.search(after) and not _OFFICIAL_ABSENCE.search(before):
        return "把材料缺口说成官方没有公布"
    if re.search(r"(?:材料|资料|摘要|底稿).{0,8}(?:没有|没)说", before):
        for match in _DENIAL.finditer(after):
            if match.group() not in before:
                return "把没写到说成能力不存在"
    return ""


def _substantive(head: str) -> bool:
    head = head.strip("，,；; ")
    if len(head) < 4 or defects(head):
        return False
    if _DANGLING.search(head) and not _CLAIM.search(head):
        return False
    if not _CLAIM.search(head) and len(head) < 12:
        return False
    return True


def _prefix(sentence: str) -> str:
    positions = [item["start"] for item in defects(sentence)]
    if not positions:
        return ""
    head = _CONNECTOR.sub("", sentence[:min(positions)].rstrip()).strip("，,；; ")
    if not _substantive(head):
        return ""
    return head if head.endswith(("。", "！", "？")) else head + "。"


def narrow(text: str) -> tuple[str, list[dict]]:
    """Drop defensive sentences. Keep a real claim that precedes the aside.

    Text with no defensive sentence is returned unchanged, including its
    punctuation. The notes are production metadata. They do not assert that an
    official source withheld the fact or that a capability is absent.
    """
    protected, quotes = _protect_quotes(text)
    if not defects(text):
        return text, []
    kept, notes = [], []
    for sentence in [part.strip() for part in _SENTENCE.findall(protected) if part.strip()]:
        visible = _restore(sentence, quotes)
        if not defects(visible):
            kept.append(visible if visible.endswith(("。", "！", "？")) else visible + "。")
            continue
        prefix = _restore(_prefix(sentence), quotes)
        notes.append({
            "omitted": visible,
            "spoken_instead": prefix,
            "record": "口播略去了研究备忘或自我免责。没有改写成官方未公布，也没有改写成能力不存在。",
        })
        if prefix:
            kept.append(prefix)
    return "".join(kept), notes


def settle_sections(sections: list[dict], *, locked: bool = False) -> tuple[list[dict], dict]:
    """Return sections with defensive narration removed, plus the gap record.

    ``locked`` keeps every word. That is the boundary for an approved manuscript
    or a verbatim reading. An edit that would delete the whole script is refused
    and the original sections come back with ``kept_original``.
    """
    record = {"locked": locked, "changes": [], "gaps": [], "unresolved": [], "kept_original": False}
    if locked:
        return sections, record
    out = copy.deepcopy(sections)
    for si, section in enumerate(out):
        kept = []
        for pi, paragraph in enumerate(section.get("paragraphs") or []):
            before = paragraph.get("text") or ""
            after, gaps = narrow(before)
            if _same(before, after):
                kept.append(paragraph)
                continue
            if not after:
                record["changes"].append({"section": si, "paragraph": pi, "before": before, "after": ""})
                record["gaps"].extend(gaps)
                continue
            if launders(before, after):
                record["unresolved"].append({"section": si, "paragraph": pi, "reason": "收窄结果把缺口说重了，保留原文"})
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


def _same(before: str, after: str) -> bool:
    return before == after


def settle_in_place(sections: list[dict]) -> dict:
    """Narrow defensive sentences without copying paragraph objects.

    Fact-repair uses object identity to see what the model rewrote. Copying the
    whole script would look like every paragraph changed. A section that would
    become empty keeps its original paragraphs.
    """
    record = {"changes": [], "gaps": [], "unresolved": []}
    for si, section in enumerate(sections):
        original = list(section.get("paragraphs") or [])
        kept = []
        added_changes = []
        added_gaps = []
        for pi, paragraph in enumerate(original):
            before = paragraph.get("text") or ""
            after, gaps = narrow(before)
            if after == before:
                kept.append(paragraph)
                continue
            if not after:
                added_changes.append({"section": si, "paragraph": pi, "before": before, "after": ""})
                added_gaps.extend(gaps)
                continue
            if launders(before, after):
                record["unresolved"].append({"section": si, "paragraph": pi, "reason": "收窄结果把缺口说重了，保留原文"})
                kept.append(paragraph)
                continue
            paragraph["text"] = after
            added_changes.append({"section": si, "paragraph": pi, "before": before, "after": after})
            added_gaps.extend(gaps)
            kept.append(paragraph)
        if kept:
            section["paragraphs"] = kept
            record["changes"].extend(added_changes)
            record["gaps"].extend(added_gaps)
        elif added_changes:
            record["unresolved"].append({"section": si, "reason": "这一节只剩研究备忘，保留原文"})
    return record


def settle_version(version: dict, *, script_status: str = "") -> tuple[list[dict], dict]:
    """Read-side helper. Approved and verbatim manuscripts are locked."""
    meta = version.get("meta") or {}
    locked = script_status == "APPROVED" or bool(meta.get("approved")) or meta.get("generation") == "article_verbatim"
    return settle_sections(version.get("sections") or [], locked=locked)

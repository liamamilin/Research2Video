"""Drop internal memos from narration without inventing a stronger public claim.

A missing price or score is a lookup memo. A missing control group, sample
size, independent check, or disclosed evaluation condition can change whether
a comparison holds. Lookup memos leave the spoken script; consequential gaps
also stop automatic approval. Already approved scripts are not rewritten.
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
# 「放」 covers 材料没有放完整. 放到 / 放进 / 放在 stays a normal placement.
_GAP = re.compile(
    r"(?:这批|这份|现有|所给|这些|本次|手头|内部)?"
    r"(?:材料|资料|摘要|底稿)"
    r"(?:里|中)?(?:也|还|并|都)?"
    r"(?:没有|没|未)"
    r"(?:放(?!到|进|在)|给|说|交代|提供|列出|写明|查到|提到|讲|收录|(?=[，,。！？]|$))"
)
_ASIDE = re.compile(r"这里要说清楚")
_GAP_TAIL = re.compile(
    r"^(?:也|还|并|又|同时)?(?:没有|没|未)(?:放(?!到|进|在)|给|说|交代|提供|列出|写明|查到|提到|讲|收录)"
)
_ORDINAL = re.compile(r"^第[一二三四五六七八九十百零0-9]+$")
_CONJUNCTION = ("而", "但", "但是", "所以", "不过", "并且")
_BROKEN_JOIN = re.compile(r"第[一二三四五六七八九十百零0-9]+[，,]\s*而")

#: "材料点了四个名字" / "公开材料说脚本未执行" — production sourcing said aloud.
_SOURCE_ATTRIB = (
    (re.compile(r"材料点了"), "有"),
    (re.compile(r"公开材料说(?:过|了)?"), ""),
    (re.compile(r"材料里写的是"), ""),
    (re.compile(r"材料(?:里|中)?(?:写|说)(?:过|了)?"), ""),
    (re.compile(r"材料显示"), ""),
    (re.compile(r"底稿(?:里|中)?(?:写|说)(?:过|了|明)?"), ""),
)
#: Talking about another video or "this one only covers…" as if the viewer saw the programme.
_SEQUEL_META = re.compile(
    r"[^。！？]*别的视频讲的是[^。！？]*[。！？]?"
    r"|今天不(?:重复)?讲(?:它)?(?:发布了什么|上线)[，, ]*"
)

_WITHDRAWAL = re.compile(r"不能(?:替[^。！？，,]{0,24}?)?下结论|我(?:也)?不会(?:说|编)|打包票")
_OFFICIAL = re.compile(r"官方.{0,8}?(?:没有|未|没)(?:公布|披露|公开|发布)|并(?:未|没有)(?:公布|披露|公开)")
_DENIAL = re.compile(r"不会|不支持|并不具备|做不到")
_COMPLETENESS = re.compile(r"五家|这几家都|全部|每一家|所有(?:产品|这些)|都能")
_NEGATED_SCOPE = re.compile(r"不能|不是|并不|不代表|尚未|还没|不要以为|别当成|没法说")
_CONSEQUENTIAL = re.compile(
    r"对照组|样本量|样本数|个样本|小样本|预印本|非独立|未独立|独立复现|评测条件|评价条件|实验条件|未披露"
)
_NOTE = "口播去掉了自我免责或取材备忘。没有补「这次没有讲」，也没有改写成官方未公布或能力不存在。"
REVIEW_REASON = "比较还缺对照组、样本量，或评测是否独立、条件有没有披露。已有的描述和范围可以留下，但不能当成可靠比较。请重写给观众的限制，不要编官方没公布。"
BROKEN_REASON = "收窄后序号或转折接不上，没有把残句念出来。请重写这一处，不要编官方没公布，也不要把没写到说成不支持。"
PROBLEM = "口播里还有取材备忘或收窄后接不上的残句。请重写这一处，不要编官方没公布，也不要把没写到说成不支持。"
_SKIP_GENERATION = {"article_verbatim", "provided_narration"}


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


def _aside(clause: str) -> bool:
    return bool(_ASIDE.search(clause))


def _tail(clause: str) -> bool:
    return bool(_GAP_TAIL.search(clause.strip("。！？")))


def _ordinal(clause: str) -> bool:
    return bool(_ORDINAL.fullmatch(clause.strip("。！？ ")))


def _conjunction(clause: str) -> bool:
    return clause.strip("。！？ ").startswith(_CONJUNCTION)


def broken_join(text: str) -> bool:
    """A comma splice left after a memo clause was removed, such as 「第二，而……」."""
    protected, _quotes = _protect(text or "")
    return bool(_BROKEN_JOIN.search(protected))


def _consequential_gap(clause: str) -> bool:
    return _gap(clause) and bool(_CONSEQUENTIAL.search(clause))


def defects(text: str) -> list[dict]:
    """Clauses that read an internal memo aloud, or a join that no longer forms a sentence."""
    found = []
    protected, quotes = _protect(text)
    for sentence in [part.strip() for part in _SENTENCE.findall(protected) if part.strip()]:
        visible = _restore(sentence, quotes)
        if "«Q" in sentence and not _performance(sentence) and not _gap(sentence) and not _aside(sentence):
            continue
        hit = False
        for clause in _clauses(sentence):
            if _performance(clause):
                found.append({"quote": _restore(clause, quotes), "sentence": visible, "kind": "self_exoneration"})
                hit = True
            elif _gap(clause) or _aside(clause):
                found.append({"quote": _restore(clause, quotes), "sentence": visible, "kind": "internal_gap"})
                hit = True
        if broken_join(visible) and not hit:
            found.append({"quote": visible, "sentence": visible, "kind": "broken_join"})
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


def _without_source_attrib(text: str) -> str:
    """Drop aloud sourcing ("材料点了") while keeping the fact that followed."""
    cleaned = text
    for pattern, repl in _SOURCE_ATTRIB:
        cleaned = pattern.sub(repl, cleaned)
    cleaned = re.sub(r"[，,]{2,}", "，", cleaned)
    cleaned = re.sub(r"。{2,}", "。", cleaned)
    return cleaned.strip("，,；; ")


def _without_sequel_meta(text: str) -> str:
    """Drop programme/sequel asides; keep the actionable clause after a colon when present."""

    def repl(match: re.Match) -> str:
        sentence = match.group(0)
        colon = re.search(r"这条只讲[^：:]*[：:](.+?)([。！？]?)$", sentence)
        if colon:
            body = colon.group(1).strip()
            if body:
                return body + (colon.group(2) or "。")
        return ""

    cleaned = _SEQUEL_META.sub(repl, text)
    return cleaned.strip()


def repair(text: str) -> tuple[str, list[dict]]:
    """Drop memo clauses. Do not speak a dangling ordinal or conjunction.

    Do not invent 「这次没有讲」, 「官方未公布」, or 「不支持」. A sentence whose
    only leftovers are 「第二」 and 「而……」 is left unspoken and marked for
    another draft. Limits already addressed to the audience stay.
    """
    original = text
    notes = []
    demoted = _without_source_attrib(text)
    if demoted != text.strip("，,；; "):
        notes.append({
            "omitted": original,
            "spoken_instead": demoted,
            "needs_attention": False,
            "consequential": False,
            "record": "口播去掉了取材套话，保留已经成立的事实。",
        })
        text = demoted
    before_sequel = text
    text = _without_sequel_meta(text)
    if text != before_sequel:
        notes.append({
            "omitted": before_sequel,
            "spoken_instead": text,
            "needs_attention": False,
            "consequential": False,
            "record": "口播去掉了对其他视频或合集的续集说明。",
        })
    if not text:
        return "", notes
    if not defects(text):
        return text, notes
    protected, quotes = _protect(text)
    kept = []
    for raw in [part.strip() for part in _SENTENCE.findall(protected) if part.strip()]:
        visible = _restore(raw, quotes)
        if not defects(visible):
            kept.append(visible if visible.endswith(("。", "！", "？")) else visible + "。")
            continue
        clauses = _clauses(raw)
        memo = [_gap(clause) or _aside(clause) or _performance(clause) for clause in clauses]
        retracts = any("所以" in clause and (_WITHDRAWAL.search(clause) or "不能下结论" in clause) for clause in clauses)
        facts = []
        dropped_join = False
        for clause, is_memo in zip(clauses, memo):
            if is_memo or (any(memo) and _tail(clause)):
                continue
            bare = clause.strip("。！？ ")
            # Only drop a dangling ordinal / bare conjunction left after a memo, not a full "所以不能说…".
            if _ordinal(clause) or bare in _CONJUNCTION:
                dropped_join = True
                continue
            if retracts or (_COMPLETENESS.search(clause) and not _NEGATED_SCOPE.search(clause) and any(_gap(item) for item in clauses)):
                continue
            facts.append(_restore(clause, quotes).strip("。"))
        spoken = ""
        if facts:
            spoken = "，".join(part for part in facts if part)
            if not spoken.endswith(("。", "！", "？")):
                spoken += "。"
        consequential = any(_consequential_gap(clause) for clause in clauses)
        attention = consequential or not facts or dropped_join or broken_join(spoken)
        if attention and (not facts or dropped_join or broken_join(spoken)):
            spoken = ""
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
        if spoken and not broken_join(spoken):
            kept.append(spoken)
    return "".join(kept), notes


def settle_sections(sections: list[dict], *, locked: bool = False) -> tuple[list[dict], dict]:
    """Repair defensive narration. ``locked`` returns the sections untouched.

    Consequential evidence gaps set ``blocks_auto_approval``. A dangling join
    is recorded in ``unresolved`` and is not written into the spoken text.
    """
    record = {"locked": locked, "changes": [], "gaps": [], "unresolved": [], "kept_original": False}
    if locked:
        return sections, record
    out = copy.deepcopy(sections)
    for si, section in enumerate(out):
        kept = []
        for pi, paragraph in enumerate(section.get("paragraphs") or []):
            before = paragraph.get("text") or ""
            after, gaps = repair(before)
            if after == before and not gaps:
                kept.append(paragraph)
                continue
            consequential = any(gap.get("consequential") for gap in gaps)
            attention = any(gap.get("needs_attention") for gap in gaps)
            if consequential:
                record["blocks_auto_approval"] = True
            if launders(before, after):
                record["unresolved"].append({"section": si, "paragraph": pi, "reason": "收窄会把缺口说重，保留原文"})
                if consequential:
                    paragraph["evidence"] = "NEEDS_REVIEW"
                    paragraph["review_reason"] = REVIEW_REASON
                kept.append(paragraph)
                continue
            record["changes"].append({"section": si, "paragraph": pi, "before": before, "after": after})
            record["gaps"].extend(gaps)
            if attention or consequential:
                record["unresolved"].append({
                    "section": si, "paragraph": pi,
                    "reason": REVIEW_REASON if consequential else BROKEN_REASON,
                })
            if not after:
                continue
            paragraph["text"] = after
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


def note(meta: dict, stance: dict | None) -> None:
    """Copy a settle receipt onto a version meta dict."""
    if not stance:
        return
    if stance.get("gaps"):
        meta["narration_gaps"] = list(meta.get("narration_gaps") or []) + list(stance["gaps"])
    if stance.get("unresolved"):
        meta["narration_unresolved"] = list(meta.get("narration_unresolved") or []) + list(stance["unresolved"])
    if stance.get("blocks_auto_approval"):
        meta["narration_blocks_auto_approval"] = True


def spoken_defects(version: dict) -> bool:
    sections = (version or {}).get("sections") or []
    return any(defects(paragraph.get("text") or "")
               for section in sections for paragraph in section.get("paragraphs") or [])


def _exempt(version: dict) -> bool:
    meta = (version or {}).get("meta") or {}
    article = meta.get("article") or {}
    return bool(meta.get("local_video") or meta.get("generation") in _SKIP_GENERATION or article.get("mode") == "read")


def narration_problem(version: dict, *, rendering: bool = False) -> str:
    """Block a memo or a broken join. Historical approved copy without this receipt still renders."""
    if _exempt(version):
        return ""
    if not spoken_defects(version):
        return ""
    meta = (version or {}).get("meta") or {}
    tracked = any(meta.get(key) for key in (
        "narration_unresolved", "narration_gaps", "narration_blocks_auto_approval", "narration_stance"))
    if rendering and not tracked:
        return ""
    return PROBLEM


def auto_approval_hold(version: dict) -> str:
    """Why a new draft must not be approved by the batch. Empty when it may proceed."""
    if _exempt(version):
        return ""
    meta = (version or {}).get("meta") or {}
    if meta.get("narration_blocks_auto_approval"):
        return "文稿还有会改变结论的证据缺口，没有自动定稿，也没有出片。请重写这一处，不要编官方没公布。"
    if meta.get("narration_unresolved") or spoken_defects(version):
        return "文稿还有取材备忘或收窄后接不上的句子，没有自动定稿，也没有出片。请重写这一处，不要编官方没公布。"
    return ""

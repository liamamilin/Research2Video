"""Shared narration priority for AutoVideo paths that generate speech.

Authored narration is voiced as written. This module only judges text a model
just generated, so a research note is not turned into a new spoken sentence.
The priority string matches Research `core/video/narration_stance.py`.
"""
from __future__ import annotations

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


def _protect_quotes(text: str) -> tuple[str, list[str]]:
    quotes: list[str] = []

    def repl(match: re.Match) -> str:
        quotes.append(match.group())
        return f"«Q{len(quotes) - 1}»"

    return _QUOTE.sub(repl, text or ""), quotes


def defects(text: str) -> list[dict]:
    """Same boundary as Research: quoted speech and scoped conditions stay."""
    found = []
    protected, quotes = _protect_quotes(text)
    for sentence in [part.strip() for part in _SENTENCE.findall(protected) if part.strip()]:
        visible = sentence
        for index, quote in enumerate(quotes):
            visible = visible.replace(f"«Q{index}»", quote)
        if _PHYSICAL.search(visible) and not _GAP.search(_PHYSICAL.sub("", visible)) and not _SELF.search(sentence):
            continue
        for pattern, kind in ((_GAP, "internal_gap"), (_SELF, "self_exoneration")):
            for match in pattern.finditer(sentence):
                found.append({"quote": match.group(), "kind": kind})
    return found


def generated_problem(text: str) -> str:
    found = defects(text)
    if not found:
        return ""
    return "生成旁白把研究备忘或自我免责说给了观众：" + found[0]["quote"]

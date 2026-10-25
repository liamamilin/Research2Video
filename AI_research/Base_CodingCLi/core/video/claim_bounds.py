"""Literal hints about bounds removed from a retained numeric claim.

These checks cannot judge semantic equivalence or which parameters matter to the
current statement. Record removed source-backed anchors for a contextual fact
audit instead of automatically rejecting a clearer rewrite.
"""

from __future__ import annotations

import re

from .adapt.common import quantities

_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-./_][A-Za-z0-9]+)*")
_SENTENCE = re.compile(r"[^。！？；!?;\n]+[。！？；!?;]?")
_QUALIFIERS = (
    ("最多", "最高", "至多", "上限", "up to"),
    ("至少", "最低", "不少于"),
    ("官方说", "官方称", "官方说法", "厂商自述", "厂商称", "按官方", "据官方"),
    ("测试中", "测试里", "基准测试", "实验中", "实验里"),
)
_UNITS = ("每百万", "每秒", "每分钟", "每小时", "每次", "输入", "输出")


def _key(text: str) -> str:
    return re.sub(r"[\W_]", "", text).casefold()


def lost_conditions(before: str, after: str, report: str, *, context_window: int = 0) -> list[str]:
    """Literal source-backed bounds lost while the associated quantity remains."""
    after_key, report_key = _key(after), _key(report)
    remaining = {(value, kind) for value, kind, _ in quantities(after) if kind is not None}
    missing = []
    sentences = _SENTENCE.findall(before)
    for index, sentence in enumerate(sentences):
        claims = {(value, kind) for value, kind, _ in quantities(sentence) if kind is not None}
        if not claims.intersection(remaining):
            continue
        sentence = "".join(sentences[max(0, index - context_window):index + context_window + 1])
        for token in _TOKEN.findall(sentence):
            # Model/hardware IDs and names such as GPT-OSS, B200 and vLLM.
            named = any(c.isdigit() for c in token) or "-" in token or (
                any(c.isupper() for c in token) and any(c.islower() for c in token))
            if named and _key(token) in report_key and _key(token) not in after_key:
                missing.append(token)
        for aliases in _QUALIFIERS:
            present = next((term for term in aliases if term.casefold() in sentence.casefold()), None)
            if present and not any(term.casefold() in after.casefold() for term in aliases):
                missing.append(present)
        for unit in _UNITS:
            if unit in sentence and unit in report and unit not in after:
                missing.append(unit)
    return list(dict.fromkeys(missing))

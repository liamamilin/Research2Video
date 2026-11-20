"""Keep production memos out of the author request.

Joint planning and collectors may still store split notes and packet
bookkeeping. Those lines must not be what the manuscript model is asked to
speak from. See ``spec/manuscript-input-gates.md``.
"""
from __future__ import annotations

import re

#: Substrings that mark production-split or collector bookkeeping.
FORBIDDEN_MARKERS = (
    "同批合集",
    "发布概况只作必要背景",
    "合集只",
    "与单条共同规划",
    "材料编号",
    "采集状态",
)

#: Soft scrub for direction text that still carries a known split phrase.
_SCRUB = (
    (re.compile(r"同批合集仅介绍：[^。]*。?"), ""),
    (re.compile(r"这条展开已分配的独立问题，发布概况只作必要背景。?"), ""),
    (re.compile(r"这是与单条共同规划的合集材料。?"), ""),
    (re.compile(r"合集只[^；。]*[；。]"), ""),
    (re.compile(r"^材料编号：.*$", re.M), ""),
    (re.compile(r"^采集状态：.*$", re.M), ""),
)


def violations(text: str) -> list[str]:
    """Markers found in ``text`` (stable order, unique)."""
    body = text or ""
    return [marker for marker in FORBIDDEN_MARKERS if marker in body]


def scrub(text: str) -> tuple[str, list[str]]:
    """Remove known production memos; return cleaned text and markers that were present."""
    found = violations(text)
    cleaned = text or ""
    for pattern, repl in _SCRUB:
        cleaned = pattern.sub(repl, cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip(), found


def require_clean(text: str, *, where: str = "author request") -> None:
    """Raise ``AssertionError`` when forbidden markers remain (tests / strict checks)."""
    found = violations(text)
    if found:
        raise AssertionError(f"{where} still contains production memos: {', '.join(found)}")

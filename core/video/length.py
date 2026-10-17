"""Spoken-length budget for a video script.

A script is narrated by AutoVideo, so its length is a duration, not a word
count the model picks. The target duration becomes a budget of *spoken
units* — each Chinese character, Latin word and digit is one unit;
punctuation and spaces are silent — split across sections and measured
afterwards.

Spoken units rather than ``len()``: a Latin word inside a Chinese script
("Claude", "API") takes about the time of one character, not one per
letter. Three real AutoVideo renders of Chinese scripts measured 4.30, 4.30
and 4.36 units per second, pauses included; by ``len()`` the same narration
ran at about 6.6 characters per second.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

UNITS_PER_SECOND = 4.3
#: A script more than this far from its budget is flagged, never truncated:
#: cutting narration mid-argument would drop the qualifiers that matter.
#: Was 0.25, under which a six-minute target showed 4.5–7.5 minutes as on
#: target (C4 v6: 437 s, +21%, shown as 达标).
TOLERANCE = 0.15
MIN_MINUTES, MAX_MINUTES = 2, 15

_CJK = re.compile("[" + chr(0x3400) + "-" + chr(0x9FFF) + chr(0xF900) + "-" + chr(0xFAFF) + "]")
_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z0-9'’-]*")
_DIGIT = re.compile(r"\d")


def spoken_units(text: str) -> int:
    """How long ``text`` takes to say, in units."""
    if not text:
        return 0
    return len(_CJK.findall(text)) + len(_LATIN_WORD.findall(text)) + len(_DIGIT.findall(text))


def chinese_share(text: str) -> float:
    """Share of Chinese among the words of ``text`` (a CJK char and a Latin word count one each)."""
    cjk = len(_CJK.findall(text))
    latin = len(_LATIN_WORD.findall(text))
    return cjk / (cjk + latin) if cjk + latin else 0.0


def budget_for(target_minutes: int) -> int:
    return round(target_minutes * 60 * UNITS_PER_SECOND)


def allocate(total: int, weights: Sequence[float]) -> list[int]:
    """Split ``total`` in proportion to ``weights``, summing exactly to ``total``.

    Largest-remainder rounding, so the parts add up without drift. Zero or
    missing weights fall back to an even split.
    """
    if not weights:
        return []
    clean = [max(float(w or 0), 0.0) for w in weights]
    if sum(clean) <= 0:
        clean = [1.0] * len(clean)
    scale = total / sum(clean)
    raw = [w * scale for w in clean]
    parts = [int(r) for r in raw]
    order = sorted(range(len(raw)), key=lambda i: raw[i] - parts[i], reverse=True)
    for i in order[: total - sum(parts)]:
        parts[i] += 1
    return parts


def status(length: int, budget: int | None) -> str | None:
    if not budget:
        return None
    ratio = length / budget
    if ratio > 1 + TOLERANCE:
        return "OVER"
    if ratio < 1 - TOLERANCE:
        return "UNDER"
    return "OK"


def measure(sections: list[dict], target_minutes: int | None,
            section_budgets: Sequence[int] | None = None) -> dict:
    """The ``length_check`` recorded on a script version.

    Sections share the budget evenly unless ``section_budgets`` (one per
    section, e.g. from a narrative plan) says otherwise.
    """
    per_section = []
    total = 0
    for section in sections:
        length = sum(spoken_units(str(p.get("text") or "")) for p in section.get("paragraphs") or [])
        total += length
        per_section.append({"title": section.get("title", ""), "spoken_length": length})
    budget = budget_for(target_minutes) if target_minutes else None
    if budget:
        weights = list(section_budgets) if section_budgets and len(section_budgets) == len(per_section) \
            else [1] * len(per_section)
        for part, share in zip(per_section, allocate(budget, weights), strict=True):
            part["budget"] = share
    return {
        "spoken_length": total,
        "estimated_seconds": round(total / UNITS_PER_SECOND),
        "units_per_second": UNITS_PER_SECOND,
        "target_minutes": target_minutes,
        "budget": budget,
        "ratio": round(total / budget, 3) if budget else None,
        "status": status(total, budget),
        "tolerance": TOLERANCE,
        "sections": per_section,
    }

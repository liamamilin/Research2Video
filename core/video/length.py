"""Spoken-length budget for a video script.

A script is narrated by AutoVideo, so its length is a duration, not a word
count the model picks. The target duration becomes a budget of *spoken
units* — each Chinese character, Latin word and digit is one unit;
punctuation and spaces are silent — split across sections and measured
afterwards.

Spoken units rather than ``len()``: a Latin word inside a Chinese script
("Claude", "API") takes about the time of one character, not one per
letter. By ``len()`` real narration ran at about 6.6 characters per second.

Duration is speech plus pauses. AutoVideo cuts the script into sentences
(``g2/authored_script.split_script``) and leaves 0.36 s between sentences and
0.85 s between chapters, so a script of short sentences runs longer than one of
long sentences with the same number of units. Two real renders (C4 v10: 1683
units, 92 sentences, 6 chapters, 406.9 s; the crewAI eval script: 1518 units, 63
sentences, 5 chapters, 353.9 s) both gave 4.53-4.61 units per second of speech
once the pauses were taken out, but 4.14 and 4.29 overall. The estimate adds the
pauses; the unit budget uses the overall rate at about 20 units per sentence.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

#: Overall rate, pauses included, at about 20 units per sentence: what turns a target into a unit budget.
UNITS_PER_SECOND = 4.2
#: Speech alone, from the two renders above.
SPEECH_UNITS_PER_SECOND = 4.57
SENTENCE_PAUSE_SECONDS = 0.36
CHAPTER_PAUSE_SECONDS = 0.85
#: AutoVideo splits a longer sentence at a comma.
SEGMENT_MAX_CHARS = 80
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


def segment_count(text: str) -> int:
    """How many pieces AutoVideo voices ``text`` in: one per sentence, and a sentence over 80 characters is cut."""
    count = 0
    for paragraph in re.split(r"[\r\n]+", text):
        for sentence in re.split(r"(?<=[。！？!?；;])", paragraph):
            remaining = sentence.strip()
            while remaining:
                end = min(SEGMENT_MAX_CHARS, len(remaining))
                if len(remaining) > SEGMENT_MAX_CHARS:
                    natural = max(remaining.rfind(mark, 0, SEGMENT_MAX_CHARS) + 1 for mark in "，,、")
                    if natural < SEGMENT_MAX_CHARS // 2:
                        natural = remaining.rfind(" ", 0, SEGMENT_MAX_CHARS) + 1
                    if natural >= SEGMENT_MAX_CHARS // 2:
                        end = natural
                remaining = remaining[end:].strip()
                count += 1
    return count


def estimated_seconds(units: int, segments: int, chapters: int) -> float:
    """Speech time plus the pauses between sentences and between chapters."""
    pauses = SENTENCE_PAUSE_SECONDS * max(segments - chapters, 0) + CHAPTER_PAUSE_SECONDS * max(chapters - 1, 0)
    return units / SPEECH_UNITS_PER_SECOND + pauses


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
    return _status_of(length / budget)


def _status_of(ratio: float) -> str:
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
    segments = 0
    for section in sections:
        texts = [str(p.get("text") or "") for p in section.get("paragraphs") or []]
        length = sum(spoken_units(text) for text in texts)
        total += length
        segments += segment_count("\n".join(texts))
        per_section.append({"title": section.get("title", ""), "spoken_length": length})
    seconds = estimated_seconds(total, segments, len(sections))
    budget = budget_for(target_minutes) if target_minutes else None
    if budget:
        weights = list(section_budgets) if section_budgets and len(section_budgets) == len(per_section) \
            else [1] * len(per_section)
        for part, share in zip(per_section, allocate(budget, weights), strict=True):
            part["budget"] = share
    # The verdict is on the estimated time: units alone miss the pauses of a script of short sentences.
    ratio = seconds / (target_minutes * 60) if target_minutes else None
    return {
        "spoken_length": total,
        "estimated_seconds": round(seconds),
        "segments": segments,
        "units_per_second": UNITS_PER_SECOND,
        "target_minutes": target_minutes,
        "budget": budget,
        "ratio": round(ratio, 3) if ratio else None,
        "status": _status_of(ratio) if ratio else None,
        "tolerance": TOLERANCE,
        "sections": per_section,
    }

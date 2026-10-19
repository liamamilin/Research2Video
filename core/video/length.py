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
#: Speech alone, from the two renders above (the Kokoro voice, which is also the default).
SPEECH_UNITS_PER_SECOND = 4.57
#: Local voices measured on real renders; a voice that is not listed is counted like the default one.
#: ``speech_rate`` is units per second of speech alone (edge silence trimmed). ``content_pauses`` voices rest between
#: pieces by what the pieces say (``content_pause``, mirrored from AutoVideo's g2/pauses.py) and keep
#: ``EDGE_MARGIN_SECONDS`` of silence around each piece; the other voices rest a flat SENTENCE_PAUSE_SECONDS.
#: voxcpm_anchor: C4 v10 rendered 2026-10-18 (1683 units, 92 pieces, 342.8 s of speech, 39.6 s of rests, 390.0 s in all);
#: overhead_per_piece is EDGE_MARGIN_SECONDS plus the average rest per piece (39.6 / 92).
VOICE_PROFILES = {"voxcpm_anchor": {"speech_rate": 4.91, "content_pauses": True, "overhead_per_piece": 0.52}}
EDGE_MARGIN_SECONDS = 0.09
#: What UNITS_PER_SECOND assumes: about this many units per sentence.
UNITS_PER_SENTENCE = 20
SENTENCE_PAUSE_SECONDS = 0.36
CHAPTER_PAUSE_SECONDS = 0.85
#: AutoVideo splits a longer sentence at a comma.
SEGMENT_MAX_CHARS = 80
#: A script more than this far from its budget is flagged, never truncated:
#: cutting narration mid-argument would drop the qualifiers that matter.
#: Was 0.25, under which a six-minute target showed 4.5–7.5 minutes as on
#: target (C4 v6: 437 s, +21%, shown as 达标).
TOLERANCE = 0.15
#: a column may target a short video: 1, 1.5, 2 ... 15 minutes
MIN_MINUTES, MAX_MINUTES = 1, 15

_CJK = re.compile("[" + chr(0x3400) + "-" + chr(0x9FFF) + chr(0xF900) + "-" + chr(0xFAFF) + "]")
_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z0-9'’-]*")
_DIGIT = re.compile(r"\d")


def spoken_units(text: str) -> int:
    """How long ``text`` takes to say, in units."""
    if not text:
        return 0
    return len(_CJK.findall(text)) + len(_LATIN_WORD.findall(text)) + len(_DIGIT.findall(text))


def pieces(text: str) -> list[str]:
    """The pieces AutoVideo voices ``text`` in: one per sentence, and a sentence over 80 characters is cut."""
    out = []
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
                piece, remaining = remaining[:end].strip(), remaining[end:].strip()
                if piece:
                    out.append(piece)
    return out


def segment_count(text: str) -> int:
    """How many pieces AutoVideo voices ``text`` in."""
    return len(pieces(text))


# Rests between pieces for the voices with ``content_pauses``; the same rule and numbers as AutoVideo's
# g2/pauses.py (both test the same GOLDEN table), in seconds.
_PAUSE = {"base": 0.36, "question": 0.16, "semicolon": -0.12, "cut": 0.12, "lead_in": 0.10, "long": 0.08,
          "connective": -0.08, "paragraph": 0.66, "chapter": 0.85, "min": 0.12, "max": 0.70}
_LEAD_IN_UNITS, _LONG_UNITS = 6, 32
_CONNECTIVE = re.compile(r"^(但是|但|不过|可是|所以|因此|因为|而且|并且|而|另外|同时|然后|接着|再|还有|也就是说|比如|例如|就像|如果|要是|否则|只是)")

#: (current, following, boundary, milliseconds): asserted by AutoVideo's tests too
GOLDEN = [
    ("请求本身就是这个顺序：工具定义在前，系统提示在中，消息在后。", "缓存就按这个顺序从开头往后比。", "sentence", 360),
    ("缓存有两个价。", "第一次把前缀存进去，叫缓存写入。", "sentence", 460),
    ("开头不改呢？", "只有第一次 10 美分。", "sentence", 520),
    ("先说钱。", "缓存有两个价。", "sentence", 460),
    ("命中按 0.05 倍读，没命中按 1.25 倍写。", "隔了 25 倍。", "sentence", 360),
    ("不是缓存坏了，是缓存从你改动的那个字节起就对不上，后面的全认不出来，所以每次都要重新写一遍。", "好。", "sentence", 440),
    ("前缀对得上，这次走读取，按 0.05 倍算；", "对不上，这段就得重新写进缓存，按 1.25 倍算。", "sentence", 240),
    ("这笔账我们一步步算。", "但是前缀一变，就全废了。", "sentence", 280),
    ("先说钱。", "再说账。", "sentence", 380),
    ("工具定义在前，系统提示在中，消息在后，缓存按这个顺序从开头往后比，比到哪一段断了，", "从那往后全作废。", "sentence", 120),
    ("这笔账我们一步步算。", "打个比方，就像咖啡店。", "paragraph", 660),
    ("记住这个顺序。", "先做最便宜的：清前缀。", "chapter", 850),
]


def content_pause(current: str, following: str, boundary: str = "sentence") -> float:
    """Seconds of silence after ``current`` before ``following``; ``boundary`` is "sentence", "paragraph" or "chapter"."""
    if boundary == "chapter":
        return _PAUSE["chapter"]
    if boundary == "paragraph":
        return _PAUSE["paragraph"]
    text = current.rstrip()
    end = text[-1:] if text else ""
    if end in "，,、":
        return _PAUSE["cut"]
    gap = _PAUSE["base"]
    if end in "？?":
        gap += _PAUSE["question"]
    elif end in "；;":
        gap += _PAUSE["semicolon"]
    elif spoken_units(text) <= _LEAD_IN_UNITS:
        gap += _PAUSE["lead_in"]
    if spoken_units(text) >= _LONG_UNITS:
        gap += _PAUSE["long"]
    if _CONNECTIVE.match(following.lstrip()):
        gap += _PAUSE["connective"]
    return max(_PAUSE["min"], min(_PAUSE["max"], gap))


def content_pause_seconds(sections: list[dict]) -> tuple[int, float]:
    """(pieces, seconds of rest between them) for a script voiced with content-aware pauses."""
    flat = []
    for si, section in enumerate(sections):
        for pi, paragraph in enumerate(section.get("paragraphs") or []):
            flat.extend((piece, si, pi) for piece in pieces(str(paragraph.get("text") or "")))
    total = 0.0
    for (current, si, pi), (following, sj, pj) in zip(flat, flat[1:]):
        boundary = "chapter" if si != sj else "paragraph" if pi != pj else "sentence"
        total += content_pause(current, following, boundary)
    return len(flat), total


def speech_rate(voice: str | None = None) -> float:
    """Units per second of speech for ``voice`` (the default voice when it is not named or not measured)."""
    return VOICE_PROFILES.get(voice or "", {}).get("speech_rate", SPEECH_UNITS_PER_SECOND)


def units_per_second(voice: str | None = None) -> float:
    """Overall rate, pauses included, at about 20 units per sentence: what turns a target into a unit budget."""
    profile = VOICE_PROFILES.get(voice or "")
    if not profile:
        return UNITS_PER_SECOND
    # the average rest and edge margin per piece, measured on the render the speech rate comes from
    overhead = profile.get("overhead_per_piece", SENTENCE_PAUSE_SECONDS)
    return round(UNITS_PER_SENTENCE / (UNITS_PER_SENTENCE / profile["speech_rate"] + overhead), 2)


def estimated_seconds(units: int, segments: int, chapters: int, voice: str | None = None) -> float:
    """Speech time plus the pauses between sentences and between chapters."""
    pauses = SENTENCE_PAUSE_SECONDS * max(segments - chapters, 0) + CHAPTER_PAUSE_SECONDS * max(chapters - 1, 0)
    return units / speech_rate(voice) + pauses


def chinese_share(text: str) -> float:
    """Share of Chinese among the words of ``text`` (a CJK char and a Latin word count one each)."""
    cjk = len(_CJK.findall(text))
    latin = len(_LATIN_WORD.findall(text))
    return cjk / (cjk + latin) if cjk + latin else 0.0


def budget_for(target_minutes: float, voice: str | None = None) -> int:
    return round(target_minutes * 60 * units_per_second(voice))


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


def measure(sections: list[dict], target_minutes: float | None,
            section_budgets: Sequence[int] | None = None, voice: str | None = None) -> dict:
    """The ``length_check`` recorded on a script version.

    Sections share the budget evenly unless ``section_budgets`` (one per
    section, e.g. from a narrative plan) says otherwise. ``voice`` is the voice
    that will read it: voices speak at different rates.
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
    if VOICE_PROFILES.get(voice or "", {}).get("content_pauses"):
        segments, rests = content_pause_seconds(sections)
        seconds = total / speech_rate(voice) + EDGE_MARGIN_SECONDS * segments + rests
    else:
        seconds = estimated_seconds(total, segments, len(sections), voice)
    budget = budget_for(target_minutes, voice) if target_minutes else None
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
        "units_per_second": units_per_second(voice),
        "voice": voice or None,
        "target_minutes": target_minutes,
        "budget": budget,
        "ratio": round(ratio, 3) if ratio else None,
        "status": _status_of(ratio) if ratio else None,
        "tolerance": TOLERANCE,
        "sections": per_section,
    }

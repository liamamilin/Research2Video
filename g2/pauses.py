"""How long the narration rests between two spoken pieces, from what they say and where they sit.

A person does not leave the same gap after every sentence: a question gets a beat before its answer, a long sentence
a breath, a short lead-in line ("先说钱。") a moment, a "；" or a cut inside a long sentence hardly any, a new paragraph
a clear one and a new chapter the longest. The voice models add edge silence of their own that varies from 0 to 0.4 s
per sentence, so for the local MLX voices the edges are trimmed to a fixed margin (``trim``) and the gaps below are
the whole of the silence between two pieces.

The rule is mirrored in AREC (``core/video/length.py``, ``pause_seconds``) so a script's length estimate counts the
same pauses; the two share ``GOLDEN`` in their tests. Change both together.
"""
import re

BASE_MS = 360
QUESTION_MS = 160       # added after a question: a beat before the answer
SEMICOLON_MS = -120     # added after a "；": the sentence goes on
CUT_MS = 120            # absolute, after a piece cut at a comma inside a long sentence
LEAD_IN_MS = 100        # added after a short line of at most LEAD_IN_UNITS
LONG_MS = 80            # added after a sentence of at least LONG_UNITS: a breath
CONNECTIVE_MS = -80     # added when the next piece goes on with a connective
PARAGRAPH_MS = 660      # absolute, between paragraphs
CHAPTER_MS = 850        # absolute, between chapters
MIN_MS, MAX_MS = 120, 700
LEAD_IN_UNITS, LONG_UNITS = 6, 32
#: silence kept before and after the speech of each piece when the voice's own edges are trimmed
LEAD_MARGIN_MS, TAIL_MARGIN_MS = 30, 60

_CJK = re.compile("[" + chr(0x3400) + "-" + chr(0x9FFF) + chr(0xF900) + "-" + chr(0xFAFF) + "]")
_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9'’-]*")
_DIGIT = re.compile(r"\d")
_CONNECTIVE = re.compile(r"^(但是|但|不过|可是|所以|因此|因为|而且|并且|而|另外|同时|然后|接着|再|还有|也就是说|比如|例如|就像|如果|要是|否则|只是)")


def units(text: str) -> int:
    return len(_CJK.findall(text)) + len(_LATIN.findall(text)) + len(_DIGIT.findall(text))


def gap_ms(current: str, following: str, boundary: str = "sentence") -> int:
    """Silence after ``current`` before ``following``; ``boundary`` is "sentence", "paragraph" or "chapter"."""
    if boundary == "chapter":
        return CHAPTER_MS
    if boundary == "paragraph":
        return PARAGRAPH_MS
    text = current.rstrip()
    end = text[-1:] if text else ""
    if end in "，,、":
        return CUT_MS
    gap = BASE_MS
    if end in "？?":
        gap += QUESTION_MS
    elif end in "；;":
        gap += SEMICOLON_MS
    elif units(text) <= LEAD_IN_UNITS:
        gap += LEAD_IN_MS
    if units(text) >= LONG_UNITS:
        gap += LONG_MS
    if _CONNECTIVE.match(following.lstrip()):
        gap += CONNECTIVE_MS
    return max(MIN_MS, min(MAX_MS, gap))


def trim(samples, sample_rate: int, threshold: float = 0.003):
    """``samples`` without the voice's own silence at either end, keeping LEAD_MARGIN_MS / TAIL_MARGIN_MS of it."""
    import numpy as np
    active = np.flatnonzero(np.abs(samples) > threshold)
    if active.size == 0:
        return samples
    start = max(0, int(active[0]) - round(sample_rate * LEAD_MARGIN_MS / 1000))
    end = min(len(samples), int(active[-1]) + 1 + round(sample_rate * TAIL_MARGIN_MS / 1000))
    return samples[start:end]


#: (current, following, boundary, ms): the same table is asserted in AREC
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

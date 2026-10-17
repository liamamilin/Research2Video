"""Shared by the adaptation steps: model JSON calls, paragraph ids, fact lines."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator

from pydantic import BaseModel, ValidationError

CONFIDENCE = ("已证实", "厂商自述", "有迹象", "推导", "推测", "有冲突", "未核实")
#: A fact this weak cannot carry the hook, and a paragraph using it needs review.
WEAK = ("未核实", "有冲突")
UNVERIFIED = "未核实"
DERIVED = "推导"

#: How each confidence may be said aloud (spec D2). Shared by the draft and review prompts.
WORDING = (
    "已证实 → 直接说；厂商自述 → 「官方说」「按厂商的说法」；有迹象 → 「有案例显示」「目前看」；"
    "推导 → 「算一下」「按这个价格推算」；推测 → 「可能」「我的判断是」，不当结论讲；"
    "有冲突 → 说清用的是哪个口径，或者两个都说；未核实 → 尽量不讲，要讲就明说「还没有一手来源」"
)


class AdaptError(RuntimeError):
    """A step's model output could not be used."""


def parse_json(content: str) -> dict:
    """The JSON object in a model reply, fenced or bare."""
    text = (content or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise AdaptError("模型没有返回 JSON")
        text = text[start:end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AdaptError(f"模型返回的 JSON 无法解析：{exc}") from exc
    if not isinstance(data, dict):
        raise AdaptError("模型返回的 JSON 不是对象")
    return data


def call_json(client, system: str, user: str, model: type[BaseModel], *, temperature: float) -> BaseModel:
    """One call whose reply must validate as ``model``; one retry that names the problem."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    problem = ""
    for _ in range(2):
        response = client.chat(messages, temperature=temperature)
        try:
            return model.model_validate(parse_json(response.content))
        except (AdaptError, ValidationError) as exc:
            problem = str(exc)[:600]
            messages = messages + [
                {"role": "assistant", "content": response.content},
                {"role": "user", "content": f"上面的回复不符合要求：{problem}\n只输出一个符合格式的 JSON 对象。"},
            ]
    raise AdaptError(f"模型两次都没有给出可用的 JSON：{problem}")


# --- Quantities and sentences ------------------------------------------------------

#: Model names and version strings ("Opus 5.5", "GPT-6", "v1.15.27") are names, not quantities.
VERSIONED = re.compile(r"[A-Za-z][A-Za-z0-9._]*(?:[ -]v?)\d+(?:\.\d+)*|\bv\d+(?:\.\d+)+")
_CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_SMALL = {"十": 10, "百": 100, "千": 1000}
_CN_LARGE = {"万": 10_000, "亿": 100_000_000}
#: Arabic numbers, and Chinese numerals big enough to be a quantity ("两万", "一百"): small ones
#: ("一个", "三件事", "十分重要") are words, not data.
_QUANTITY = re.compile(
    r"(\$)?\s*(\d[\d,]*(?:\.\d+)?|[零〇一二两三四五六七八九十百千万亿]*[百千万亿][零〇一二两三四五六七八九十百千万亿]*)"
    r"\s*(×|%|[A-Za-z]+|[\u4e00-\u9fff]{1,2})?"
)
_UNIT_KIND = (
    (("次",), "count"), (("倍", "×", "x", "X"), "times"), (("美元", "美金", "刀", "元"), "money"),
    (("%",), "percent"), (("token", "Token", "tokens", "MTok", "Tok"), "tokens"), (("毫秒", "ms"), "ms"),
    (("秒",), "s"), (("分钟",), "min"), (("小时",), "h"), (("天",), "day"),
)
_TO_SECONDS = {"ms": 0.001, "s": 1, "min": 60, "h": 3600, "day": 86400}
_SENTENCE = re.compile(r"[^。！？；!?;\n]+[。！？；!?;]?")


def chinese_number(text: str) -> float | None:
    """"两万" → 20000, "一百二十" → 120, "三千五百" → 3500; None if it is not a plain numeral."""
    total, section, digit = 0, 0, None
    for ch in text:
        if ch in _CN_DIGIT:
            digit = _CN_DIGIT[ch]
        elif ch in _CN_SMALL:
            section += (1 if digit is None else digit) * _CN_SMALL[ch]
            digit = None
        elif ch in _CN_LARGE:
            total += (section + (digit or 0)) * _CN_LARGE[ch]
            section, digit = 0, None
        else:
            return None
    return float(total + section + (digit or 0))


def quantities(text: str) -> list[tuple[float, str | None, str]]:
    """(value, unit kind, as written) for each quantity in ``text``; names and versions skipped."""
    out = []
    for match in _QUANTITY.finditer(VERSIONED.sub(" ", text or "")):
        dollar, raw, unit = match.groups()
        value = chinese_number(raw) if not raw[0].isdigit() else float(raw.replace(",", ""))
        if value is None:
            continue
        prefix = next((p for prefixes, _ in _UNIT_KIND for p in prefixes if unit and unit.startswith(p)), None)
        kind = "money" if dollar else next((k for prefixes, k in _UNIT_KIND if prefix in prefixes), None)
        written = f"{'$' if dollar else ''}{raw}{' ' if prefix and prefix.isascii() and prefix.isalpha() else ''}{prefix or ''}"
        if kind in _TO_SECONDS:  # 1200 毫秒 and 1.2 秒 are the same quantity
            value, kind = value * _TO_SECONDS[kind], "s"
        out.append((value, kind, written))
    return out


def _close(a: float, b: float, tolerance: float) -> bool:
    return a == b or (b != 0 and abs(a - b) / abs(b) <= tolerance)


def untraced(text: str, sources: str, tolerance: float = 0.10) -> list[str]:
    """Quantities in ``text`` that ``sources`` (the facts a paragraph names) do not contain.

    A value within ``tolerance`` counts as the same; a percentage may also match
    its fraction or complement ("省 95%" ↔ "0.05×"). A value found only with a
    different unit kind ("两万次" ↔ "20,000 token") is reported too.
    """
    known = quantities(sources)
    problems = []
    for value, kind, written in quantities(text):
        same = [k for v, k, _ in known if _close(value, v, tolerance)]
        converted = kind == "percent" and any(
            _close(c, v, tolerance) for v, _, _ in known for c in (value / 100, 1 - value / 100))
        if not same and not converted:
            problems.append(f"「{written}」在标注的事实里找不到")
        elif same and not converted and kind and all(k and k != kind for k in same):
            problems.append(f"「{written}」在标注的事实里单位不同")
    return problems


def sentence_spans(text: str) -> list[tuple[int, int]]:
    return [m.span() for m in _SENTENCE.finditer(text or "") if m.group(0).strip()]


def _squash(text: str) -> str:
    return re.sub(r"[\s，。！？；、：「」“”\"'（）()]", "", text or "")


def remove_sentence(text: str, quote: str) -> str | None:
    """``text`` without the sentence(s) holding ``quote``; None if the quote is not really there."""
    quote = (quote or "").strip().strip("「」“”\"'")
    if not quote:
        return None
    start = text.find(quote)
    if start >= 0:
        end = start + len(quote)
    else:  # the model may have dropped punctuation or spaces; match sentence by sentence
        squashed = _squash(quote)
        if len(squashed) < 6:
            return None
        hit = next(((a, b) for a, b in sentence_spans(text) if squashed in _squash(text[a:b])), None)
        if hit is None:
            return None
        start, end = hit
    keep = [(a, b) for a, b in sentence_spans(text) if b <= start or a >= end]
    return "".join(text[a:b] for a, b in keep).strip()


_FACT_ID = re.compile(r"F\d+")


def fact_ids(values) -> list[str]:
    """Fact ids as the model wrote them, reduced to the id: "F14：缓存……" → "F14". Order kept, duplicates dropped."""
    out: list[str] = []
    for value in values or []:
        match = _FACT_ID.match(str(value).strip())
        key = match.group(0) if match else str(value).strip()
        if key and key not in out:
            out.append(key)
    return out


def pid(section_index: int, paragraph_index: int) -> str:
    """A paragraph's id in prompts and checks: S1P1 is the first paragraph of the first section."""
    return f"S{section_index + 1}P{paragraph_index + 1}"


def paragraphs(sections: list[dict]) -> Iterator[tuple[int, int, dict]]:
    for si, section in enumerate(sections):
        for pi, para in enumerate(section.get("paragraphs") or []):
            yield si, pi, para


def numbered_script(sections: list[dict]) -> str:
    """The script as a model reads it: section titles and numbered paragraphs."""
    lines = []
    for si, section in enumerate(sections):
        lines.append(f"## {section.get('title', '')}")
        for pi, para in enumerate(section.get("paragraphs") or []):
            lines.append(f"[{pid(si, pi)}] {para.get('text', '')}")
    return "\n".join(lines)


def concept_names(term: str) -> list[str]:
    """The ways a sheet concept is said: "缓存断点（cache_control breakpoint）" → both halves."""
    match = re.match(r"^(.*?)\s*[（(](.*?)[）)]\s*$", term or "")
    names = [match.group(1), match.group(2)] if match else [term or ""]
    return [n.strip() for n in names if len(n.strip()) >= 2]


def covered(name: str, plan: dict) -> bool:
    """A concept the viewer will have: explained in the plan's glossary, or already known."""
    needle = name.strip().lower()
    if not needle:
        return True
    known = [g.get("term", "") for g in plan.get("glossary") or []] + list((plan.get("audience") or {}).get("knows") or [])
    return any(needle in k.lower() or k.lower() in needle for k in known if k.strip())


def fact_line(fact: dict) -> str:
    """One fact for a prompt: id, confidence, claim, caveat, formula. Sources stay out."""
    line = f"{fact['id']} [{fact['confidence']}] {fact['claim']}"
    if fact.get("caveat"):
        line += f"（口径：{fact['caveat']}）"
    if fact.get("formula"):
        line += f"（推导：{fact['formula']}）"
    return line


def facts_by_id(sheet: dict) -> dict[str, dict]:
    return {f["id"]: f for f in sheet.get("facts") or []}

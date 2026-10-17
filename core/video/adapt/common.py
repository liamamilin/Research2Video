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

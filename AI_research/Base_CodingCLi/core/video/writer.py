"""Generate a script directly from source material and its audience.

Only the production goal and the JSON transport shape guide the author.
Timing and source metadata are recorded without triggering editorial rewrites.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Optional

from pydantic import BaseModel, Field, ValidationError

from core.provenance import extract_urls, normalize_url

from . import length
from .audience_copy import findings as audience_findings

MIN_CHINESE_SHARE = 0.5


class ScriptError(RuntimeError):
    """The model's script could not be used."""


class _Paragraph(BaseModel):
    text: str = Field(min_length=1)
    citations: list[str] = Field(default_factory=list)
    needs_review: bool = False
    review_reason: str = ""


class _Section(BaseModel):
    title: str = Field(min_length=1)
    paragraphs: list[_Paragraph] = Field(min_length=1)


class _Script(BaseModel):
    title: str = Field(min_length=1)
    sections: list[_Section] = Field(min_length=1)


PROMPT_VERSION = 3
GOAL = "根据素材，产出目标观众愿意看、听得懂、想继续看的视频。"
SYSTEM_PROMPT = GOAL + """
当前步骤：你是本期讲述者，现在直接对目标观众讲述，正文就是观众将听到的口播。
找到这次讲述最值得听的东西，让标题说出本期最有意思的具体事情，让开场引起兴趣，让观众随着讲述理解它，并愿意继续听。
接口输出 JSON：
{"title":"视频标题","sections":[{"title":"小节标题","paragraphs":[{"text":"口播正文","citations":["素材链接"]}]}]}"""


@dataclass
class WrittenScript:
    title: str
    sections: list[dict]
    length_check: dict
    meta: dict = field(default_factory=dict)


def trusted_urls(report: str, citation_check: Optional[dict]) -> set[str]:
    """Normalized report URLs that the run's citation check did not flag."""
    cited = {normalize_url(u) for u in extract_urls(report_body(report))}
    unmatched = {normalize_url(u) for u in (citation_check or {}).get("unmatched_examples") or []}
    return cited - unmatched


def report_body(report: str) -> str:
    """Use the saved report body without a provider's complete leading think block.

    Keep the source file intact; incomplete blocks and tags inside the actual
    report are preserved. The final report and its evidence are never truncated.
    """
    body = re.sub(r"^\s*<think>.*?</think>\s*", "", report, count=1, flags=re.S | re.I)
    return body if body.strip() else report


#: The topic fields shown to the model when the recipe names none.
TOPIC_FIELDS = ("核心角度", "目标受众", "条目数", "条目", "对比对象", "对比维度")


def _request(report: str, topic: dict, column_style: str, target_minutes: float, budget: int,
             paragraph_units: Optional[tuple] = None, topic_fields: Optional[tuple] = None) -> str:
    report = report_body(report)
    fields = topic.get("fields") or {}
    names = set(topic_fields or TOPIC_FIELDS) - {"核心角度", "建议大纲", "评分", "条目数"}
    selected = "\n".join(f"- {name}：{value}" for name, value in fields.items() if name in names and name != "目标受众" and value)
    direction = f"## 这次想做的视频\n{column_style.strip()}\n\n" if column_style.strip() else ""
    if topic.get("story"):
        from core.video.story_pool import brief

        direction += "## 已选主事件\n" + brief(topic["story"]) + "\n\n"
    return (
        f"## 目标观众\n{fields.get('目标受众') or '普通观众'}\n\n"
        f"{direction}"
        f"## 选题\n{topic.get('title', '')}\n\n"
        f"## 选题资料\n{selected}\n\n## 素材\n{report}"
    )


def _parse(content: str) -> _Script:
    text = content.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ScriptError("模型没有返回 JSON 文稿")
        text = text[start:end + 1]
    try:
        return _Script.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ScriptError(f"文稿 JSON 不符合格式：{exc}") from exc


def _finalize(script: _Script, allowed: set[str], target_minutes: float, voice: Optional[str] = None) -> tuple[list[dict], dict]:
    sections: list[dict] = []
    dropped = 0
    for section in script.sections:
        paragraphs = []
        for para in section.paragraphs:
            text = para.text.strip()
            kept = [u for u in dict.fromkeys(para.citations) if normalize_url(u) in allowed]
            dropped_here = len(set(para.citations)) - len(kept)
            dropped += dropped_here
            if para.needs_review or dropped_here:
                evidence = "NEEDS_REVIEW"
            elif kept:
                evidence = "CITED"
            else:
                evidence = "UNCITED"
            note = None
            if dropped_here:
                note = f"去掉了 {dropped_here} 个不在报告可信来源里的引用"
            paragraphs.append({"text": text, "citations": kept, "evidence": evidence, "note": note})
            if evidence == "NEEDS_REVIEW":
                paragraphs[-1]["review_reason"] = para.review_reason.strip() or note or "撰稿时对本段事实把握不足，尚需带出处的明确核验"
        sections.append({"title": section.title.strip(), "paragraphs": paragraphs})
    check = length.measure(sections, target_minutes, voice=voice)
    check["dropped_citations"] = dropped
    return sections, check


_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _canonical(raw: str) -> str:
    """A number as digits only: 1,500 -> 1500, 0.20 -> 0.2, so $0.20 in a report matches 0.2 in the script."""
    text = raw.replace(",", "").rstrip(".")
    if "." in text:
        text = text.rstrip("0").rstrip(".") or "0"
    return text


def _numbers(text: str) -> set[str]:
    return {_canonical(m.group(0)) for m in _NUMBER.finditer(text)}


def ungrounded_numbers(sections: list[dict], report: str) -> list[str]:
    """Numbers the script says that the research report does not contain, in the order they are said."""
    known = _numbers(report)
    found: list[str] = []
    for section in sections:
        for para in section["paragraphs"]:
            for match in _NUMBER.finditer(para["text"]):
                number = _canonical(match.group(0))
                if number not in known and number not in found:
                    found.append(number)
    return found


def lint(sections: list[dict], report: Optional[str] = None) -> list[str]:
    """Trace concrete research leaks, unsupported numbers and the voice language requirement."""
    problems = []
    spoken = "".join(p["text"] for s in sections for p in s["paragraphs"])
    if report is not None:
        missing = ungrounded_numbers(sections, report)
        if missing:
            shown = "、".join(missing[:6]) + ("等" if len(missing) > 6 else "")
            problems.append(f"文稿里的数字 {shown} 在研究报告里找不到，请对照来源核实")
    problems.extend(f"{item['problem']}：「{item['quote']}」" for item in audience_findings(spoken))
    if length.chinese_share(spoken) < MIN_CHINESE_SHARE:
        problems.append("文稿主要不是中文，AutoVideo 只能制作中文视频")
    return problems


def _require_chinese(sections: list[dict]) -> None:
    spoken = "".join(p["text"] for s in sections for p in s["paragraphs"])
    if length.chinese_share(spoken) < MIN_CHINESE_SHARE:
        raise ScriptError("文稿主要不是中文，AutoVideo 只能制作中文视频")


def write(
    *,
    report: str,
    citation_check: Optional[dict],
    topic: dict,
    column_style: str,
    target_minutes: float,
    llm_config,
    cancel_token: Optional[threading.Event] = None,
    voice: Optional[str] = None,
    paragraph_units: Optional[tuple] = None,
    topic_fields: Optional[tuple] = None,
    source_sheet: Optional[dict] = None,
    on_sheet: Optional[Callable[[dict], None]] = None,
) -> WrittenScript:
    """Write a complete first draft; validate only its transport shape."""
    from core.llm import LLMClient
    client = LLMClient(llm_config, cancel_token=cancel_token)
    report = report_body(report)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _request(report, topic, column_style, target_minutes, 0,
                                              topic_fields=topic_fields)},
    ]
    response = client.chat(messages, temperature=0.4)
    attempts = 1
    try:
        script = _parse(response.content)
    except ScriptError:
        # A reply cut off mid-JSON (seen with the free models) is worth one more try before failing the script.
        attempts += 1
        response = client.chat(messages, temperature=0.4)
        script = _parse(response.content)
    allowed = trusted_urls(report, citation_check)
    sections, check = _finalize(script, allowed, target_minutes, voice)
    return WrittenScript(
        title=script.title.strip(),
        sections=sections,
        length_check=check,
        meta={"attempts": attempts, "usage": dict(client.usage), "problems": [],
              "generation": "goal_first", "prompt_version": PROMPT_VERSION,
              "creative_direction": column_style.strip(),
              "trusted_urls": len(allowed)},
    )

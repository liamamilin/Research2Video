"""Generate a script directly from source material and its audience.

Only the production goal and the JSON transport shape guide the author.
Timing and source metadata are recorded without triggering editorial rewrites.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

from core import report_material, telemetry
from core.errors import CancelledError
from core.provenance import extract_urls, normalize_url

from . import author_input_gates, length, persona as narrator
from .audience_copy import findings as audience_findings
from .narration_stance import LISTENING_DIRECTION, note, settle_sections

MIN_CHINESE_SHARE = 0.5


class ScriptError(RuntimeError):
    """The model's script could not be used."""


class _Paragraph(BaseModel):
    text: str = Field(min_length=1)
    citations: list[str] = Field(default_factory=list)
    needs_review: bool = False
    review_reason: str = ""

    @field_validator("citations", mode="before")
    @classmethod
    def _citation_urls(cls, values: Any) -> Any:
        # Some providers include a source title with its URL. Keep the same
        # URL-only contract and let the existing source filter check it.
        if isinstance(values, list):
            return [item["url"] if isinstance(item, dict) and isinstance(item.get("url"), str) else item
                    for item in values]
        return values


class _Section(BaseModel):
    title: str = Field(min_length=1)
    paragraphs: list[_Paragraph] = Field(min_length=1)
    episode_role: Literal["opening", "item", "closing"] | None = None


class _Script(BaseModel):
    title: str = Field(min_length=1)
    sections: list[_Section] = Field(min_length=1)


class _EpisodeEntrance(BaseModel):
    opening: list[_Paragraph] = Field(min_length=1)
    entry_start: int = Field(ge=0)


PROMPT_VERSION = 28
# The material decides what is worth naming.
TITLE_RULE = "标题点出本期最有意思的具体事，核心看点前置，尽量控制在 18 到 28 字左右，数字有助于理解看点时再用。"
#: The first seconds decide whether a viewer stays (11-10 benchmark): small accounts' hits open on the hook itself.
OPENING_RULE = '从本期最值得理解的内容进入，第一句短促抛出反差、变化或核心结论（尽量控制在 25 到 35 字以内），细节和背景放到下一句再展开；切入点由材料和目标观众决定。'
ENDING_RULE = "结尾留一个值得带走的判断、发现或下一步。有值得聊的实际体验，再问观众。"
#: How the two most-watched videos talk (2026-11-02 review): short lines, a stated judgement, few numbers said aloud.
STYLE_RULE = "像对一个懂行的朋友讲，多用短句。说数字时讲出实际含义，陌生术语放在情境里解释。"
#: Narration is what viewers hear; a note to the picture ("画面里可以……") was read out and subtitled (11-10 research,
#: spec/bilibili-growth-research-20261110/SAMPLE-O03.md). The program also rewrites what slips through (``spoken_only``).
#: An item's 核心角度 names what it is about; the 11-10 trial narrowed to three dimensions and dropped the one point
#: its angle named (ChatGPT's barrier for Chinese users).
ANGLE_RULE = "讲清选题答应观众要解决的问题。"
CONCRETE_RULE = ("用材料中的人物、动作、原话或可见结果把事讲具体。"
                 "抽象的地方可以用日常场景讲明白。")
SCOPE_CONCRETE_RULE = (CONCRETE_RULE + "标题、开场和判断面向选题的目标受众，用例子解释他们关心的问题。"
                       "若选题本来只面向专业人群，就把适用对象说清。")


PICTURE_RULE = "口播只写观众听到的话，分镜和屏幕说明留给制作。"
GOAL = "根据素材，产出目标观众愿意看、听得懂、想继续看的视频。"
AUTHORING_PROMPT = "\n".join((GOAL, LISTENING_DIRECTION, "沿用上游的事实、数字和计算。",
                              STYLE_RULE, PICTURE_RULE, ANGLE_RULE, CONCRETE_RULE))
SYSTEM_PROMPT = "\n".join((AUTHORING_PROMPT, OPENING_RULE, TITLE_RULE, ENDING_RULE)) + """
接口输出 JSON：
{"title":"视频标题","sections":[{"title":"小节标题","paragraphs":[{"text":"口播正文","citations":["素材链接"]}]}]}"""
EPISODE_LEAD = "整期可以从最值得听的具体内容进入，需要帮助观众建立方向时再简短预告。"
EPISODE_PROMPT = AUTHORING_PROMPT + """
这期有多个条目，各自讲清看点和必要细节；同一问题可按解释层次展开。条目之间有关系就说出关系，也可以直接切换。
""" + EPISODE_LEAD + "\n" + ENDING_RULE + "\n" + TITLE_RULE + """
正文每个条目用一个 item section。独立开场和结尾分别用 opening 和 closing。
引用了具体事实、数字或原话的段落填写对应素材链接，纯过渡段可留空数组。
输出 JSON：{"title":"本期标题","sections":[{"title":"短标题","episode_role":"item","paragraphs":[{"text":"口播正文","citations":["素材链接"]}]}]}"""

EXPLAINER_EPISODE_PROMPT = AUTHORING_PROMPT + """
这期帮助观众理解研究底稿中的科学问题。可以从具体现象建立疑问，用实验、观察、模型或不同尺度的例子解释因果。建议保留研究者做了什么、看到了什么，以及这些材料怎样支持解释。相关背景与例子按理解需要展开。
""" + EPISODE_LEAD + """
解释章节用 section 组织，标题体现具体内容；讲述顺序与收束方式可随本题的推理安排。
""" + ENDING_RULE + """
正文章节用 item，有独立开场或收尾时分别用 opening 或 closing。
引用了具体事实、数字或原话的段落填写对应素材链接，纯过渡段可留空数组。
输出 JSON：{"title":"本期标题","sections":[{"title":"短标题","episode_role":"item","paragraphs":[{"text":"口播正文","citations":["素材链接"]}]}]}
"""

BUSINESS_EPISODE_PROMPT = AUTHORING_PROMPT + """
这期帮助观众理解一门生意怎样运转。具体交易可用来说明客户需求、付款、交付和协作；成本、收益、风险及参与者关系值得展开。可以按实际材料选择业务过程、对比或其它清楚的讲法。
""" + EPISODE_LEAD + """
业务章节用 section 组织，标题体现具体内容；收束可落在本题有依据的经营认识上。
""" + ENDING_RULE + """
正文章节用 item，有独立开场或收尾时分别用 opening 或 closing。
引用了具体事实、数字或原话的段落填写对应素材链接，纯过渡段可留空数组。
输出 JSON：{"title":"本期标题","sections":[{"title":"短标题","episode_role":"item","paragraphs":[{"text":"口播正文","citations":["素材链接"]}]}]}
"""

ARTWORK_EPISODE_PROMPT = AUTHORING_PROMPT + """
这期帮助观众读懂作品的表达。构图、材质、声音、创作动作和观看感受可以成为理解的入口；历史背景或相邻作品有助于解释时也可展开。建议让评论与可以辨认的细节相连。
""" + EPISODE_LEAD + """
讲述章节用 section 组织，标题体现具体作品或值得理解的内容；切入和收束可由材料决定。
""" + ENDING_RULE + """
正文章节用 item，有独立开场或收尾时分别用 opening 或 closing。
引用了具体事实、数字或原话的段落填写对应素材链接，纯过渡段可留空数组。
输出 JSON：{"title":"本期标题","sections":[{"title":"短标题","episode_role":"item","paragraphs":[{"text":"口播正文","citations":["素材链接"]}]}]}
"""

CULTURE_CHANGE_EPISODE_PROMPT = AUTHORING_PROMPT + """
这期围绕选定的文化问题解释它的来路。代表作品、创作者行动、展示或传播处境可以帮助观众理解现象怎样形成或改变；相关作品之间的联系与差别按本题需要展开。建议把文化认识落在具体细节与背景上。
""" + EPISODE_LEAD + """
讲述章节用 section 组织，标题体现具体内容；可以按本题的因果、时间或作品关系展开。
""" + ENDING_RULE + """
正文章节用 item，有独立开场或收尾时分别用 opening 或 closing。
引用了具体事实、数字或原话的段落填写对应素材链接，纯过渡段可留空数组。
输出 JSON：{"title":"本期标题","sections":[{"title":"短标题","episode_role":"item","paragraphs":[{"text":"口播正文","citations":["素材链接"]}]}]}
"""

MANUFACTURING_EPISODE_PROMPT = AUTHORING_PROMPT + """
这期帮助观众理解产品或部件怎样制造和交付。可以沿实际工序解释工具的作用、材料或结构的变化以及前后依赖；规格、伙伴、对照工艺与交付条件有助于理解时展开。建议保留能说明制造难点的具体过程与结果。
""" + EPISODE_LEAD + """
制造章节用 section 组织，标题体现具体内容；可以按理解需要选择切入点和收束。
""" + ENDING_RULE + """
正文章节用 item，有独立开场或收尾时分别用 opening 或 closing。
引用了具体事实、数字或原话的段落填写对应素材链接，纯过渡段可留空数组。
输出 JSON：{"title":"本期标题","sections":[{"title":"短标题","episode_role":"item","paragraphs":[{"text":"口播正文","citations":["素材链接"]}]}]}
"""

TUTORIAL_PROMPT = AUTHORING_PROMPT + """
你是工具教程的讲述者，沿同一份输入讲到实际输出。
把准备输入、执行操作、查看结果等可跟随的阶段分别写成 section，让视频直接用这些章节做步骤转场。
每个阶段有清楚的短标题与必要的 paragraphs，沿用材料已有的实际命令、截图和结果。
输入展示与操作代码各在对应阶段展开，结果阶段可以对照同一份输入和输出。
旁白用自然中文讲清截图里看得到的内容；命令可以保留原文，图片中的 Markdown 标题、列表和表格用口语解释。
不要把代码围栏、整块 Markdown 原文或表格分隔线写成口播正文，这些内容由实际截图呈现。
可以用实际输入、操作或可见结果让观众理解教程任务，开场和收束随本次材料安排。
episode_role 使用 opening、item、closing。保留实际步骤顺序，不把所有步骤塞进同一个项目章节。
引用了具体事实、数字或原话的段落填写对应素材链接，纯过渡段可留空数组。
输出 JSON：{"title":"本期标题","sections":[{"title":"短标题","episode_role":"item","paragraphs":[{"text":"口播正文","citations":["素材链接"]}]}]}"""


#: A series item that has a next one (selection.next_episode): the last words before the farewell promise it.
NEXT_EPISODE_PROMPT = """
这是一个连续系列，下一期已经排好，讲的是「{next}」。在结尾简短预告已排好的下一期，接法和措辞随讲述自然安排。"""
GREETING_PROMPT = """
口播开头由节目固定念出问候「{greeting}」，正文从这句问候之后接着讲，不再另外问候或介绍栏目；最后用一句简短的道别结束。"""
#: Opening and closing sections of a programme carry these titles, so the screen never shows a working label.
OPENING_TITLE, CLOSING_TITLE = "本期导读", "本期回顾"
#: Authors sometimes label the first/last beat as "开场：…" / "结尾"; navigation should not show that.
_WORKING_TITLE = re.compile(r"^(开场|结尾)(?:[：:]\s*(.+))?$")


def viewer_section_title(title: str) -> str:
    """Drop working open/close labels from a section title shown to viewers."""
    text = (title or "").strip()
    match = _WORKING_TITLE.match(text)
    if not match:
        return text
    labeled = (match.group(2) or "").strip()
    if labeled:
        return labeled
    return OPENING_TITLE if match.group(1) == "开场" else CLOSING_TITLE


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
             paragraph_units: Optional[tuple] = None, topic_fields: Optional[tuple] = None,
             original_material: Optional[str] = None) -> str:
    report = report_material.for_narration(report_body(report), research_report=original_material is not None)
    if original_material:
        original_material = report_material.for_narration(original_material)
    fields = topic.get("fields") or {}
    names = set(topic_fields or TOPIC_FIELDS) - {"核心角度", "建议大纲", "评分", "条目数"}
    selected = "\n".join(f"- {name}：{value}" for name, value in fields.items() if name in names and name != "目标受众" and value)
    direction = f"## 这次想做的视频\n{column_style.strip()}\n\n" if column_style.strip() else ""
    if topic.get("story"):
        from core.video.story_pool import brief

        direction += "## 已选主事件\n" + brief(topic["story"]) + "\n\n"
    material = f"## 素材\n{report}"
    if original_material is not None:
        programme = topic.get("content_mode") == "episode"
        pacing = "整期篇幅由内容决定。" if programme else f"配方参考片长约 {target_minutes:g} 分钟。"
        material = f"## 讲述节奏\n{pacing}\n\n## 本期研究底稿\n{report}"
        if original_material:
            material += f"\n\n## 对应原始材料\n{original_material}"
    return (
        f"## 目标观众\n{fields.get('目标受众') or '普通观众'}\n\n"
        f"{direction}"
        f"## 选题\n{topic.get('title', '')}\n\n"
        f"## 选题资料\n{selected}\n\n{material}"
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


#: Only the salutation or the welcome clause, so "大家好，今天讲三件事……" keeps its overview.
_OWN_GREETING = re.compile(r"^(?:(?:大家好|各位好|哈喽|嗨|你好)[，,！!。\s]*|各位(?:观众|朋友们?)?[，,！!。]\s*|欢迎(?:收看|来到|回到)[^，,。！？!]*[，,。！？!]\s*)")


def without_greeting(text: str) -> str:
    """``text`` without a salutation or welcome clause at its start ("大家好，欢迎收看今日快讯。今天……" → "今天……")."""
    previous = None
    while previous != text:
        previous, text = text, _OWN_GREETING.sub("", text, count=1).lstrip()
    return text


def _drop_own_greeting(opening: list) -> None:
    """Take a salutation or welcome clause off the first paragraphs, dropping any left empty."""
    while opening and _OWN_GREETING.match(opening[0].text.strip()):
        opening[0].text = _OWN_GREETING.sub("", opening[0].text.strip(), count=1)
        if not opening[0].text:
            opening.pop(0)


def _programme_frame(script: _Script, greeting: Optional[str], programme: bool) -> _Script:
    """Put the fixed greeting first (or take the author's off when there is none) and give a programme's opening and closing their viewer-facing titles."""
    sections = [section.model_copy(deep=True) for section in script.sections]
    if programme:
        for section in sections:
            if section.episode_role == "opening":
                section.title = OPENING_TITLE
            elif section.episode_role == "closing":
                section.title = CLOSING_TITLE
    if not greeting:
        # No fixed line: the video opens on the hook, so a greeting the author wrote anyway is taken off.
        _drop_own_greeting(sections[0].paragraphs)
    elif not sections[0].paragraphs[0].text.strip().startswith(greeting):
        # A greeting the author worded anyway would be heard twice; the column's own line replaces it.
        opening = sections[0].paragraphs
        _drop_own_greeting(opening)
        opening.insert(0, _Paragraph(text=greeting))
    return script.model_copy(update={"sections": sections})


#: A note to the picture spoken as narration, in the two forms seen: "画面里可以是X" (an image to imagine) and
#: "画面还是X" (the picture returning). Other forms are only reported (``lint``): rewriting them blindly could break a
#: sentence about a product's own picture or camera.
_PICTURE_IMAGINE = re.compile(r"(?:(?<=^)|(?<=[。！？!?；;，,]))\s*画面(?:里|上|中)?(?:可以|会|能)?是")
_PICTURE_AGAIN = re.compile(r"(?:(?<=^)|(?<=[。！？!?；;，,]))\s*画面(?:里|上|中)?(还是|又回到|回到)")
_PICTURE_NOTE = re.compile(r"(?:(?<=^)|(?<=[。！？!?；;]))\s*(?:这里|此处|这时)?画面(?:里|上|中)?(?:可以|应该|要|会|能|切到|换成|出现|显示|放|给)")


def spoken_only(text: str) -> str:
    """``text`` without a note to the picture: "画面里可以是一个人倒水" → "想象一个人倒水", "画面还是那杯水" → "还是那杯水"."""
    text = _PICTURE_IMAGINE.sub("想象", text)
    return _PICTURE_AGAIN.sub(lambda m: m.group(1), text)


def _finalize(script: _Script, allowed: set[str], target_minutes: float, voice: Optional[str] = None) -> tuple[list[dict], dict]:
    sections: list[dict] = []
    dropped = 0
    for section in script.sections:
        paragraphs = []
        for para in section.paragraphs:
            text = spoken_only(para.text.strip())
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
        title = section.title.strip()
        if section.episode_role not in {"opening", "closing"}:
            title = viewer_section_title(title)
        sections.append({"title": title, "paragraphs": paragraphs,
                         **({"episode_role": section.episode_role} if section.episode_role else {})})
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
    note = _PICTURE_NOTE.search(spoken)
    if note:
        start = max(0, note.start() - 12)
        problems.append(f"口播里有给画面的说明，观众会听到：「{spoken[start:note.end() + 12]}」")
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


def tutorial_context(content_context: dict) -> bool:
    return (content_context.get('type') == 'tutorial'
            or (content_context.get('render') or {}).get('production_recipe') == 'mixed_tutorial')


def write_for_column(*, column: Any, content_context: dict, column_style: str | None = None, **kwargs: Any) -> WrittenScript:
    """Apply the same column programme settings to report and supplied-material authors."""
    from core.video import episodes

    from core.video import angles

    topic = kwargs['topic']
    tutorial = tutorial_context(content_context)
    return write(**kwargs,
                 column_style=(column.script_style if column_style is None else column_style)
                 + episodes.brief(topic, tutorial=tutorial),
                 # an extra angle on a hot item (core/video/angles.py) is told at its own, longer length
                 target_minutes=angles.minutes(topic.get("angle") or "") or column.target_minutes, voice=column.voice_id or None,
                 paragraph_units=tuple(column.paragraph_units), topic_fields=column.writer_topic_fields(),
                 greeting=episodes.greeting(topic, content_context) or None, tutorial=tutorial)


def introduce_opening(script: _Script, persona: dict, client: Any, *, greeting: Optional[str] = None) -> tuple[_Script, dict]:
    """Repair a repeated identity once without imposing an opening structure.

    The same author sees the real opening and the next section. Only the opening
    section can change; the rest of the script and its metadata stay intact.
    Failure leaves the original available and records the unsuccessful attempt.
    """
    from core.video import persona as narrator
    if not narrator.opening_needs_introduction(script.sections[0].paragraphs, persona, fixed_greeting=greeting or ""):
        return script, {"status": "authored", "requests": 0}
    instruction = (
        "开场重复介绍了讲述者身份，保留其中一次，其余位置自然接回正文。保留已有事实、人物引语和引用链接。"
        "不要删掉讲述者身份，不添加材料没有的事实或亲身经历。这里只改开场，不添加道别。仅返回首节，格式为"
        '{"title":"原首节标题","paragraphs":[{"text":"口播","citations":[]}]}。'
        + narrator.prompt(persona, opening_only=True)
    )
    if greeting:
        instruction += f"节目会在正文前统一念出固定问候「{greeting}」，这里不另加问候。"
    request = {"title": script.title, "opening": script.sections[0].model_dump(),
               "following": script.sections[1].model_dump() if len(script.sections) > 1 else None}
    try:
        response = client.chat([{"role": "system", "content": instruction},
                                {"role": "user", "content": json.dumps(request, ensure_ascii=False)}], temperature=0.4)
        candidate = _parse('{"title":"opening","sections":[' + response.content.strip().removeprefix("```json").removesuffix("```").strip() + ']}').sections[0]
        if narrator.opening_needs_introduction(candidate.paragraphs, persona, fixed_greeting=greeting or ""):
            return script, {"status": "unchanged", "requests": 1, "reason": "opening_repeats_identity",
                            "candidate": candidate.model_dump()}
        opening = script.sections[0].model_copy(update={"paragraphs": candidate.paragraphs})
        return script.model_copy(update={"sections": [opening, *script.sections[1:]]}), {"status": "rewritten", "requests": 1}
    except CancelledError:
        raise
    except Exception as exc:  # Keep the authored draft, including when the provider is unavailable.
        return script, {"status": "unchanged", "requests": 1, "reason": type(exc).__name__}


def author_episode_opening(script: _Script, persona: dict, client: Any, *, audience: str,
                           greeting: Optional[str] = None) -> tuple[_Script, dict]:
    """Compose the lead around a verbatim continuation into the first story."""
    from core.errors import LLMError
    from . import persona as narrator
    from .adapt.common import AdaptError, call_json

    opening, *body = script.sections
    if opening.episode_role != "opening" or not body or body[0].episode_role != "item":
        return introduce_opening(script, persona, client, greeting=greeting)

    first, *rest = body[0].paragraphs
    sentences = [part for part in re.split(r"(?<=[。！？!?])", first.text) if part.strip()]
    if not sentences:
        return introduce_opening(script, persona, client, greeting=greeting)
    receipt = {"method": "scoped_lead", "version": 3}
    instruction = (
        "给中文视频写两三句开场，自然接到已写好的正文。先从观众的具体处境发问，或者把正文首段的"
        "开头一句移到这里，再接主持人自我介绍。entry_start 指开场后从正文首段的哪句起读，编号从 0 开始。"
        "只有把原句信息完整讲进开场时才跳过这句；首段至少留一句，后续正文保持原样。"
        "开场给观众继续听的理由，第一段就接着说过程或原因。照录已经给出的数字口径和归属。"
        '仅写开场，返回 JSON：{"opening":[{"text":"开场口播","citations":[]}],"entry_start":0}。'
        + narrator.prompt(persona, opening_only=True)
        + "\n这里是开场，直接接正文。"
    )
    if greeting:
        instruction += f"节目会先统一念出固定问候「{greeting}」，这里不另加问候。"
    request = json.dumps({"audience": audience, "first_item_title": body[0].title,
                          "entry_sentences": dict(enumerate(sentences)),
                          "following": [paragraph.text for paragraph in rest[:2]]}, ensure_ascii=False)
    try:
        lead = call_json(client, instruction, request, _EpisodeEntrance, temperature=0.4)
        if lead.entry_start >= len(sentences):
            return script, {**receipt, "status": "unchanged", "reason": "entry_start_out_of_range"}
        if narrator.opening_needs_introduction(lead.opening, persona, fixed_greeting=greeting or ""):
            return script, {**receipt, "status": "unchanged", "reason": "opening_repeats_identity"}
    except (AdaptError, LLMError) as exc:
        return script, {**receipt, "status": "unchanged", "reason": type(exc).__name__}
    opening = opening.model_copy(update={"paragraphs": lead.opening})
    first = first.model_copy(update={"text": "".join(sentences[lead.entry_start:]).strip()})
    item = body[0].model_copy(update={"paragraphs": [first, *rest]})
    return script.model_copy(update={"sections": [opening, item, *body[1:]]}), {
        **receipt, "status": "rewritten", "entry_start": lead.entry_start,
    }


def author_system_prompt(topic: dict, persona: dict, *, greeting: str | None = None, tutorial: bool = False,
                         episode_prompt: str | None = None, episode_lead: str | None = None) -> str:
    """Build the complete system message for the first drafting call."""
    programme = topic.get("content_mode") == "episode"
    system = (TUTORIAL_PROMPT if tutorial else episode_prompt or EPISODE_PROMPT) if programme else SYSTEM_PROMPT
    from .audience_profiles import CHANNELS
    channel = topic.get("audience_strategy")
    if topic.get("preserve_audience_scope") is True and (
            not programme or (isinstance(channel, str) and channel in CHANNELS)):
        system = system.replace(CONCRETE_RULE, SCOPE_CONCRETE_RULE, 1)
    if programme and not tutorial and episode_lead:
        system = system.replace(EPISODE_LEAD, episode_lead, 1)
    if greeting:
        system += GREETING_PROMPT.format(greeting=greeting)
    if topic.get("next_episode"):
        system += NEXT_EPISODE_PROMPT.format(next=topic["next_episode"])
    return system + narrator.prompt(persona)


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
    original_material: Optional[str] = None,
    greeting: Optional[str] = None,
    tutorial: bool = False,
    editorial_context: str = "",
    scoped_lead: bool = False,
    episode_lead: Optional[str] = None,
    episode_prompt: Optional[str] = None,
) -> WrittenScript:
    """Write a complete first draft; validate only its transport shape.

    A ``greeting`` is the column's fixed opening line: the author is told it is said first, and it is put in front
    of the script as its own paragraph rather than left to the model to word.
    """
    started = time.monotonic()
    from core.llm import LLMClient
    client = LLMClient(llm_config, cancel_token=cancel_token)
    report = report_body(report)
    programme = topic.get("content_mode") == "episode"
    persona = narrator.load()
    column_style, scrubbed_markers = author_input_gates.scrub(column_style)
    system = author_system_prompt(topic, persona, greeting=greeting, tutorial=tutorial,
                                  episode_prompt=episode_prompt, episode_lead=episode_lead)
    request = _request(report, topic, column_style, target_minutes, 0,
                       topic_fields=topic_fields, original_material=original_material)
    if editorial_context:
        request += "\n\n" + editorial_context
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": request},
    ]
    with telemetry.track("write"):
        response = client.chat(messages, temperature=0.4)
        attempts = 1
        try:
            script = _parse(response.content)
        except ScriptError:
            # A reply cut off mid-JSON (seen with the free models) is worth one more try before failing the script.
            attempts += 1
            response = client.chat(messages, temperature=0.4)
            script = _parse(response.content)
    if scoped_lead and programme and not tutorial:
        audience = (topic.get("fields") or {}).get("目标受众") or "普通观众"
        script, introduction = author_episode_opening(script, persona, client, audience=audience, greeting=greeting)
    else:
        script, introduction = introduce_opening(script, persona, client, greeting=greeting)
    script = _programme_frame(script, greeting, programme)
    allowed = trusted_urls(report, citation_check)
    if original_material is not None:
        allowed.update(trusted_urls(original_material, None))
    sections, check = _finalize(script, allowed, target_minutes, voice)
    if programme:
        check = {**length.measure(sections, None, voice=voice), "dropped_citations": check["dropped_citations"], "duration_policy": "content"}
    sections, stance = settle_sections(sections)
    if stance["changes"] and not stance.get("kept_original"):
        check = {**length.measure(sections, None if programme else target_minutes, voice=voice),
                 "dropped_citations": check.get("dropped_citations", 0),
                 **({"duration_policy": "content"} if programme else {})}
    from core.video import episodes
    title = episodes.publication_title(topic, script.title.strip())
    lint_source = report + ("\n" + original_material if original_material else "")
    lint_problems = lint(sections, lint_source)
    meta = {"attempts": attempts, "usage": dict(client.usage), "problems": lint_problems,
            **({"authored_title": script.title.strip()} if title != script.title.strip() else {}),
            "model": getattr(response, "model", "") or getattr(llm_config, "model", ""),
            "model_switches": list(getattr(client, "model_switches", [])),
            "duration_seconds": round(time.monotonic() - started, 3),
            "generation": "goal_first", "prompt_version": PROMPT_VERSION, "introduction": introduction,
            "creative_direction": column_style.strip(),
            "author_system_prompt_sha256": sha256(system.encode()).hexdigest(),
            "trusted_urls": len(allowed),
            **({'episode': episodes.manifest(topic, sections)} if programme else {})}
    if scrubbed_markers:
        meta["author_input_scrubbed"] = scrubbed_markers
    # Direction is under our control; article bodies may legitimately use short words like「合集」.
    author_input_gates.require_clean(column_style, where="creative_direction")
    note(meta, stance)
    return WrittenScript(title=title, sections=sections, length_check=check, meta=meta)

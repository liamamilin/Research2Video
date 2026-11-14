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

from pydantic import BaseModel, Field, ValidationError

from core import telemetry
from core.errors import CancelledError
from core.provenance import extract_urls, normalize_url

from . import length, persona as narrator
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
    episode_role: Literal["opening", "item", "closing"] | None = None


class _Script(BaseModel):
    title: str = Field(min_length=1)
    sections: list[_Section] = Field(min_length=1)


class _EpisodeEntrance(BaseModel):
    opening: list[_Paragraph] = Field(min_length=1)
    entry_start: int = Field(ge=0)


PROMPT_VERSION = 15
#: What the weekly review found titles that get watched have in common (two weeks, 120 videos): the product named,
#: the money stated, no date or programme name. The 11-10 benchmark (spec/benchmark-20261110/ai-report.md) adds the
#: framing: small accounts' AI hits ask a question, set two things against each other or state a judgement (41% / 23%
#: of titles against our 9% / 7%), with one or two numbers rather than a list of parameters.
TITLE_RULE = ("标题点名材料里的具体产品或公司（如 ChatGPT、Claude、Gemini、Qwen），材料给出价格、套餐、额度、成本或关键数字时把它写进标题；"
              "标题写成观众想点开的一句话：一个问题（如“XX 值不值得升级？”）、一个对比（如“同样 20 美元，三家卖的不是一样东西”）"
              "或一个直接的判断（如“建议先别急着换 XX”），可以带明确的态度；只放一两个最关键的数字，不堆参数清单，控制在 30 字以内；"
              "标题不写日期，不写栏目名，不用“本期”“今日”开头。")
#: The first seconds decide whether a viewer stays (11-10 benchmark): small accounts' hits open on the hook itself.
OPENING_RULE = ("第一句话就要让观众想听下去：用一个反常识的数字、一个直接问观众的问题，或者先说结论；"
                "钩子要落在目标观众自己的处境上，不用和观众无关的全球或海外统计开场（11-10 试片用“全球八成人用 ChatGPT”开场，国内观众听着不沾边）；"
                "第一句不打招呼，不报栏目名，不用“据某某报道”起头；钩子后的问候和自我介绍按讲述者设定自然衔接。")
ENDING_RULE = ("结尾落在本期最值得带走的判断、解释或下一步。有一个确实值得观众用自己的经历回答的问题，再邀请他们聊；"
               "避免把正文讲清的取舍硬凑成站队。")
#: How the two most-watched videos talk (2026-11-02 review): short lines, a stated judgement, few numbers said aloud.
STYLE_RULE = ("像对一个懂行的朋友讲：多用短句，可以直接说出你的判断（比如“我的建议是先别升级”）。"
              "一段口播只念最关键的两三个数字，钱说成观众熟悉的口径（每月多少钱，材料给了人民币就说人民币），其余参数交给画面，但具体的场景、动作和原话要讲出来。"
              "英文术语换成中文说法，或用半句话讲明白。一个前提或限制只在相关处说一次，说短，不在后面重复。")
#: Narration is what viewers hear; a note to the picture ("画面里可以……") was read out and subtitled (11-10 research,
#: spec/bilibili-growth-research-20261110/SAMPLE-O03.md). The program also rewrites what slips through (``spoken_only``).
#: An item's 核心角度 names what it is about; the 11-10 trial narrowed to three dimensions and dropped the one point
#: its angle named (ChatGPT's barrier for Chinese users).
ANGLE_RULE = "选题核心角度里点名要比较或讲清的点，正文必须讲到，不能为了收窄或控制篇幅把它省掉。"
#: Scripts felt distant (11-11): a lecture skeleton said aloud, hedges read out, summaries instead of what happens. The
#: same item written four ways (current model, deepseek-v4-pro, this rule, plus the original articles) showed the rule,
#: not the stronger model or more material, gives a scene with quoted words and no abstract lines.
CONCRETE_RULE = ("写具体，不写远：每条内容跟着一个具体的人、一个具体时刻走（谁、在哪、手里拿着什么、屏幕上跳出什么字），"
                 "每个结论都落到这个场景里发生的事；材料里有的原话、真实输出、按钮名、页面上的字，直接念出来；"
                 "判断用具体后果说（少等几分钟、多按几下、每月多花多少钱），不用“省的是思考不是劳动”“门槛基本为零”这类概括；"
                 "正文不说结构提示语（“第一个维度”“这一维度上”“原因我后面说”），换话题就直接说下一件事；开场自我介绍可以简短说明本期具体问题；"
                 "上游未提供的事实、数字和亲历不要编造；把“待实测”这类制作备注留在稿外。")
PICTURE_RULE = "口播只写观众听到的话：不描述画面、不给画面或镜头提要求（不说“画面里可以……”“画面还是……”），要打比方就直接讲情境。"
GOAL = "根据素材，产出目标观众愿意看、听得懂、想继续看的视频。"
AUTHORING_PROMPT = GOAL + """
当前步骤：你是本期讲述者，现在直接对目标观众讲述，正文就是观众将听到的口播。
找到这次讲述最值得听的东西，让开场引起兴趣，让观众随着讲述理解它，并愿意继续听。
沿用上游给出的事实、数据和计算。挑出最值得讲的材料，给出讲述者自己的解释、判断或取舍，让观众听到原材料之外的一层认识。
有意思的推论和猜测可以直接讲，交代从哪个细节想到的，用“我猜”或“我更看重”这样的自然口吻表达。
有两面时，讲清哪一面在眼下更重要、凭什么，或哪个条件会改变选择。把真正影响判断的条件放在相关处，结尾把自己的观点讲完整。
研究备忘仅作内部取材背景。素材里的“需要核”“可拍任务”和未取到资料的清单等制作备忘留在制作侧。
""" + STYLE_RULE + PICTURE_RULE + ANGLE_RULE + CONCRETE_RULE
SYSTEM_PROMPT = AUTHORING_PROMPT + """
讲述有起承转合：开头引出本期的问题或看点，中段一步步展开，在关键处转折或点出要害，结尾回到开头的问题落下结论。
""" + OPENING_RULE + """
让标题说出本期最有意思的具体事情。""" + TITLE_RULE + ENDING_RULE + """
接口输出 JSON：
{"title":"视频标题","sections":[{"title":"小节标题","paragraphs":[{"text":"口播正文","citations":["素材链接"]}]}]}"""
EPISODE_LEAD = "整期按节目讲述：开场用一两句话抛出本期最抓人的那一条，再用一句话报出后面还有哪几条，细节留到正文讲。"
EPISODE_PROMPT = AUTHORING_PROMPT + """
你是整期节目的讲述者，一篇总稿覆盖报告中的多个独立内容；同一问题的内容按解释层次组织。
各条内容分别充分解释，不把本期多个事件缩成一个主事件的几个角度。
各独立内容使用自己的具体对象、主例子和讲述线索，整期按合理顺序排列；同一问题的解释章节沿用共同主例子。
""" + EPISODE_LEAD + OPENING_RULE + """
随后逐条展开。两条之间确有关系、对比或递进就用一句话接上；没有就直接切换（比如“再说 Gemini。”），不硬造过渡，不重新打招呼。每条用一句短判断收住，各条措辞不同，不用“最值得记住的是”“这一条的要点是”之类的固定句式。
收尾只用一两句话落下本期最重要的一个判断，不逐条复述。""" + ENDING_RULE + """
每个内容一个 section，内部可有多个 paragraphs；episode_role 使用 opening、item、closing。
整期共用开场和收尾，不受单条视频的篇幅限制。具体看点体现在各内容标题中；整期总标题用本期最值得看的一两条内容来写，""" + TITLE_RULE + """
输出 JSON：{"title":"本期标题","sections":[{"title":"短标题","episode_role":"item","paragraphs":[{"text":"口播正文","citations":[]}]}]}"""

EXPLAINER_EPISODE_PROMPT = AUTHORING_PROMPT + """
你要把研究底稿里一个有足够细节的科学问题，讲成一集解释。整期跟着同一个对象，从可以想象的现象讲到它为何发生。研究报告里的其他发现只有在帮助解释这个问题的一步时进入。拿同一个对象的前后状态推动讲述；介绍实验时说清实验做了什么、看到了什么和它怎样帮助理解那个对象。每一节继续回答上一节留下的问题。
""" + EPISODE_LEAD + OPENING_RULE + """
每个解释阶段用一个 section。标题围绕同一个开场现象，结尾回到这个现象，给出经过讲述后的答案。
""" + ENDING_RULE + """
开场用 opening，解释阶段用 item，结尾用 closing。
输出 JSON：{"title":"本期标题","sections":[{"title":"短标题","episode_role":"item","paragraphs":[{"text":"口播正文","citations":[]}]}]}
"""

BUSINESS_EPISODE_PROMPT = AUTHORING_PROMPT + """
这期用一个真实业务过程解释一门生意。各节沿同一笔交易，依次写客户需求、购买和付款、产品或服务怎样交到客户手里，以及已有材料中接下来发生的事。每次出现新角色，让观众听见它接过什么、付出什么、交给谁。另一个案例在能帮助解释这笔交易里的具体经营取舍时出现。按材料组织讲述，顺序跟着这笔交易走。
""" + EPISODE_LEAD + OPENING_RULE + """
每个业务阶段用一个 section。标题围绕本期最值得追问的经营取舍，收尾回到这笔生意靠什么让客户愿意继续买、让交付方能继续做。
""" + ENDING_RULE + """
开场用 opening，业务阶段用 item，结尾用 closing。
输出 JSON：{"title":"本期标题","sections":[{"title":"短标题","episode_role":"item","paragraphs":[{"text":"口播正文","citations":[]}]}]}
"""

ARTWORK_EPISODE_PROMPT = AUTHORING_PROMPT + """
整期从一件具体作品入手，讲清它怎样改变观众的观看或聆听。选底稿里能展开的作品，先让人注意一个真实细节，然后沿这处细节讲创作者的动作、形式怎样变化、观众会感受到什么。可以从新发现切入，但要回到作品中能被辨认的部分。相邻作品只在直接照亮这处表达时出现。
""" + EPISODE_LEAD + OPENING_RULE + """
每个推进观众理解的部分用一个 section。标题点出作品或一处值得看听的细节，结尾回到同一作品，留下可亲自观察的一处。
""" + ENDING_RULE + """
开场用 opening，讲述阶段用 item，结尾用 closing。
输出 JSON：{"title":"本期标题","sections":[{"title":"短标题","episode_role":"item","paragraphs":[{"text":"口播正文","citations":[]}]}]}
"""

CULTURE_CHANGE_EPISODE_PROMPT = AUTHORING_PROMPT + """
用一件有具体来路的代表作品或表达讲清本期文化变化。让作品随人物的动作和出现的场合持续推进；每一节讲清这次创作、展示或传播使后来的人怎样理解它。选有材料支撑的环节展开。别的作品只在能解释一次具体变化时带到，随后接回主作品。
""" + EPISODE_LEAD + OPENING_RULE + """
每次推进变化用一个 section。标题用具体作品、行动或观众反应带出问题。结尾回到主作品现在的处境或意义。
""" + ENDING_RULE + """
开场用 opening，讲述阶段用 item，结尾用 closing。
输出 JSON：{"title":"本期标题","sections":[{"title":"短标题","episode_role":"item","paragraphs":[{"text":"口播正文","citations":[]}]}]}
"""

MANUFACTURING_EPISODE_PROMPT = AUTHORING_PROMPT + """
整期解释一件产品或一个部件怎样被制造并交付。跟同一个产品走，每节解释工具或人做了什么、产品的状态怎样变、下一步为什么要接在这里。已有材料支持的工序充分展开。规格、设备、伙伴和另一个案例只在帮助理解当下工序或交付条件时进入。
""" + EPISODE_LEAD + OPENING_RULE + """
每个改变产品状态或交付条件的步骤用一个 section。标题写出具体产品或最值得追问的制造变化，结尾回到用途，说明做出来以后要怎样持续交付。
""" + ENDING_RULE + """
开场用 opening，制造阶段用 item，结尾用 closing。
输出 JSON：{"title":"本期标题","sections":[{"title":"短标题","episode_role":"item","paragraphs":[{"text":"口播正文","citations":[]}]}]}
"""

TUTORIAL_PROMPT = AUTHORING_PROMPT + """
你是工具教程的讲述者，沿同一份输入讲到实际输出。
把准备输入、执行操作、查看结果等可跟随的阶段分别写成 section，让视频直接用这些章节做步骤转场。
每个阶段有清楚的短标题与必要的 paragraphs，沿用材料已有的实际命令、截图和结果。
输入展示与操作代码各在对应阶段展开，结果阶段可以对照同一份输入和输出。
旁白用自然中文讲清截图里看得到的内容；命令可以保留原文，图片中的 Markdown 标题、列表和表格用口语解释。
不要把代码围栏、整块 Markdown 原文或表格分隔线写成口播正文，这些内容由实际截图呈现。
开场简短说明完成什么任务，收尾回到实际结果；全片共用问候和告别。
episode_role 使用 opening、item、closing。保留实际步骤顺序，不把所有步骤塞进同一个项目章节。
输出 JSON：{"title":"本期标题","sections":[{"title":"短标题","episode_role":"item","paragraphs":[{"text":"口播正文","citations":[]}]}]}"""


#: A series item that has a next one (selection.next_episode): the last words before the farewell promise it.
NEXT_EPISODE_PROMPT = """
这是一个连续系列，下一期已经排好，讲的是「{next}」。道别前用一句话预告它：把它说成观众关心的一个问题（比如“下一期我接着讲：……？”），让观众知道关注后下一期能得到什么；只预告这一个，不编别的内容。"""
GREETING_PROMPT = """
口播开头由节目固定念出问候「{greeting}」，正文从这句问候之后接着讲，不再另外问候或介绍栏目；最后用一句简短的道别结束。"""
#: Opening and closing sections of a programme carry these titles, so the screen never shows a working label.
OPENING_TITLE, CLOSING_TITLE = "本期导读", "本期回顾"


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
    report = report_body(report)
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
        pacing = ("整期篇幅由内容决定，各项分别安排解释的深浅。" if programme else
                  f"配方参考片长约 {target_minutes:g} 分钟，按栏目目标安排解释的深浅。")
        throughline = ("各独立内容中的具体例子持续推进；同一问题的章节沿用共同主例子、按解释层次展开，参数贴着当前一步出现。"
                       if programme else
                       "让同一个具体任务在中段持续推进，参数贴着它解释的作用出现，结尾回到任务的结果与本期形成的认识。")
        material = (
            f"## 讲述节奏\n{pacing}"
            "优先把主例子的关键动作、机制和结果讲清楚，片长作为节奏参考，不机械按字数压缩。\n\n"
            "## 本期研究底稿\n" + report
            + "\n\n## 对应原始材料\n" + (original_material or "本期沿用研究底稿，未附独立原始材料。")
            + "\n\n## 创作衔接\n"
            "结合底稿中的背景、关系与机制解释，以及原始材料的具体细节，围绕本期问题写完整口播。"
            "让具体例子中的对象、操作过程与结果帮助观众理解；重要机制可以充分展开。"
            + throughline
            + "篇幅与顺序服务于理解，不按固定比例缩写底稿，也不逐节复述。"
        )
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
        sections.append({"title": section.title.strip(), "paragraphs": paragraphs,
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
    """Author a missing/bare introduction in context once; never insert a stock sentence.

    The same author sees the real opening and the next section. Only the opening
    section can change; the rest of the script and its metadata stay intact.
    Failure leaves the original available and records the unsuccessful attempt.
    """
    from core.video import persona as narrator
    if not narrator.opening_needs_introduction(script.sections[0].paragraphs, persona, fixed_greeting=greeting or ""):
        return script, {"status": "authored", "requests": 0}
    instruction = (
        "修顺这篇口播的开场，保留完整钩子、事实、人物引语和引用链接。将原来孤立的自我介绍改写为问候、名字和具体话题的连贯表达，不是另外新增一段介绍。"
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
            return script, {"status": "unchanged", "requests": 1, "reason": "opening_still_missing_or_bare",
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
            return script, {**receipt, "status": "unchanged", "reason": "opening_still_missing_or_bare"}
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
    from core.video import episodes
    title = episodes.publication_title(topic, script.title.strip())
    return WrittenScript(
        title=title,
        sections=sections,
        length_check=check,
        meta={"attempts": attempts, "usage": dict(client.usage), "problems": [],
              **({"authored_title": script.title.strip()} if title != script.title.strip() else {}),
              "model": getattr(response, "model", "") or getattr(llm_config, "model", ""),
              "model_switches": list(getattr(client, "model_switches", [])),
              "duration_seconds": round(time.monotonic() - started, 3),
              "generation": "goal_first", "prompt_version": PROMPT_VERSION, "introduction": introduction,
              "creative_direction": column_style.strip(),
              "author_system_prompt_sha256": sha256(system.encode()).hexdigest(),
              "trusted_urls": len(allowed),
              **({'episode': episodes.manifest(topic, sections)} if programme else {})},
    )

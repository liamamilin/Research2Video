"""Choose public multi-platform titles from the finished narration, without changing its words."""
from __future__ import annotations

import json

from pydantic import BaseModel, Field

from core.llm import CancelledError
from . import writer
from .adapt.common import AdaptError, call_json
from .policy import TITLE_GUIDANCE

PROMPT_VERSION = 4
DOUYIN_TITLE_MAX = 30
COVER_HEADLINE_MAX = 16

_SINGLE_RULES = """当前是单条视频：
- `title`（B 站主标题）：控制在 32–44 字，高信息密度，前 12 字点出核心产品/模型/对象或熟悉场景，优先带入正文真实数字（价格、比例、额度、日期等）与明确反差或后果；保留上游 `topic_title` 里的核心实体锚点，不要洗丢。
- `douyin_title`（抖音短标题）：控制在 16–28 字（严禁超过 30 字），保留最核心的 1 个实体 + 1 个数字或反转钩子，读起来完整顺口，不要做省略号截断。
- `cover_headline`（封面大字）：控制在 8–14 字，提炼最强冲突或数字，适合做封面大字。"""

_EPISODE_RULES = """当前是整期多条目合集视频：
- `title`（B 站整期主标题）：控制在 32–46 字，只允许两种写法——要么「单押本期最炸的 1 条」（大牌实体 + 真实数字 + 后果/反差），要么「双核硬拼」（只选最强 2 条，且两条都必须自带明确产品名与具体数字或反差）。严禁硬塞 3 条，严禁压成无主语或丢数字的残句，不写日期和栏目名。
- `douyin_title`（抖音短标题）：控制在 16–28 字（严禁超过 30 字），**强制单押本期最炸的 1 条**，不要在 30 字内硬塞两条新闻。
- `cover_headline`（封面大字）：控制在 8–14 字，**只打最强那 1 条的核心数字或冲突**，不要把整条长标题贴上封面。"""

PROMPT_TEMPLATE = (
    TITLE_GUIDANCE
    + "\n当前步骤：基于已定稿的口播正文，产出高点击、零标题党的视频标题组合（一稿三题）。\n"
    + "{mode_rules}\n"
    + "工作方式：\n"
    + "1. 先从 4 个不同切入角（如反差后果、数字账单、对比选型、避坑实用；图解/历史/文化类可用熟悉现象、反直觉结果、关键尺度数字）各拟 1 个候选，每条都必须附带正文里逐字存在的原句 `quotes` 作为证据。\n"
    + "2. 显式避开低效模板：不要写成菜单操作说明书（如“选中某段点某按钮”），不要写缺少实体或数字的空洞泛问句。\n"
    + "3. 比较 `current_title` 与候选，选出最优项下标填入 `choice`（若 `current_title` 已经足够好，可填 `-1` 保留主标题，但仍可在顶层给出优化后的 `douyin_title` 和 `cover_headline`）。\n"
    + '接口输出 JSON：\n'
    + '{"candidates":[{"angle":"切入角","title":"B站主标题","douyin_title":"抖音短标题","cover_headline":"封面大字","reason":"看点","quotes":["正文原句"]}],"choice":0,"douyin_title":"保留原主标题时的抖音短标题(可选)","cover_headline":"保留原主标题时的封面大字(可选)","reason":"选择理由"}'
)
PROMPT = PROMPT_TEMPLATE.replace("{mode_rules}", _SINGLE_RULES)


class Candidate(BaseModel):
    angle: str = ""
    title: str = Field(min_length=2, max_length=80)
    douyin_title: str = ""
    cover_headline: str = ""
    reason: str = Field(min_length=1)
    quotes: list[str] = Field(min_length=1)


class Selection(BaseModel):
    candidates: list[Candidate] = Field(default_factory=list)
    choice: int = Field(ge=-1)
    douyin_title: str = ""
    cover_headline: str = ""
    reason: str = Field(min_length=1)


def _clean_short(text: str, *, min_len: int, max_len: int, allowed_numbers: set[str]) -> str:
    cleaned = " ".join((text or "").split()).strip()
    if not min_len <= len(cleaned) <= max_len:
        return ""
    if writer._numbers(cleaned) - allowed_numbers:
        return ""
    return cleaned


def finish(
    client,
    *,
    title: str,
    sections: list[dict],
    column_style: str,
    topic_title: str = "",
    is_episode: bool = False,
    column_key: str = "",
) -> dict:
    """Keep the existing title when selection fails; always retain review evidence."""
    record: dict = {
        "prompt_version": PROMPT_VERSION,
        "before": title,
        "title": title,
        "douyin_title": "",
        "cover_headline": "",
        "applied": False,
        "is_episode": bool(is_episode),
    }
    narration = [p["text"] for s in sections for p in s["paragraphs"]]
    allowed_numbers = writer._numbers("\n".join([*narration, title, topic_title]))
    payload: dict = {
        "audience_and_recipe": column_style,
        "current_title": title,
        "narration": narration,
    }
    if topic_title:
        payload["topic_title"] = topic_title
    if column_key:
        payload["column_key"] = column_key
    if is_episode:
        payload["content_mode"] = "episode"
    request = json.dumps(payload, ensure_ascii=False)
    prompt = PROMPT_TEMPLATE.replace("{mode_rules}", _EPISODE_RULES if is_episode else _SINGLE_RULES)
    try:
        result = call_json(client, prompt, request, Selection, temperature=0.5).model_dump()
        index = result["choice"]
        if index >= len(result["candidates"]):
            raise AdaptError("标题选择下标无效")
        raw_douyin = result.get("douyin_title") or ""
        raw_cover = result.get("cover_headline") or ""
        if index >= 0:
            selected = result["candidates"][index]
            if not all(q.strip() and any(q in p for p in narration) for q in selected["quotes"]):
                raise AdaptError("标题依据不是正文原句")
            invented = writer._numbers(selected["title"]) - allowed_numbers
            if invented:
                raise AdaptError(f"标题包含正文没有的数字：{', '.join(sorted(invented))}")
            chosen_title = " ".join(selected["title"].split()).strip()
            record.update(title=chosen_title, applied=chosen_title != title)
            raw_douyin = selected.get("douyin_title") or raw_douyin
            raw_cover = selected.get("cover_headline") or raw_cover
        douyin_title = _clean_short(raw_douyin, min_len=4, max_len=DOUYIN_TITLE_MAX, allowed_numbers=allowed_numbers)
        cover_headline = _clean_short(raw_cover, min_len=2, max_len=COVER_HEADLINE_MAX, allowed_numbers=allowed_numbers)
        if douyin_title:
            record["douyin_title"] = douyin_title
        if cover_headline:
            record["cover_headline"] = cover_headline
        record.update(selection=result)
    except CancelledError:
        raise
    except Exception as exc:  # Keep a usable draft when the optional edit fails.
        record["error"] = f"{type(exc).__name__}: {exc}"
    return record

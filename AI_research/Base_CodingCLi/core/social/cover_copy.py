"""Cover copy for the number-led cover: one big line, a comparison line and the subject, taken from the narration.

The title is too long to read at feed size, so the cover gets its own short copy. Numbers are taken as the script
gives them: a number the narration and title do not contain is not put on the cover (the old cover is used instead).
Everything here is best effort: any failure returns None and the cover falls back to the title layout.
"""
from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

from pydantic import BaseModel, Field

PROMPT_VERSION = 1
PROMPT = (
    "为视频做封面文字。封面在手机信息流里只有拇指大小，观众一眼要看到最抓人的数字或结论。\n"
    "只用旁白和标题里已有的内容，数字、价格、倍数照原文写，不要换算、不要新造。\n"
    "输出 JSON：{\"big\":\"最大的字\",\"figure\":\"一行对比或数字\",\"topic\":\"主题\"}\n"
    "- big：最抓人的数字或结论，不超过 6 个汉字（英文和数字算半个），例如「差十倍」「涨价 20%」「免费了」。\n"
    "- figure：一行具体数字或对比，不超过 16 个汉字，例如「$600 vs $60 / 月」；没有合适的数字就写空字符串。\n"
    "- topic：说清是哪个产品或哪件事，不超过 16 个汉字，例如「ChatGPT · Claude · Gemini 订阅」。\n"
    "不要写日期、栏目名、问号、感叹号和引号。"
)
NARRATION_CHARS = 6000
REQUEST_SECONDS = 45
TOTAL_SECONDS = 90
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


class CoverCopy(BaseModel):
    big: str = Field(min_length=1, max_length=12)
    figure: str = Field(default="", max_length=24)
    topic: str = Field(default="", max_length=24)


def width(text: str) -> float:
    """Display width in Chinese characters: Latin letters, digits and spaces count half."""
    return sum(0.5 if ord(c) < 0x2E80 else 1 for c in text)


def _numbers(text: str) -> set[str]:
    return set(_NUMBER.findall(text.replace(",", "").replace("，", "")))


def accepted(copy: dict, source: str) -> dict | None:
    """The copy if it fits the layout and every number on it is in the narration or title; otherwise None."""
    big, figure, topic = (" ".join(str(copy.get(k) or "").split()) for k in ("big", "figure", "topic"))
    if not big or width(big) > 6 or width(figure) > 16 or width(topic) > 16:
        return None
    if not (_numbers(big) | _numbers(figure)) <= _numbers(source):
        return None
    return {"big": big, "figure": figure, "topic": topic}


def generate(client, title: str, narration: str) -> dict | None:
    from core.video.adapt.common import call_json

    request = json.dumps({"title": title, "narration": narration[:NARRATION_CHARS]}, ensure_ascii=False)
    result = call_json(client, PROMPT, request, CoverCopy, temperature=0.4).model_dump()
    return accepted(result, title + "\n" + narration)


def narration_of(render: dict) -> str:
    from core.video import store

    version = store.get_version(render["script_id"], int(render["version"]))
    return "\n".join(p.get("text", "") for s in (version or {}).get("sections") or [] for p in s.get("paragraphs") or [])


def client():
    from core.config import load_system_config
    from core.llm import LLMClient, LLMConfig
    from web.settings import get_settings

    base = LLMConfig.from_system(load_system_config(get_settings().paths.config_dir), "ai")
    # On the upload path: a slow or failing model costs at most a minute or two, then the title cover is used.
    return LLMClient(replace(base, request_timeout=REQUEST_SECONDS, timeout=TOTAL_SECONDS, max_retries=1,
                             max_tokens=2048, stream=False))


def for_render(render: dict, title: str, cache: Path) -> dict | None:
    """Cover copy for this render and title, made once; a failure is remembered so other platforms do not wait again."""
    try:
        if cache.is_file():
            return json.loads(cache.read_text(encoding="utf-8")).get("copy")
    except (OSError, ValueError):
        pass
    if not render.get("script_id") or render.get("version") is None:
        return None
    record: dict = {"prompt_version": PROMPT_VERSION, "copy": None}
    try:
        narration = narration_of(render)
        if narration.strip():
            record["copy"] = generate(client(), title, narration)
            if record["copy"] is None:
                record["reason"] = "封面文字超长或含旁白里没有的数字"
        else:
            record["reason"] = "没有旁白"
    except Exception as exc:  # noqa: BLE001 - the title cover is always available
        record["reason"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return record["copy"]

"""Choose a public title from the finished narration, without changing its words."""
from __future__ import annotations

import json

from pydantic import BaseModel, Field

from core.llm import CancelledError
from .adapt.common import AdaptError, call_json
from .policy import TITLE_GUIDANCE

PROMPT_VERSION = 3
PROMPT = '根据素材，产出目标观众愿意看、听得懂、想继续看的视频。\n当前步骤：生成视频标题。\n接口输出 JSON：\n{"candidates":[{"title":"标题","reason":"看点","quotes":["正文原句"]}],"choice":-1,"reason":"选择理由"}'


class Candidate(BaseModel):
    title: str = Field(min_length=2, max_length=80)
    reason: str = Field(min_length=1)
    quotes: list[str] = Field(min_length=1)


class Selection(BaseModel):
    candidates: list[Candidate] = Field(default_factory=list)
    choice: int = Field(ge=-1)
    reason: str = Field(min_length=1)


def finish(client, *, title: str, sections: list[dict], column_style: str) -> dict:
    """Keep the existing title when selection fails; always retain review evidence."""
    record = {"prompt_version": PROMPT_VERSION, "before": title, "title": title, "applied": False}
    narration = [p["text"] for s in sections for p in s["paragraphs"]]
    request = json.dumps({"audience_and_recipe": column_style, "current_title": title,
                          "narration": narration}, ensure_ascii=False)
    try:
        result = call_json(client, PROMPT, request, Selection, temperature=0.5).model_dump()
        index = result["choice"]
        if index >= len(result["candidates"]):
            raise AdaptError("标题选择下标无效")
        if index >= 0:
            selected = result["candidates"][index]
            if not all(q.strip() and any(q in p for p in narration) for q in selected["quotes"]):
                raise AdaptError("标题依据不是正文原句")
            record.update(title=selected["title"], applied=selected["title"] != title)
        record.update(selection=result)
    except CancelledError:
        raise
    except Exception as exc:  # Keep a usable draft when the optional edit fails.
        record["error"] = f"{type(exc).__name__}: {exc}"
    return record

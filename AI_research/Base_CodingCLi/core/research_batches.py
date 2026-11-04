"""Materials too large for one model call are researched in batches and merged, never cut.

A source block is a header (instructions, the collection range) followed by ``### [title](url)`` items, each one a
whole material with its 材料编号. Batches split only between items and every batch repeats the header, so each part
is researched under the same rules. The batch drafts are then merged into one report; the material receipts the
supply commit reads are joined here in code, not left to the merging model.
"""
from __future__ import annotations

import json
import math
import re

from .errors import LLMError

# The research model's window (deepseek-v4.1-flash: 1,048,576 tokens). 11-04 telemetry: the flash prompt of 1.59M
# chars counted 520k tokens (3.1 chars/token), t09's 624k chars 228k (2.7); 2.5 keeps Chinese-heavy material safe.
CONTEXT_TOKENS = 1_048_576
CHARS_PER_TOKEN = 2.5
OUTPUT_RESERVE_TOKENS = 100_000
BATCH_SHARE = .6  # of the window per batch, leaving room for an estimate that runs low
MAX_PROMPT_CHARS = int((CONTEXT_TOKENS - OUTPUT_RESERVE_TOKENS) * CHARS_PER_TOKEN)
BATCH_CHARS = int(CONTEXT_TOKENS * BATCH_SHARE * CHARS_PER_TOKEN)

_ITEM = re.compile(r"(?m)^(?=### \[)")
_RECEIPT = re.compile(r"<!--\s*material-use:\s*(\{.*?\})\s*-->", re.S)
_OVERFLOW = re.compile(r"maximum context length is (\d+) tokens.*?(?:prompt|messages?) (?:has|have|resulted in) (\d+) tokens", re.S)


BATCH_NOTE = ("\n\n（这是本期材料中的一批，其余批次另行整理后合并。本批只整理候选清单：每个候选写明具体对象、"
              "这次的变化、关键数字与细节、可展示的页面和原始链接、材料编号，用要点写，不写导读和成稿；"
              "完整展开在合并时完成。报告末尾照常附素材回执。）")


class Truncated(LLMError):
    """A batch draft or the merge stopped at the model's output limit: the result is incomplete."""


class OversizedItem(ValueError):
    """One material on its own is larger than a batch: it is reported, not truncated."""


def overflow(error: Exception) -> tuple[int, int] | None:
    """``(window, prompt)`` tokens from a provider's context-length refusal; None for any other failure."""
    found = _OVERFLOW.search(str(error))
    return (int(found.group(1)), int(found.group(2))) if found else None


def split(block: str) -> tuple[str, list[str]]:
    """The header of a source block and its items, each item whole."""
    parts = _ITEM.split(block)
    return parts[0], [p for p in parts[1:] if p.strip()]


def pack(header: str, items: list[str], limit: int) -> list[str]:
    """Item-boundary batches of at most ``limit`` chars, each led by the header, in the original order."""
    room = limit - len(header)
    batches, current, size = [], [], 0
    for item in items:
        if len(item) > room:
            title = item.splitlines()[0][:120]
            raise OversizedItem(f"单条材料 {len(item)} 字，超过一批可处理的 {room} 字，无法分批：{title}")
        if current and size + len(item) > room:
            batches.append(header + "".join(current))
            current, size = [], 0
        current.append(item)
        size += len(item)
    if current or not batches:
        batches.append(header + "".join(current))
    return batches


def batches_for(block: str, base_chars: int, *, limit: int = BATCH_CHARS) -> list[str]:
    header, items = split(block)
    return pack(header, items, max(1, limit - base_chars))


def resized(prompt_tokens: int, window: int, block_chars: int) -> int:
    """A batch size, in chars, that brings an over-window prompt to ``BATCH_SHARE`` of the window."""
    parts = math.ceil(prompt_tokens / (window * BATCH_SHARE))
    return max(1, block_chars // max(2, parts))


def strip_receipts(report: str) -> tuple[str, dict[str, list[str]]]:
    joined: dict[str, list[str]] = {"selected": [], "background": [], "deferred": []}
    for match in _RECEIPT.finditer(report):
        try:
            value = json.loads(match.group(1))
        except ValueError:
            continue
        for key in joined:
            joined[key] += [str(v) for v in value.get(key) or [] if str(v) not in joined[key]]
    return _RECEIPT.sub("", report).rstrip(), joined


def joined_receipt(drafts: list[str]) -> str:
    """One material receipt for the merged report. An id selected in any batch stays selected; it is background
    or deferred only where no batch used it for a topic."""
    total: dict[str, list[str]] = {"selected": [], "background": [], "deferred": []}
    for draft in drafts:
        for key, ids in strip_receipts(draft)[1].items():
            total[key] += [i for i in ids if i not in total[key]]
    total["background"] = [i for i in total["background"] if i not in total["selected"]]
    total["deferred"] = [i for i in total["deferred"] if i not in total["selected"] and i not in total["background"]]
    return "<!-- material-use: " + json.dumps(total, ensure_ascii=False) + " -->"


def merge_prompt(task_prompt: str, drafts: list[str]) -> str:
    parts = [strip_receipts(d)[0] for d in drafts]
    return (task_prompt + f"\n\n## 分批研究稿\n本期材料超过单次处理上限，已按材料顺序分成 {len(parts)} 批，"
            "每批按同一任务要求各自整理。把下面各批研究稿合并成一份完整研究底稿：同一事件合并成一个条目，"
            "保留各批的细节、例子和原始链接；按任务要求重新排序和写导读；不要遗漏任何一批中的条目。"
            "沿用任务指定的条目与来源格式。素材回执由程序合并，不要输出 material-use 注释。\n\n"
            + "\n\n".join(f"### 第 {i} 批研究稿\n\n{p}" for i, p in enumerate(parts, 1)))

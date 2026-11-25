"""Extract clean editorial scope from Job prompts for non-R7 stages.

Job YAML ``prompt`` fields mix two concerns:
1. Editorial scope (audience, subject boundaries, inclusion/exclusion rules).
2. R7 Markdown report formatting (``任务：{name}``, ``### 1. 选题标题``,
   ``## 来源清单``, empty-topic sentinels).

Stages that do NOT render the R7 Markdown report — specifically OpenCode gather
(``core/opencode_gather.py``) and audience-led JSON selection
(``core/video/audience_strategy.py``) — should receive only the editorial scope,
avoiding unrendered ``{var}`` placeholders and conflicting layout instructions.
"""
from __future__ import annotations

import re

_META_LINE = re.compile(
    r"^(?:任务|领域|关键词|日期|输出语言)：.*$"
)
_LAYOUT_START = re.compile(
    r"^(?:输出\s*Markdown|数据格式：Markdown|###\s+(?:1\.|2\.|具体选题标题|来源清单)|##\s+来源清单)"
)
_STANDALONE_LAYOUT_LINE = re.compile(
    r"^(?:若变化监测显示没有新增或修改|没有合适题材时|材料有合适的比较安排时|"
    r"记录实际使用的来源链接|素材链接。?$|正文充分展开，末尾列来源清单|"
    r"日期：\{date\}。根据取得的材料形成可供视频作者使用的研究底稿)"
)
_TEMPLATE_BULLET = re.compile(r"^-\s+\*\*[^*]+\*\*：")


def editorial_brief(prompt: str) -> str:
    """Return the editorial-scope portion of a Job prompt without R7 layout noise.

    Falls back to the trimmed original prompt if stripping would leave too little
    text (e.g., short synthetic prompts in unit tests).
    """
    raw = (prompt or "").strip()
    if not raw:
        return ""

    kept: list[str] = []
    skipping_template = False

    for line in raw.splitlines():
        s = line.strip()
        if not s:
            if skipping_template:
                # A blank line ends a markdown template section unless the next
                # line starts another layout block.
                skipping_template = False
                continue
            if kept and kept[-1] != "":
                kept.append("")
            continue

        if _META_LINE.match(s):
            skipping_template = False
            continue

        if _LAYOUT_START.match(s) or _STANDALONE_LAYOUT_LINE.match(s):
            skipping_template = True
            continue

        if skipping_template:
            if _TEMPLATE_BULLET.match(s) or s.startswith("### ") or s.startswith("## "):
                continue
            # Single-line continuation of a layout block (e.g. "记录实际使用的来源链接。")
            if "来源清单" in s or "本次暂无新题材" in s or "使用条目格式" in s:
                continue
            skipping_template = False

        # Drop inline R7 template trigger sentences if they appear mid-paragraph.
        if "使用条目格式：" in s:
            before = s.split("使用条目格式：", 1)[0].strip()
            if before:
                kept.append(before)
            skipping_template = True
            continue

        kept.append(line.rstrip())

    cleaned = "\n".join(kept).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned if len(cleaned) >= 20 else raw

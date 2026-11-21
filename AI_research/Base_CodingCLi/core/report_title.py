"""Use the same substantive heading for report lists, video topics and history."""
from __future__ import annotations

import re

_PREFACE = re.compile(r"^(?:选题说明|选题思路|选题理由|选题依据|写作说明|研究说明)(?:$|[（(:：\s])")


def title_from_text(text: str) -> str:
    """Skip editorial prefaces; prefer an explicitly labelled main question."""
    headings = re.findall(r"(?m)^\s{0,3}(#{1,2})[ \t]+(.+?)\s*$", text)
    for _, heading in headings:
        match = re.match(r"本期主问题[：:]\s*(.+)", heading)
        if match:
            return match[1].strip().rstrip("#").strip()
    for level, heading in headings:
        title = heading.strip().rstrip("#").strip()
        if level == "#" and title and not _PREFACE.match(title):
            return title
    return ""

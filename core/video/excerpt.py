"""One item's part of a discovery report: its section and the sources it cites.

A script written without researching its item (a config with 重新调研 turned off) is written from what the report says
about that item and nothing else, so the writer and the audits cannot draw on the other items. The report lists its
items as ``### 1.1 title`` followed by ``- **field**：value`` bullets, an item ends at the next heading, and the
sources are a numbered list under a heading that says 来源清单 that items cite as ``[1][3]``.
"""

from __future__ import annotations

import re
from typing import Optional

_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*$")
_NUMBERING = re.compile(r"^(?:[A-Z]{0,3}\d+(?:\.\d+)*)\s*[.．、:：]?\s+")
_SOURCE = re.compile(r"^\s*(\d+)\s*[.、)]\s")
_REF = re.compile(r"\[(\d+)\]")


def _title(heading: str) -> str:
    return _NUMBERING.sub("", heading.replace("**", "").strip()).strip()


def item_excerpt(report: str, title: str) -> Optional[str]:
    """The heading and body of the item called ``title``, then the sources it cites; None when no heading is it."""
    lines = report.splitlines()
    start = next((i for i, line in enumerate(lines)
                  if (m := _HEADING.match(line)) and _title(m.group(1)) == title.strip()), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if _HEADING.match(lines[i])), len(lines))
    body = lines[start:end]
    cited = {int(n) for n in _REF.findall("\n".join(body))}
    sources: dict[int, str] = {}
    in_sources = False
    for line in lines:
        heading = _HEADING.match(line)
        if heading:
            in_sources = "来源清单" in heading.group(1)
        elif in_sources and (m := _SOURCE.match(line)):
            sources[int(m.group(1))] = line.strip()
    out = "\n".join(body).rstrip()
    listed = [sources[n] for n in sorted(cited) if n in sources]
    if listed:
        out += "\n\n### 来源清单\n" + "\n".join(listed)
    return out + "\n"

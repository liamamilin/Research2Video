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
_SUMMARY = re.compile(r"^\s*[-*]\s*(?:\*\*)?一句话(?:\*\*)?\s*[：:]\s*(.*?)\s*$")


def _title(heading: str) -> str:
    return _NUMBERING.sub("", heading.replace("**", "").strip()).strip()


def item_excerpt(report: str, title: str, *, summary: str = "") -> Optional[str]:
    """Read an item's section and cited sources, using its frozen summary if its display title changed.

    A summary must match one section exactly; ambiguous or absent matches never return the whole report.
    """
    lines = report.splitlines()
    start = next((i for i, line in enumerate(lines)
                  if (m := _HEADING.match(line)) and _title(m.group(1)) == title.strip()), None)
    if start is None and summary.strip():
        normalize = lambda value: " ".join(value.split())
        wanted = normalize(summary)
        matches = set()
        heading = None
        for i, line in enumerate(lines):
            if _HEADING.match(line):
                heading = i
            elif heading is not None and (m := _SUMMARY.match(line)) and normalize(m.group(1)) == wanted:
                matches.add(heading)
        if len(matches) == 1:
            start = matches.pop()
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


def topic_for(column, found) -> dict:
    """The topic a script of ``column`` is made from for a report item. A column that does not research its item
    (``research_mode: none``) writes from the item's part of its report, so the topic carries the report and the item;
    the manual route and the unattended pipeline both build it here."""
    topic = found.as_dict()
    if column.research_mode == "none" and column.topic_kind == "single" and found.report_path:
        topic = {**topic, "from_report": found.report_path, "from_item": found.id,
                 "from_item_title": found.title}
    return topic

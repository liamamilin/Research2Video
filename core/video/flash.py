"""Flash-news topics from the discovery job's report (``jobs/monitoring/ai_flash_discovery.yaml``).

A report lists its items as ``### 1.1 title`` followed by ``- **field**：value`` bullets, and ends with a numbered
source list (``1. title | 一手/二手 | date | url``); an item cites sources as ``[1][3]`` or with the URLs inline, as
the intelligence radars do. The items become topics numbered ``F1``, ``F2`` ... in the order listed, so a topic's id
is ``<report date>/F<n>`` and it stays the same for as long as the report does.

The research prompt fills the same placeholders for every column, so the fields a flash item does not have under
their usual names are borrowed: the one-line summary is the core angle, the effect on developers is why anyone
cares, the action is the outline. The radar spelling of each field is accepted too.
"""

from __future__ import annotations

import os
import re

from .topics import Topic

FLASH_DIR = "flash"
_REPORT = re.compile(r"^(\d{4}-\d{2}-\d{2})_ai_flash_discovery\.md$")
_ITEM = re.compile(r"^#{2,4}\s*(\d+\.\d+)\s*[.．、:：]?\s*(.+?)\s*$")
_HEADING = re.compile(r"^#{1,4}\s")
_FIELD = re.compile(r"^\s*[-*]\s*\*\*(.+?)\*\*\s*[:：]\s*(.*)$")
_NESTED = re.compile(r"^\s{2,}[-*]\s+(.*)$")
_SOURCE = re.compile(r"^\s*(\d+)\s*[.．、)]\s*(.+)$")
_URL = re.compile(r"https?://[^\s)\]；;，,>]+")
_REF = re.compile(r"\[(\d+)\]")

#: placeholder field ← the names it may go by (the first one found is used when the field is missing)
ALIASES = {
    "核心角度": ("一句话", "变更内容"),
    "为什么有人在乎": ("对开发者的影响", "实际影响"),
    "建议大纲": ("可做的动作", "推荐行动"),
    "变化": ("旧/新情况",),
}
AUDIENCE = "开发者"


def _plain(text: str) -> str:
    return text.replace("**", "").replace("__", "").strip()


def _sources(lines: list[str]) -> dict[str, dict]:
    sources: dict[str, dict] = {}
    for line in lines:
        match = _SOURCE.match(line)
        if not match:
            continue
        url = _URL.search(match.group(2))
        if url is None:
            continue
        parts = [p.strip() for p in match.group(2).split("|")]
        sources[match.group(1)] = {"title": parts[0], "kind": parts[1] if len(parts) > 3 else "",
                                   "date": parts[2] if len(parts) > 3 else "", "url": url.group(0).rstrip(".")}
    return sources


def parse(report: str, report_date: str) -> list[Topic]:
    """The items of a discovery report, as topics, in the order the report lists them."""
    lines = report.splitlines()
    listed: list[str] = []
    in_sources = False
    for line in lines:
        if _HEADING.match(line):
            in_sources = "来源清单" in line
        elif in_sources:
            listed.append(line)
    sources = _sources(listed)

    topics: list[Topic] = []
    current: Topic | None = None
    last_field = ""
    for line in lines:
        heading = _ITEM.match(line)
        if heading:
            if current is not None:
                topics.append(current)
            number = len(topics) + 1
            current = Topic(id=f"{report_date}/F{number}", round=report_date, code=f"F{number}",
                            title=_plain(heading.group(2)))
            last_field = ""
            continue
        if current is None:
            continue
        if _HEADING.match(line):  # any other heading ends the item
            topics.append(current)
            current, last_field = None, ""
            continue
        field = _FIELD.match(line)
        if field:
            last_field = _plain(field.group(1))
            current.fields[last_field] = field.group(2).replace("**", "").strip()
            continue
        nested = _NESTED.match(line)
        if nested and last_field:
            current.fields[last_field] += "；" + nested.group(1).replace("**", "").strip()
    if current is not None:
        topics.append(current)
    for topic in topics:
        _complete(topic, sources)
    return topics


def _complete(topic: Topic, sources: dict[str, dict]) -> None:
    fields = topic.fields
    for name, aliases in ALIASES.items():
        if not fields.get(name):
            found = next((fields[a] for a in aliases if fields.get(a)), "")
            if found:
                fields[name] = found
    fields.setdefault("目标受众", AUDIENCE)
    cited = fields.get("证据来源", "")
    chosen = [sources[n] for n in dict.fromkeys(_REF.findall(cited)) if n in sources]
    urls = [s["url"] for s in chosen] or list(dict.fromkeys(u.rstrip(".") for u in _URL.findall(cited)))
    fields["来源网址"] = "\n".join(urls)
    fields["来源明细"] = "\n".join(
        " | ".join(p for p in (s["title"], s["kind"], s["date"], s["url"]) if p) for s in chosen)


def latest_report(output_dir: str) -> str | None:
    """Date of the newest discovery report."""
    base = os.path.join(output_dir, FLASH_DIR)
    if not os.path.isdir(base):
        return None
    dates = sorted((m.group(1) for name in os.listdir(base) if (m := _REPORT.match(name))), reverse=True)
    return dates[0] if dates else None


def report_path(output_dir: str, report_date: str) -> str:
    return os.path.join(output_dir, FLASH_DIR, f"{report_date}_ai_flash_discovery.md")


def load(output_dir: str, report_date: str | None = None) -> tuple[str | None, list[Topic]]:
    """A report's topics (the newest report when none is given)."""
    report_date = report_date or latest_report(output_dir)
    if report_date is None:
        return None, []
    try:
        with open(report_path(output_dir, report_date), encoding="utf-8") as fh:
            return report_date, parse(fh.read(), report_date)
    except OSError:
        return report_date, []

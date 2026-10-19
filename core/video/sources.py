"""The source list at the end of a research report, and the source card a short video ends on.

A report lists its sources as ``n. title | 一手/二手 | date | url``. AutoVideo's source card (``sources`` in the
narration request, UPSTREAM_INTEGRATION.md) takes 1–3 of ``{title, url, date}``, so the card shows what the script
cites: the sources its paragraphs cite most, first-hand ones before the rest. A source whose publish date the report
does not give is left out rather than shown with a guessed date.
"""

from __future__ import annotations

import re
from datetime import date

CARD_MAX = 3
TITLE_MAX = 40
SHORTEST_HEAD = 8

_SOURCE = re.compile(r"^\s*(\d+)\s*[.．、)]\s*(.+)$")
_URL = re.compile(r"https?://[^\s)\]；;，,>]+")
_HEADING = re.compile(r"^#{1,4}\s")
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_CHECKED = re.compile(r"检索于\s*(\d{4})-(\d{2})-(\d{2})")
_TRAILING_NOTE = re.compile(r"\s*[（(][^（()）]*[)）]\s*$")


def parse_list(lines: list[str]) -> dict[str, dict]:
    """``number → {title, kind, date, url}`` for each numbered line that carries a URL."""
    sources: dict[str, dict] = {}
    for line in lines:
        match = _SOURCE.match(line)
        if not match:
            continue
        url = _URL.search(match.group(2))
        if url is None:
            continue
        parts = [p.strip() for p in match.group(2).split("|")]
        kind, date_note = _kind_and_date(parts)
        sources[match.group(1)] = {"title": parts[0], "kind": kind, "date": date_note, "url": url.group(0).rstrip(".")}
    return sources


def _kind_and_date(parts: list[str]) -> tuple[str, str]:
    """``title | 一手/二手 | date | url`` or ``title | site | 一手/二手 | date | url``: the kind and the date note after it."""
    for index, part in enumerate(parts[1:], start=1):
        if part.startswith(("一手", "二手")):
            after = parts[index + 1] if index + 1 < len(parts) else ""
            return part, "" if _URL.search(after) else after
    return (parts[1], parts[2]) if len(parts) > 3 else ("", "")


def report_sources(report: str) -> list[dict]:
    """The report's source list (the section headed 「来源清单」), in the order listed."""
    listed: list[str] = []
    in_sources = False
    for line in report.splitlines():
        if _HEADING.match(line):
            in_sources = "来源清单" in line
        elif in_sources:
            listed.append(line)
    return list(parse_list(listed).values())


def _valid(match) -> str:
    try:
        date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return ""
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def _iso_date(raw: str) -> str:
    """The date to show for a source, as YYYY-MM-DD; empty when there is none.

    A date at the start of the note is the publish date. A page the report says is kept up to date
    (「持续更新，检索于 2026-10-17」) has no publish date, so the day it was checked stands in: the card then reads
    as of that day. A date elsewhere in the note (「随 Opus 5.5 于 2026-09-22 开放」) is about something else and is not used."""
    match = _ISO.match(raw.strip())
    if match:
        return _valid(match)
    if "持续更新" in raw:
        checked = _CHECKED.search(raw)
        if checked:
            return _valid(checked)
    return ""


def _title(raw: str) -> str:
    text = " ".join(_TRAILING_NOTE.sub("", raw).split())
    if len(text) <= TITLE_MAX:
        return text
    # 「Gemini 4 Argon: Built to solve …」 reads better cut at the colon than in the middle of a word
    for separator in (" — ", " – ", ": ", "：", " - ", " | "):
        head = text.split(separator)[0].strip()
        if SHORTEST_HEAD <= len(head) <= TITLE_MAX:
            return head
    return text[: TITLE_MAX - 1].rstrip() + "…"


def _key(url: str) -> str:
    return url.strip().rstrip("/")


def source_cards(version: dict, report: str, limit: int = CARD_MAX) -> list[dict]:
    """The ``sources`` for the video's source card: what the script cites, most-cited first-hand sources first."""
    listed = {_key(s["url"]): s for s in report_sources(report)}
    counts: dict[str, int] = {}
    for section in version.get("sections") or []:
        for paragraph in section.get("paragraphs") or []:
            for url in dict.fromkeys(paragraph.get("citations") or []):
                counts[_key(url)] = counts.get(_key(url), 0) + 1
    ranked = sorted((url for url in counts if url in listed and _iso_date(listed[url]["date"])),
                    key=lambda url: (not listed[url]["kind"].startswith("一手"), -counts[url]))
    return [{"title": _title(listed[url]["title"]) or url, "url": listed[url]["url"], "date": _iso_date(listed[url]["date"])}
            for url in ranked[:limit]]

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
        sources[match.group(1)] = {"title": parts[0], "kind": parts[1] if len(parts) > 3 else "",
                                   "date": parts[2] if len(parts) > 3 else "", "url": url.group(0).rstrip(".")}
    return sources


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


def _iso_date(raw: str) -> str:
    """The publish date a source line starts with, as YYYY-MM-DD; empty when it is not a real date.

    「未标注（检索于 …）」 is a retrieval date, not a publish date, so only a date at the start counts."""
    match = _ISO.match(raw.strip())
    if not match:
        return ""
    try:
        date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return ""
    return match.group(0)


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

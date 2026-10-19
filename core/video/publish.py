"""The publish pack of an approved script: title, description, chapters, tags and cover text, ready to paste.

Everything here is derived from what the pipeline already holds (the approved version, its research report, the
render's chapter timeline), so nothing is invented that the script does not say. The pack is a suggestion for the
person who uploads; nothing is posted anywhere.

Chapter lines are ``MM:SS 标题`` (``H:MM:SS`` past an hour), the first at 00:00. No official Bilibili page for this
could be found (2026-10-19): the rules used (at least 3 chapters, at least 5 seconds apart, no special punctuation in
titles) come from third-party notes, and the pack says so. Paste once and check that the platform makes them links.
"""

from __future__ import annotations

import re
from typing import Optional

from . import sources

TITLE_MAX = 80
DESCRIPTION_MAX = 2000
TAGS_MAX = 10
TAG_LENGTH = 20
CHAPTERS_MIN = 3
CHAPTER_GAP_SECONDS = 5
CHAPTER_TITLE_MAX = 20
SUMMARY_MAX = 100

_CLAUSE_END = re.compile(r"[。！？!?；;]")
_TITLE_SPLIT = re.compile(r"[：:，,、｜|—\-·（）()《》「」\s]+")
_LATIN_NAME = re.compile(r"[A-Za-z][A-Za-z0-9.+-]{2,}")
_CHAPTER_CHARS = re.compile(r"[^0-9A-Za-z一-鿿 ]+")
CHAPTER_NOTE = ("章节格式（MM:SS 标题、首条 00:00、至少 3 条、间隔不少于 5 秒、标题不带特殊符号）来自第三方资料，"
                "没有找到 B 站官方说明；第一次发布时粘贴后确认是否变成可点击的章节。")


def clock(seconds: float) -> str:
    whole = int(seconds)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def chapter_lines(timeline: Optional[dict]) -> list[dict]:
    """``[{seconds, time, title}]`` from a render's packaged chapter timeline; empty when it has too few chapters."""
    if not timeline or timeline.get("timeline") != "packaged":
        return []
    out: list[dict] = []
    for chapter in timeline.get("chapters") or []:
        title = _CHAPTER_CHARS.sub(" ", str(chapter.get("title") or "")).strip()
        title = re.sub(r"\s+", " ", title)[:CHAPTER_TITLE_MAX].strip()
        start = max(float(chapter.get("start_ms") or 0), 0) / 1000
        if not title:
            continue
        if out and start - out[-1]["seconds"] < CHAPTER_GAP_SECONDS:
            continue
        out.append({"seconds": start, "title": title})
    if len(out) < CHAPTERS_MIN:
        return []
    out[0]["seconds"] = 0.0  # the intro belongs to the first chapter
    return [{"seconds": c["seconds"], "time": clock(c["seconds"]), "title": c["title"]} for c in out]


def _first_clause(text: str, limit: int) -> str:
    text = text.strip()
    match = _CLAUSE_END.search(text)
    clause = text[:match.start()] if match else text
    return clause if len(clause) <= limit else clause[:limit]


def summary(version: dict) -> str:
    """One or two sentences for the description's first line: the model's summary when the version has one."""
    given = ((version.get("meta") or {}).get("description") or {}).get("summary")
    if given:
        return str(given).strip()
    for section in version.get("sections") or []:
        for paragraph in section.get("paragraphs") or []:
            text = str(paragraph.get("text") or "").strip()
            if text:
                return _first_clause(text, SUMMARY_MAX) + "。"
    return ""


def tags(title: str, column, topic_title: str = "") -> list[str]:
    """Up to ten tags: the content type, the audience, product names from the title, then its keywords."""
    candidates = [column.name, column.audience, "AI"]
    candidates += _LATIN_NAME.findall(f"{title} {topic_title}")
    for source in (title, topic_title):  # phrases of the title; a price or a bare number is no tag
        candidates += [p for p in _TITLE_SPLIT.split(source)
                       if 2 <= len(p) <= TAG_LENGTH and re.match(r"[A-Za-z\u4e00-\u9fff]", p)][:2]
    out: list[str] = []
    for tag in candidates:
        tag = tag.strip()
        if tag and len(tag) <= TAG_LENGTH and tag.lower() not in {t.lower() for t in out}:
            out.append(tag)
    return out[:TAGS_MAX]


def cover_text(title: str, column) -> dict:
    """A short headline for the cover picture and the badge the column puts on its videos."""
    headline = re.split(r"[：:，,]", title)[0].strip() if len(title) > 14 else title
    headline = headline[:14]
    return {"headline": headline, "badge": (column.package or {}).get("badge") or column.name}


def reference_lines(version: dict, report: str) -> list[dict]:
    """The sources the script cites, as ``{title, url, date}`` in the report's order; a source with no date has ''."""
    cited = {sources._key(u) for s in version.get("sections") or [] for p in s.get("paragraphs") or []
             for u in p.get("citations") or []}
    out = []
    for entry in sources.report_sources(report):
        if sources._key(entry["url"]) in cited:
            out.append({"title": sources._title(entry["title"]) or entry["url"], "url": entry["url"],
                        "date": sources._iso_date(entry["date"])})
    return out


def build(script: dict, version: dict, column, report: str, timeline: Optional[dict]) -> dict:
    """The pack for ``version``. ``report`` may be empty (then no references); ``timeline`` is a render's chapters.json."""
    title = str(version.get("title") or "").strip()
    chapters = chapter_lines(timeline)
    meta_description = (version.get("meta") or {}).get("description") or {}
    refs = reference_lines(version, report)

    def assemble(with_extra: bool) -> str:
        parts = [summary(version)]
        if chapters:
            parts.append("\n".join(f"{c['time']} {c['title']}" for c in chapters))
        if with_extra:
            if meta_description.get("details"):
                parts.append("数字和口径：\n" + "\n".join(f"· {d}" for d in meta_description["details"]))
            if meta_description.get("open_questions"):
                parts.append("还没确定：\n" + "\n".join(f"· {q}" for q in meta_description["open_questions"]))
        if refs:
            parts.append("参考来源：\n" + "\n".join(
                f"· {r['title']}{('（' + r['date'] + '）') if r['date'] else ''} {r['url']}" for r in refs))
        return "\n\n".join(p for p in parts if p)

    description = assemble(True)
    if len(description) > DESCRIPTION_MAX:
        description = assemble(False)

    warnings: list[str] = []
    if script.get("status") != "APPROVED" or script.get("approved_version") != version.get("version"):
        warnings.append("这一版还没有定稿，发布包按它现在的样子生成。")
    if len(title) > TITLE_MAX:
        warnings.append(f"标题有 {len(title)} 个字，超过 {TITLE_MAX}。")
    if len(description) > DESCRIPTION_MAX:
        warnings.append(f"简介有 {len(description)} 个字，超过 {DESCRIPTION_MAX}，请删减。")
    serious = ((version.get("checks") or (version.get("meta") or {}).get("checks") or {}).get("summary") or {}).get("serious")
    if serious:
        warnings.append(f"审稿还有 {serious} 个严重的事实问题没有处理。")
    if timeline is None:
        warnings.append("还没有出片，所以没有章节时间。")
    elif not chapters:
        warnings.append(f"视频不足 {CHAPTERS_MIN} 个章节，简介里不放章节。")
    if report and not refs:
        warnings.append("稿子没有引用报告里列出的来源，简介没有参考来源。")
    dateless = [r for r in refs if not r["date"]]
    if dateless:
        warnings.append(f"{len(dateless)} 个来源没有发布日期，简介里只写了网址。")

    return {
        "title": title,
        "description": description,
        "chapters": chapters,
        "chapter_note": CHAPTER_NOTE if chapters else "",
        "tags": tags(title, column, (script.get("topic") or {}).get("title", "")),
        "cover": cover_text(title, column),
        "references": refs,
        "warnings": warnings,
        "version": version.get("version"),
    }

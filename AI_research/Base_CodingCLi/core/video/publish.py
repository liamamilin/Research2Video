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

from core.provenance import extract_urls
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


def _first_sentence(text: str, limit: int) -> str:
    """The text up to its first full stop, ended with 。; when that is longer than ``limit``, cut at the last comma
    that fits (or at ``limit`` with … when there is none): never a sentence chopped mid-word and then closed."""
    text = text.strip()
    match = _CLAUSE_END.search(text)
    sentence = text[:match.start()] if match else text
    if len(sentence) <= limit:
        return sentence + "。"
    head = sentence[:limit]
    comma = max(head.rfind("，"), head.rfind("、"), head.rfind(","))
    return head[:comma] + "。" if comma > limit // 2 else head + "…"


def summary(version: dict) -> str:
    """One or two sentences for the description's first line: the model's summary when the version has one."""
    given = ((version.get("meta") or {}).get("description") or {}).get("summary")
    if given:
        return str(given).strip()
    for section in version.get("sections") or []:
        for paragraph in section.get("paragraphs") or []:
            text = str(paragraph.get("text") or "").strip()
            if text:
                return _first_sentence(text, SUMMARY_MAX)
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


_WORD_CHAR = re.compile(r"[A-Za-z0-9.\-_/]")


def cover_text(title: str, column) -> dict:
    """A short headline for the cover picture and the badge the column puts on its videos."""
    headline = re.split(r"[：:，,]", title)[0].strip() if len(title) > 14 else title
    if len(headline) > 14:
        cut = 14
        while cut > 0 and _WORD_CHAR.match(headline[cut - 1]) and _WORD_CHAR.match(headline[cut]):
            cut -= 1  # never cut a model name or a number in half (GPT-4o)
        headline = headline[:cut].strip() or headline[:14]
    packaging = getattr(column, "packaging", None) or {}
    badge = packaging.get("badge", {}).get("text") or packaging.get("label") or (column.package or {}).get("badge") or column.name
    return {"headline": headline, "badge": badge}


def reference_lines(version: dict, report: str) -> list[dict]:
    """Attach the report's source material, recording which links the script explicitly cites."""
    article = (version.get("meta") or {}).get("article")
    if article:
        source = article["source"]
        return ([{"title": source["title"], "url": source["source_url"], "date": source.get("published_on", ""),
                  "kind": "用户提供的文章，未做外部事实核验", "cited": True}]
                if source.get("source_url") else [])
    from core import report_material

    meta = version.get("meta") or {}
    original = report_material.read(meta.get("material") or {}, meta.get("report_path") or "")
    cited = {sources._key(u) for s in version.get("sections") or [] for p in s.get("paragraphs") or []
             for u in p.get("citations") or []}
    entries = sources.report_sources(report, fallback=True)
    listed = {sources._key(entry["url"]) for entry in entries}
    for entry in sources.report_sources(original, fallback=True):
        key = sources._key(entry["url"])
        if key in cited and key not in listed:
            entries.append(entry)
            listed.add(key)
    # A paragraph may cite a real report URL that the bibliography did not repeat. Keep that source too;
    # citations absent from the report are never promoted into report material here.
    for url in extract_urls(report):
        key = sources._key(url)
        if key in cited and key not in listed:
            entries.append({"title": url, "url": url, "date": "", "kind": ""})
            listed.add(key)
    out = [{"title": sources._label(entry["title"]) or entry["url"], "url": entry["url"],
            "date": sources._iso_date(entry["date"]), "kind": entry.get("kind") or "",
            "cited": sources._key(entry["url"]) in cited} for entry in entries]
    return sorted(out, key=lambda entry: not entry["cited"])


def build(script: dict, version: dict, column, report: str, timeline: Optional[dict],
          freshness: Optional[dict] = None) -> dict:
    """The pack for ``version``. ``report`` may be empty (then no references); ``timeline`` is a render's chapters.json."""
    title = str(version.get("title") or "").strip()
    chapters = chapter_lines(timeline)
    meta_description = (version.get("meta") or {}).get("description") or {}
    refs = reference_lines(version, report)
    article = (version.get("meta") or {}).get("article")
    article_credit = ""
    if article:
        article_credit = "原文朗读" if article["mode"] == "read" else "根据文章改编"
        if article["source"].get("author"):
            article_credit += " · 原文作者：" + article["source"]["author"]

    def assemble(with_extra: bool) -> str:
        parts = [summary(version)]
        if article_credit:
            parts.append(article_credit)
        if chapters:
            parts.append("\n".join(f"{c['time']} {c['title']}" for c in chapters))
        if with_extra:
            if meta_description.get("details"):
                parts.append("数字和口径：\n" + "\n".join(f"· {d}" for d in meta_description["details"]))
            if meta_description.get("open_questions"):
                parts.append("还没确定：\n" + "\n".join(f"· {q}" for q in meta_description["open_questions"]))
        for used, heading in ((True, "参考来源（文稿引用）："), (False, "报告补充资料：")):
            group = [r for r in refs if r["cited"] == used]
            if group:
                parts.append(heading + "\n" + "\n".join(
                    f"· {r['title']}{('（' + r['date'] + '）') if r['date'] else ''} {r['url']}" for r in group))
        return "\n\n".join(p for p in parts if p)

    description = assemble(True)
    if len(description) > DESCRIPTION_MAX:
        description = assemble(False)

    warnings: list[str] = []
    if script.get("approved_version") != version.get("version"):
        warnings.append("这一版还没有定稿，发布包按它现在的样子生成。")
    elif script.get("current_version") and script["current_version"] != version.get("version"):
        warnings.append(f"发布包对应已定稿旧版 v{version['version']}，当前文稿为 v{script['current_version']}。")
    if len(title) > TITLE_MAX:
        warnings.append(f"标题有 {len(title)} 个字，超过 {TITLE_MAX}。")
    if len(description) > DESCRIPTION_MAX:
        warnings.append(f"简介有 {len(description)} 个字，超过 {DESCRIPTION_MAX} 的参考长度；资料链接已完整保留，可按平台要求编辑。")
    serious = ((version.get("checks") or (version.get("meta") or {}).get("checks") or {}).get("summary") or {}).get("serious")
    if serious:
        warnings.append(f"审稿还有 {serious} 个严重的事实问题没有处理。")
    if timeline is None:
        warnings.append("还没有出片，所以没有章节时间。")
    elif not chapters:
        warnings.append(f"视频不足 {CHAPTERS_MIN} 个章节，简介里不放章节。")
    if not refs:
        warnings.append("文章未提供来源链接；未生成来源卡，也未进行外部事实核验。" if (version.get("meta") or {}).get("article")
                        else "报告中没有找到可提取的资料链接。" if report else "研究报告暂时无法读取，未能附上资料来源。")
    dateless = [r for r in refs if not r["date"]]
    if dateless:
        warnings.append(f"{len(dateless)} 个来源没有发布日期，简介里只写了网址。")

    warnings += (freshness or {}).get("warnings") or []
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
        "freshness": freshness or {"required": False, "warnings": []},
        "content": {
            # Keep an entire paragraph when no separately authored summary is
            # available. The first sentence may omit a following qualification.
            "required": [str(meta_description.get("summary") or next((p["text"] for s in version.get("sections") or []
                         for p in s.get("paragraphs") or [] if p.get("text")), "")).strip(),
                         *([article_credit] if article_credit else []),
                         *(["数字和口径：\n" + "\n".join(f"· {d}" for d in meta_description["details"])] if meta_description.get("details") else []),
                         *(["还没确定：\n" + "\n".join(f"· {q}" for q in meta_description["open_questions"])] if meta_description.get("open_questions") else [])],
            "chapters": "\n".join(f"{c['time']} {c['title']}" for c in chapters),
        },
    }

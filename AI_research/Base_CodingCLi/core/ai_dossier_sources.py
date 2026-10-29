"""Read existing research links for examples and process detail in migrated columns.

This runs after the ordinary researcher has chosen and organized candidates.
It reuses public-page adapters and never issues a new search query.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import re

from core.errors import CancelledError
from core.provenance import extract_urls, normalize_url
from core.search import SearchClient, SearchResult
from core.video.research_dossier import PRODUCTIONS

MAX_PAGES = 3
PAGE_SECONDS = 15
PAGE_CHARS = 24000
_DETAIL = re.compile(r"example|quick.?start|usage|how.?to|getting.?started|architect|method|result|workflow|experiment|observation|mechanism|示例|用法|过程|方法|实验|观测|机制", re.I)


def selected_urls(report: str, supplied: str) -> list[str]:
    """Follow the researcher's ordering, within this run's existing material."""
    known = {normalize_url(u) for u in extract_urls(supplied)}
    chosen = []
    for url in extract_urls(report):
        key = normalize_url(url)
        if key in known and key not in {normalize_url(u) for u in chosen}:
            chosen.append(url)
        if len(chosen) == MAX_PAGES:
            break
    return chosen


def detail_sections(sections: list[SearchResult]) -> list[SearchResult]:
    """Prefer examples and methods, keeping selected sections whole and ordered."""
    if not sections:
        return []
    priority = list(dict.fromkeys([0] + [i for i, s in enumerate(sections) if _DETAIL.search(s.title)]
                                  + [len(sections) - 1] + list(range(len(sections)))))
    kept, used = [], 0
    for i in priority:
        size = len(sections[i].to_markdown())
        if used + size <= PAGE_CHARS:
            kept.append(i)
            used += size
    return [sections[i] for i in sorted(kept)]


def enrich(job: dict, report: str, supplied: str, *, search_client=None, cancel_token=None,
           progress_cb=None, fetch=None) -> tuple[str, dict]:
    """Return additional source text and a small run receipt; keep failed attempts."""
    if job.get("_file") not in PRODUCTIONS.values():
        return "", {}
    if fetch is None:
        from core.sources import fetch_source
        fetch = fetch_source
    urls = selected_urls(report, supplied)
    if progress_cb and urls:
        progress_cb({"type": "phase", "phase": "reading_dossier_sources"})

    def read(url):
        if cancel_token and cancel_token.is_set():
            raise CancelledError("Cancelled before reading dossier source")
        try:
            sections = fetch("page:" + url, limit=24, timeout=PAGE_SECONDS, search_client=search_client)
            kept = detail_sections(sections)
            text = SearchClient.format_results(kept) if kept else ""
            return text, {"url": url, "status": "read" if text else "empty", "sections": len(kept),
                          "available_sections": len(sections), "chars": len(text)}
        except CancelledError:
            raise
        except Exception as exc:
            return "", {"url": url, "status": "unavailable", "error": str(exc)[:300]}

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(read, urls))
    text = "\n\n---\n\n".join(part for part, _ in results if part)
    return text, {"version": 1, "pages": [r for _, r in results], "extra_search_queries": 0,
                  "page_limit": MAX_PAGES, "chars": len(text)}

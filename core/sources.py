"""Subscription source adapters (RSS/Atom, GitHub Releases, arXiv, HN, TLDR, Hugging Face, Artificial Analysis).

Each adapter returns :class:`core.search.SearchResult` items so they merge
with web-search results. Specs are strings:

* ``rss:https://example.com/feed.xml``  (also ``feed:``)
* ``github:owner/repo``
* ``arxiv:query``
* ``hn:query``
* ``tldr:ai``              the latest TLDR AI issues (no issue on weekends: the last few days are tried)
* ``hf_papers:daily``      Hugging Face daily papers
* ``hf_models:trending``   Hugging Face trending models
* ``gh_new:keywords``      GitHub repositories created in the last 7 days matching the keywords, by stars
* ``aa:models``            Artificial Analysis model leaderboard (intelligence, price, speed)

A page that answers 200 but yields no items is a failure, not "no news": ``fetch_all_strict`` reports it, and raises
``SourcesUnavailableError`` when nothing at all came back.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Optional

from .errors import SourcesUnavailableError
from .search import SearchClient, SearchResult
import datetime

logger = logging.getLogger(__name__)

_UA = "BaseCodingCLi-ResearchAgent/2.0"
_ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}
_TAG_RE = re.compile(r"<[^>]+>")


def _get_text(url: str, timeout: int, accept: str = "*/*") -> str:
    request = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": accept})
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _get_json(url: str, timeout: int) -> dict:
    return json.loads(_get_text(url, timeout, accept="application/json"))


def _clean(text: Optional[str], limit: int = 400) -> str:
    text = html.unescape(_TAG_RE.sub(" ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _parse_feed(xml_text: str, limit: int) -> list[SearchResult]:
    """Parse RSS 2.0 or Atom into SearchResult items."""
    root = ET.fromstring(xml_text)
    results: list[SearchResult] = []

    for item in root.findall(".//item")[:limit]:
        results.append(SearchResult(
            title=_clean(item.findtext("title"), 200),
            url=(item.findtext("link") or "").strip(),
            excerpts=[_clean(item.findtext("description"))] if item.findtext("description") else [],
            publish_date=(item.findtext("pubDate") or "").strip(),
            provider="rss",
        ))
    if results:
        return results

    for entry in root.findall(".//a:entry", _ATOM_NS)[:limit]:
        link = ""
        for node in entry.findall("a:link", _ATOM_NS):
            if node.get("rel", "alternate") == "alternate" and node.get("href"):
                link = node.get("href")
                break
        summary = entry.findtext("a:summary", default="", namespaces=_ATOM_NS)
        results.append(SearchResult(
            title=_clean(entry.findtext("a:title", default="", namespaces=_ATOM_NS), 200),
            url=link.strip(),
            excerpts=[_clean(summary)] if summary else [],
            publish_date=(entry.findtext("a:updated", default="", namespaces=_ATOM_NS)
                          or entry.findtext("a:published", default="", namespaces=_ATOM_NS)).strip(),
            provider="rss",
        ))
    return results


def fetch_rss(url: str, limit: int = 8, timeout: int = 20) -> list[SearchResult]:
    return _parse_feed(_get_text(url, timeout, accept="application/rss+xml, application/xml"), limit)


def fetch_github_releases(repo: str, limit: int = 8, timeout: int = 20) -> list[SearchResult]:
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"User-Agent": _UA, "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"https://api.github.com/repos/{repo}/releases?per_page={max(1, min(limit, 30))}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        releases = json.loads(resp.read().decode("utf-8", "replace"))

    results: list[SearchResult] = []
    for rel in releases[:limit]:
        name = rel.get("name") or rel.get("tag_name") or ""
        results.append(SearchResult(
            title=_clean(f"{repo} {name}".strip(), 200),
            url=rel.get("html_url") or f"https://github.com/{repo}/releases",
            excerpts=[_clean(rel.get("body"))] if rel.get("body") else [],
            publish_date=(rel.get("published_at") or "")[:10],
            provider="github",
        ))
    return results


def fetch_arxiv(query: str, limit: int = 8, timeout: int = 20) -> list[SearchResult]:
    params = urllib.parse.urlencode({
        "search_query": f"all:{query}",
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": max(1, min(limit, 30)),
    })
    xml_text = _get_text(f"http://export.arxiv.org/api/query?{params}", timeout)
    results = _parse_feed(xml_text, limit)
    for item in results:
        item.provider = "arxiv"
    return results


def fetch_hn(query: str, limit: int = 8, timeout: int = 20) -> list[SearchResult]:
    params = urllib.parse.urlencode({
        "query": query,
        "tags": "story",
        "hitsPerPage": max(1, min(limit, 30)),
    })
    data = _get_json(f"https://hn.algolia.com/api/v1/search_by_date?{params}", timeout)
    results: list[SearchResult] = []
    for hit in (data.get("hits") or [])[:limit]:
        results.append(SearchResult(
            title=_clean(hit.get("title") or hit.get("story_title"), 200),
            url=hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
            excerpts=[_clean(hit.get("story_text"))] if hit.get("story_text") else [],
            publish_date=(hit.get("created_at") or "")[:10],
            provider="hn",
        ))
    return results


_BROWSER_UA = "Mozilla/5.0 (compatible; BaseCodingCLi/2.0)"  # some sites refuse an unknown agent string


def _get_page(url: str, timeout: int, accept: str = "text/html,application/json") -> str:
    request = urllib.request.Request(url, headers={"User-Agent": _BROWSER_UA, "Accept": accept})
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


_TLDR_ARTICLE = re.compile(
    r'<article[^>]*>\s*<a[^>]*href="(?P<url>[^"]+)"[^>]*>\s*<h3[^>]*>(?P<title>.*?)</h3>\s*</a>'
    r'\s*<div class="newsletter-html">(?P<body>.*?)</div>', re.S)
_TLDR_SPONSOR = ("(sponsor)", "tldrnewsletter.com", "doubleclick.net")


def parse_tldr(page: str, issue_date: str) -> list[SearchResult]:
    """One TLDR issue: each article is a link, a title and a short summary. Sponsors are left out."""
    results = []
    for match in _TLDR_ARTICLE.finditer(page):
        url = html.unescape(match.group("url")).strip()
        title = _clean(match.group("title"), 200)
        if any(mark in title.lower() or mark in url.lower() for mark in _TLDR_SPONSOR):
            continue
        results.append(SearchResult(
            title=title, url=url.split("?utm_source", 1)[0], excerpts=[_clean(match.group("body"), 700)],
            publish_date=issue_date, provider="tldr"))
    return results


def fetch_tldr(edition: str, limit: int = 8, timeout: int = 20) -> list[SearchResult]:
    """The latest TLDR issues of an edition (``ai``): up to 3 issues from the last 7 days, newest first. A day
    without an issue (weekend, holiday) answers 404 and is skipped; ``limit`` caps the items per issue."""
    results: list[SearchResult] = []
    issues = 0
    today = datetime.date.today()
    for back in range(8):
        day = (today - datetime.timedelta(days=back)).isoformat()
        try:
            page = _get_page(f"https://tldr.tech/{edition}/{day}", timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue
            raise
        items = parse_tldr(page, day)
        if not items:  # today's page before the issue is out; a redesign empties every day and ends in the error below
            continue
        results.extend(items[: max(limit, 1)])
        issues += 1
        if issues == 3:
            break
    if not issues:
        raise ValueError(f"tldr {edition}: 最近 7 天没有找到任何一期（没有页面，或页面里解析不到条目，页面结构可能变了）")
    return results


def parse_hf_papers(data: list, limit: int) -> list[SearchResult]:
    results = []
    for item in data[:limit]:
        paper = item.get("paper") or {}
        paper_id = paper.get("id") or ""
        if not paper_id:
            continue
        meta = [f"upvotes {paper.get('upvotes')}" if paper.get("upvotes") is not None else "",
                f"GitHub {paper['githubRepo']}" if paper.get("githubRepo") else ""]
        summary = paper.get("ai_summary") or item.get("summary") or paper.get("summary") or ""
        results.append(SearchResult(
            title=_clean(item.get("title") or paper.get("title"), 200), url=f"https://huggingface.co/papers/{paper_id}",
            excerpts=[_clean(" ".join(m for m in meta if m) + ". " + summary, 700)],
            publish_date=(item.get("publishedAt") or paper.get("publishedAt") or "")[:10], provider="hf_papers"))
    return results


def fetch_hf_papers(target: str, limit: int = 8, timeout: int = 20) -> list[SearchResult]:
    data = json.loads(_get_page(f"https://huggingface.co/api/daily_papers?limit={max(1, min(limit, 50))}", timeout))
    return parse_hf_papers(data, limit)


def parse_hf_models(data: list, limit: int) -> list[SearchResult]:
    results = []
    for item in data[:limit]:
        model_id = item.get("id") or ""
        if not model_id:
            continue
        tags = [t for t in item.get("tags") or [] if t.startswith("license:")]
        facts = [f"{item.get('pipeline_tag') or 'unknown task'}", f"downloads {item.get('downloads')}",
                 f"likes {item.get('likes')}", f"trending score {item.get('trendingScore')}", *tags]
        results.append(SearchResult(
            title=model_id, url=f"https://huggingface.co/{model_id}", excerpts=[", ".join(str(f) for f in facts)],
            publish_date=(item.get("createdAt") or "")[:10], provider="hf_models"))
    return results


def fetch_hf_models(target: str, limit: int = 8, timeout: int = 20) -> list[SearchResult]:
    data = json.loads(_get_page(
        f"https://huggingface.co/api/models?sort=trendingScore&limit={max(1, min(limit, 50))}", timeout))
    return parse_hf_models(data, limit)


def parse_gh_search(data: dict, limit: int) -> list[SearchResult]:
    results = []
    for repo in (data.get("items") or [])[:limit]:
        facts = [f"{repo.get('stargazers_count')} stars", repo.get("language") or "", ", ".join(repo.get("topics") or [])]
        results.append(SearchResult(
            title=repo.get("full_name") or "", url=repo.get("html_url") or "",
            excerpts=[_clean(f"{repo.get('description') or ''} ({'; '.join(f for f in facts if f)})", 500)],
            publish_date=(repo.get("created_at") or "")[:10], provider="gh_new"))
    return results


def fetch_gh_new(keywords: str, limit: int = 8, timeout: int = 20) -> list[SearchResult]:
    since = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
    query = urllib.parse.urlencode({"q": f"{keywords} created:>{since}", "sort": "stars", "order": "desc",
                                    "per_page": max(1, min(limit, 30))})
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"User-Agent": _UA, "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"https://api.github.com/search/repositories?{query}", headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return parse_gh_search(json.loads(resp.read().decode("utf-8", "replace")), limit)


_AA_ROW = re.compile(r"<tr.*?</tr>", re.S)
_AA_CELL = re.compile(r"<td.*?</td>", re.S)


def parse_aa_models(page: str, limit: int) -> list[SearchResult]:
    """The leaderboard table: model, context window, creator, intelligence index, cost per task, tokens/s, latency."""
    results = []
    for row in _AA_ROW.findall(page):
        cells = [_clean(c, 120) for c in _AA_CELL.findall(row)]
        if len(cells) < 6 or not cells[0]:
            continue
        model, context, creator, index, cost, speed = cells[:6]
        results.append(SearchResult(
            title=f"{model} ({creator})", url="https://artificialanalysis.ai/leaderboards/models",
            excerpts=[f"context window {context}; Artificial Analysis Intelligence Index {index}; "
                      f"cost per task {cost}; median {speed} tokens/s"
                      + (f"; latency to first chunk {cells[6]} s" if len(cells) > 6 else "")
                      + (f"; total response time {cells[7]} s" if len(cells) > 7 else "")],
            publish_date=datetime.date.today().isoformat(), provider="aa"))
        if len(results) >= limit:
            break
    return results


def fetch_aa_models(target: str, limit: int = 8, timeout: int = 30) -> list[SearchResult]:
    return parse_aa_models(_get_page("https://artificialanalysis.ai/leaderboards/models", timeout), limit)


_ADAPTERS = {
    "rss": fetch_rss,
    "feed": fetch_rss,
    "github": fetch_github_releases,
    "arxiv": fetch_arxiv,
    "hn": fetch_hn,
    "tldr": fetch_tldr,
    "hf_papers": fetch_hf_papers,
    "hf_models": fetch_hf_models,
    "gh_new": fetch_gh_new,
    "aa": fetch_aa_models,
}


def fetch_source(spec: str, limit: int = 8, timeout: int = 20) -> list[SearchResult]:
    """Fetch one source spec (``kind:target``)."""
    kind, _, target = (spec or "").partition(":")
    kind = kind.strip().lower()
    target = target.strip()
    adapter = _ADAPTERS.get(kind)
    if not adapter or not target:
        logger.warning("Unknown source spec: %s", spec)
        return []
    return adapter(target, limit=limit, timeout=timeout)


def fetch_all(sources: list[str], limit_per_source: int = 8,
              timeout: int = 20) -> list[SearchResult]:
    """Fetch every source spec, dedupe by URL, keep first-seen order."""
    collected: list[SearchResult] = []
    for spec in sources or []:
        try:
            collected.extend(fetch_source(spec, limit=limit_per_source, timeout=timeout))
        except Exception as exc:  # noqa: BLE001 - one bad source must not break the run
            logger.warning("Source %s failed: %s", spec, str(exc)[:200])
    return SearchClient._dedupe(collected)


def fetch_all_strict(sources: list[str], limit_per_source: int = 8,
                     timeout: int = 20) -> tuple[list[SearchResult], dict[str, object]]:
    """Fetch every source spec for a Job that has no web search behind it.

    Returns ``(items, stats)``; ``stats`` maps each spec to its item count, or to ``"失败：原因"``. A source that
    answers but gives no items counts as failed. Raises ``SourcesUnavailableError`` when nothing came back from any
    source, so the run stops instead of writing from the model's memory."""
    collected: list[SearchResult] = []
    stats: dict[str, object] = {}
    failures: dict[str, str] = {}
    for spec in sources or []:
        try:
            items = fetch_source(spec, limit=limit_per_source, timeout=timeout)
            if not items:
                raise ValueError("抓到了页面但没有条目")
        except Exception as exc:  # noqa: BLE001 - recorded per source; only "all failed" stops the run
            failures[spec] = str(exc)[:200]
            stats[spec] = f"失败：{failures[spec]}"
            logger.warning("Source %s failed: %s", spec, failures[spec])
            continue
        stats[spec] = len(items)
        collected.extend(items)
    if not collected:
        raise SourcesUnavailableError(failures or {"(无)": "Job 没有声明任何订阅源"})
    return SearchClient._dedupe(collected), stats

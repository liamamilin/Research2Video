"""Read public pages and tutorial directories as research material."""
from __future__ import annotations

import html
import re
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, NavigableString, Tag

from .errors import CancelledError
from .search import SearchClient, SearchResult

MAX_SECTION_CHARS = 6000


def canonical_url(url: str) -> str:
    parts = urlsplit(url)
    query = urlencode([(key, value) for key, value in parse_qsl(parts.query)
                       if not key.lower().startswith("utm_") and key not in {"trk", "autoRsvp"}])
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), query, ""))


def _document(page: str, *, catalog: bool = False):
    soup = BeautifulSoup(page, "html.parser")
    for node in soup.select("script,style,nav,footer,aside,svg,noscript,[hidden]"):
        node.decompose()
    root = (soup.find("main") if catalog else soup.find("article")) or soup.find("main") or soup.body or soup
    title = soup.find("h1") or soup.title
    return root, title.get_text(" ", strip=True) if title else ""


def _date(text: str) -> str | None:
    from datetime import datetime

    match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", text)
    if match:
        return match.group()
    match = re.search(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", text)
    if match:
        return "{}-{:02d}-{:02d}".format(int(match[1]), int(match[2]), int(match[3]))
    match = re.search(r"\b([A-Z][a-z]{2,8})\s+(\d{1,2}),?\s+(\d{4})\b", text)
    if match:
        for fmt in ("%B %d %Y", "%b %d %Y"):
            try:
                return datetime.strptime(" ".join(match.groups()), fmt).date().isoformat()
            except ValueError:
                continue
    return None


def parse_page(page: str, url: str, limit: int = 8) -> list[SearchResult]:
    """Keep heading sections, dates and links; discard navigation and scripts."""
    root, title = _document(page)
    text = root.get_text(" ", strip=True)
    if len(text) < 40 or re.search(r"^(just a moment|access denied|verify you are human)", text, re.I):
        raise ValueError("网页没有可读取的正文")
    results: list[SearchResult] = []
    heading, anchor, date, chunks, links = title, "", None, [], []

    def flush():
        body = re.sub(r"\s+", " ", " ".join(chunks)).strip()
        if len(body) < 30:
            return
        target = url + ("#" + quote(anchor, safe="-_") if anchor else
                        "#:~:text=" + quote(heading, safe=""))
        excerpt = body[:MAX_SECTION_CHARS]
        if links:
            excerpt += "\n相关页面：" + "；".join(dict.fromkeys(links))[:1500]
        results.append(SearchResult(title=f"{title} · {heading}" if heading != title else title,
                                    url=target, publish_date=date, excerpts=[excerpt], provider="page"))

    for node in root.descendants:
        if isinstance(node, Tag) and re.fullmatch(r"h[1-4]", node.name or ""):
            flush()
            heading, anchor = node.get_text(" ", strip=True), str(node.get("id") or "")
            date = _date(heading) or date
            chunks, links = [], []
        elif isinstance(node, Tag) and node.name == "a" and node.get("href"):
            link = urljoin(url, str(node["href"]))
            if urlsplit(link).scheme in {"https", "http"}:
                links.append(link)
        elif isinstance(node, NavigableString) and not node.find_parent(re.compile(r"^h[1-4]$")):
            chunks.append(str(node))
    flush()
    return results[:max(1, limit)]


def parse_catalog(page: str, url: str, limit: int = 8) -> list[SearchResult]:
    """Find content cards in a directory, including official videos hosted on YouTube."""
    root, _ = _document(page, catalog=True)
    results, seen = [], set()
    for link in root.select("a[href]"):
        target = canonical_url(urljoin(url, str(link["href"])))
        label = link.get_text(" ", strip=True)
        path = urlsplit(target).path
        if (urlsplit(target).scheme not in {"https", "http"} or target in seen or
                target == canonical_url(url) or len(label) < 12 or
                label.lower() in {"skip to main content", "view all videos", "go to all courses"} or
                re.search(r"/(login|signin|sign-in|privacy|terms|signup)/?$", path)):
            continue
        # Directory entries link to articles, courses, events, episodes or videos, not site-wide navigation.
        if not (link.find(["h2", "h3", "h4"]) or re.search(
                r"/(videos?/|watch|stories/|index/|articles/|public/|courses/|podcast/|episode/|examples/)", path)):
            continue
        seen.add(target)
        body = label
        results.append(SearchResult(title=label[:220], url=target, publish_date=_date(body),
                                    excerpts=[body[:MAX_SECTION_CHARS]], provider="catalog"))
        if len(results) >= limit:
            break
    if not results:
        raise ValueError("目录页面没有解析到内容条目")
    return results


def fetch_page(url: str, limit: int, timeout: int, *, get_page, search_client: SearchClient | None = None,
               catalog: bool = False) -> list[SearchResult]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("网页来源需要不带账号密码的 HTTP 或 HTTPS URL")
    parser = parse_catalog if catalog else parse_page
    try:
        results = parser(get_page(url, timeout), url, limit)
        if not results:
            raise ValueError("网页没有解析到正文段落")
        return results
    except CancelledError:
        raise
    except Exception:
        if search_client is None:
            raise
        # Public pages may refuse simple HTTP clients. Read the exact URL through the configured content API.
        content = search_client.read_page(url)
        import markdown
        page = markdown.markdown(content.excerpts[0], extensions=["tables"])
        return parser("<main><h1>" + html.escape(content.title) + "</h1>" + page + "</main>", url, limit)

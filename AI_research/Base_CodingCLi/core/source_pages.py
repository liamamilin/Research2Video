"""Read public pages and tutorial directories as research material."""
from __future__ import annotations

import html
import logging
import re
import posixpath
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, NavigableString, Tag

from .errors import CancelledError
from .search import SearchClient, SearchResult

MAX_SECTION_CHARS = 6000
PAGE_SECTION_CHARS = 24000
TEASER_PROVIDERS = frozenset({"rss", "catalog", "tldr", "hn", "gh_new", "github", "arxiv", "hf_papers"})
DEFAULT_DETAIL_PAGES = 8
_PAID_QUOTA = re.compile(r"HTTP (401|402|429)\b")
logger = logging.getLogger(__name__)


def paid_reader_exhausted(exc: BaseException) -> bool:
    """True when the paid page reader is out of credits or rate-limited."""
    return bool(_PAID_QUOTA.search(str(exc)))


def canonical_url(url: str) -> str:
    parts = urlsplit(url)
    query = urlencode([(key, value) for key, value in parse_qsl(parts.query)
                       if not key.lower().startswith("utm_") and key not in {"trk", "autoRsvp"}])
    path = posixpath.normpath(parts.path) if parts.path else ""
    return urlunsplit((parts.scheme, parts.netloc, path.rstrip("/"), query, ""))


def _document(page: str, *, catalog: bool = False):
    # Official documentation also offers public Markdown endpoints. Keep their
    # headings and links instead of treating the entire document as one paragraph.
    if not re.search(r"<(?:!doctype|html|body|main|article|h[1-6]|p)\b", page, re.I):
        import markdown
        page = markdown.markdown(page, extensions=["tables"])
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


def parse_page(page: str, url: str, limit: int = 8, *, section_chars: int = MAX_SECTION_CHARS) -> list[SearchResult]:
    """Keep heading sections, dates and links; discard navigation and scripts."""
    root, title = _document(page)
    text = root.get_text(" ", strip=True)
    if len(text) < 40 or re.search(r"^(just a moment|access denied|verify you are human)", text, re.I):
        raise ValueError("网页没有可读取的正文")
    results: list[SearchResult] = []
    sections = {}
    heading, anchor, date, chunks, links = title, "", None, [], []
    year = ""

    def flush():
        body = re.sub(r"\s+", " ", " ".join(chunks)).strip()
        if len(body) < 30:
            return
        target = url + ("#" + quote(anchor, safe="-_") if anchor else
                        "#:~:text=" + quote(heading, safe=""))
        excerpt = body[:section_chars]
        if links:
            excerpt += "\n相关页面：" + "；".join(dict.fromkeys(links))[:1500]
        key = (target, date)
        if key in sections:
            # Several releases can share a date heading and text-fragment URL.
            # Preserve all their bodies before the archive indexes that URL.
            if excerpt not in sections[key].excerpts:
                sections[key].excerpts.append(excerpt)
        else:
            result = SearchResult(title=f"{title} · {heading}" if heading != title else title,
                                  url=target, publish_date=date, excerpts=[excerpt], provider="page")
            results.append(result)
            sections[key] = result

    for node in root.descendants:
        if isinstance(node, Tag) and re.fullmatch(r"h[1-4]", node.name or ""):
            flush()
            heading, anchor = node.get_text(" ", strip=True), str(node.get("id") or "")
            dated_year = re.search(r"\b(20\d{2})\b", heading)
            if dated_year:
                year = dated_year.group(1)
                date = _date(heading)
            else:
                # Changelogs often group “Oct 30” under “October, 2026”.
                date = _date(heading) or (_date(heading + " " + year) if year else None) or date
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
        readable_title = len(label) >= 12 or len(re.findall(r"[\u3400-\u9fff]", label)) >= 6
        if (urlsplit(target).scheme not in {"https", "http"} or target in seen or
                target == canonical_url(url) or not readable_title or
                label.lower() in {"skip to main content", "view all videos", "go to all courses"} or
                re.search(r"/(login|signin|sign-in|privacy|terms|signup)/?$", path)):
            continue
        # Directory entries link to articles, courses, events, episodes or videos, not site-wide navigation.
        if not (link.find(["h2", "h3", "h4"]) or re.search(
                r"/(videos?/|watch|stories/|index/|articles/|public/|courses/|podcast/|episode/|examples/|"
                r"news/|blogs?/|reports/|features/|publications/|press-releases/|collections?/|exhibitions?/)", path)
                or re.search(r"/(?:20\d{2}/\d{1,2}/|t20\d{6}_|20\d{6})", path)):
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


def _parse_with(parser, page: str, url: str, limit: int) -> list[SearchResult]:
    results = parser(page, url, limit)
    if not results:
        raise ValueError("网页没有解析到正文段落")
    return results


def _read_via_browser(url: str, limit: int, timeout: int, *, catalog: bool,
                      section_chars: int) -> list[SearchResult]:
    """Anonymous Chrome only; never a publishing login."""
    from .video.screenshots import page_for_capture, wait_unless_csp

    parser = parse_catalog if catalog else (
        lambda page, target, lim: parse_page(page, target, lim, section_chars=section_chars))
    with page_for_capture(url, timeout_seconds=timeout) as page:
        # Under a CSP that forbids eval, read what has rendered; the parser rejects a page with no text.
        wait_unless_csp(page, "(document.querySelector('article, main') || document.body).innerText.length > 120",
                        timeout * 1000)
        return _parse_with(parser, page.content(), page.url, limit)


def _read_via_paid(search_client: SearchClient, url: str, limit: int, *, catalog: bool,
                   section_chars: int) -> list[SearchResult]:
    content = search_client.read_page(url)
    import markdown
    page = markdown.markdown(content.excerpts[0], extensions=["tables"])
    parser = parse_catalog if catalog else (
        lambda body, target, lim: parse_page(body, target, lim, section_chars=section_chars))
    return _parse_with(parser, "<main><h1>" + html.escape(content.title) + "</h1>" + page + "</main>", url, limit)


def _read_via_opencode(url: str, limit: int, *, catalog: bool, section_chars: int,
                       config_dir: str | None = None) -> list[SearchResult]:
    """OpenCode CLI webfetch: declared-URL fallback before Exa Contents."""
    from .opencode_cli import settings, webfetch_markdown

    body = webfetch_markdown(url, config=settings(config_dir))
    import markdown
    page = markdown.markdown(body, extensions=["tables"])
    parser = parse_catalog if catalog else (
        lambda raw, target, lim: parse_page(raw, target, lim, section_chars=section_chars))
    return _parse_with(parser, "<main>" + page + "</main>", url, limit)


def fetch_page(url: str, limit: int, timeout: int, *, get_page, search_client: SearchClient | None = None,
               catalog: bool = False, section_chars: int = MAX_SECTION_CHARS, browser: bool = True,
               allow_paid: bool = True, allow_opencode: bool = True,
               config_dir: str | None = None) -> list[SearchResult]:
    """HTTP → anonymous browser → OpenCode CLI webfetch → optional Exa Contents.

    Declared sources should succeed without Exa when possible. OpenCode is the
    local-agent fallback; Exa remains last-resort paid reading.
    """
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("网页来源需要不带账号密码的 HTTP 或 HTTPS URL")
    parser = parse_catalog if catalog else (
        lambda page, target, lim: parse_page(page, target, lim, section_chars=section_chars))
    errors: list[BaseException] = []

    try:
        return _parse_with(parser, get_page(url, timeout), url, limit)
    except CancelledError:
        raise
    except Exception as exc:
        errors.append(exc)

    if browser:
        try:
            return _read_via_browser(url, limit, timeout, catalog=catalog, section_chars=section_chars)
        except CancelledError:
            raise
        except Exception as exc:
            errors.append(exc)

    if allow_opencode:
        try:
            from .opencode_cli import available, settings as opencode_settings
            if available(opencode_settings(config_dir)):
                return _read_via_opencode(url, limit, catalog=catalog, section_chars=section_chars,
                                         config_dir=config_dir)
        except CancelledError:
            raise
        except Exception as exc:
            errors.append(exc)
            logger.info("OpenCode CLI webfetch unavailable for %s: %s", url, str(exc)[:160])

    if search_client is not None and allow_paid:
        try:
            return _read_via_paid(search_client, url, limit, catalog=catalog, section_chars=section_chars)
        except CancelledError:
            raise
        except Exception as exc:
            errors.append(exc)
            if paid_reader_exhausted(exc):
                logger.info("Paid page reader unavailable for %s: %s", url, str(exc)[:160])
                # Prefer actionable local failures over a paid-quota message.
                for prev in reversed(errors[:-1]):
                    text = str(prev)
                    if ("截图组件未安装" in text or "Google Chrome" in text
                            or "HTTP 403" in text or "Forbidden" in text
                            or "页面未成功打开" in text
                            or "OpenCode" in text):
                        raise prev from exc

    raise errors[-1]


def follow_teaser_articles(items: list[SearchResult], *, search_client: SearchClient | None = None,
                           cancel_token=None, progress_cb=None, detail_pages: int = DEFAULT_DETAIL_PAGES,
                           detail_chars: int = PAGE_SECTION_CHARS, timeout: int = 15) -> tuple[list[SearchResult], dict]:
    """For fetch-only Jobs without source_supply: follow RSS/catalog teasers into article bodies."""
    from dataclasses import replace

    from .ai_dossier_sources import detail_sections
    from .sources import _get_page

    if detail_pages <= 0 or not items:
        return list(items), {"version": 1, "details": [], "successful": 0, "attempted": 0}

    out = list(items)
    details: list[dict] = []
    seen: set[str] = set()
    attempted = successful = 0
    paid_error = ""
    if progress_cb:
        progress_cb({"type": "phase", "phase": "reading_source_details"})

    for index, item in enumerate(list(items)):
        if successful >= detail_pages or attempted >= detail_pages * 2:
            break
        if item.provider not in TEASER_PROVIDERS or not item.url:
            continue
        target = canonical_url(item.url)
        if target in seen:
            continue
        seen.add(target)
        if cancel_token is not None and cancel_token.is_set():
            raise CancelledError("正文补读已取消")
        attempted += 1
        detail = {"url": target, "status": "unavailable", "sections": 0, "seed": index}
        details.append(detail)
        try:
            parts = fetch_page(target, limit=24, timeout=timeout, get_page=_get_page,
                               search_client=None if paid_error else search_client,
                               section_chars=PAGE_SECTION_CHARS, browser=True,
                               allow_paid=not paid_error)
            kept = detail_sections(parts, max_chars=detail_chars)
            if not kept and parts and detail_chars > 1000:
                kept = [replace(parts[0], excerpts=[parts[0].excerpts[0][:detail_chars - 600]
                                                    if parts[0].excerpts else ""])]
            for part in kept:
                out.append(replace(part, excerpts=[*part.excerpts, "（正文补读）"]))
                detail["sections"] += 1
            detail["status"] = "read" if detail["sections"] else "budget"
            if detail["sections"]:
                successful += 1
        except CancelledError:
            raise
        except Exception as exc:
            detail["error"] = str(exc)[:300]
            if paid_reader_exhausted(exc):
                paid_error = str(exc)[:300]
                detail["fallback_error"] = paid_error
            out[index] = replace(item, excerpts=[*item.excerpts, "原网页未能补读，以上是该来源已取得的内容。"])

    return out, {"version": 1, "details": details, "successful": successful, "attempted": attempted,
                 "paid_error": paid_error}

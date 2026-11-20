"""Read public pages and tutorial directories as research material."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
import html
import logging
import re
import posixpath
import threading
import time
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, NavigableString, Tag

from .errors import CancelledError
from .search import SearchClient, SearchResult

MAX_SECTION_CHARS = 50000
PAGE_SECTION_CHARS = 80000
TEASER_PROVIDERS = frozenset({"rss", "catalog", "tldr", "hn", "gh_new", "github", "arxiv", "hf_papers"})
DEFAULT_DETAIL_PAGES = 8
DEFAULT_DETAIL_TIMEOUT_SECONDS = 120.0
DEFAULT_HOST_BLOCK_THRESHOLD = 2
_PAID_QUOTA = re.compile(r"HTTP (401|402|429)\b")
_HARD_BLOCK = re.compile(r"(?:HTTP\s*403|Forbidden|Access Denied|verify you are human|Just a moment)", re.I)
logger = logging.getLogger(__name__)


class PageCache:
    """Thread-safe, run-scoped URL page cache shared across R6 → R7 → R8 → R10.

    Caches both parsed sections and terminal read failures so downstream stages
    do not repeat HTTP/Chrome/OpenCode/Exa fallback chains for the same URL or
    spend minutes retrying slow fallbacks on a hard-blocked host.
    """

    def __init__(self, *, host_block_threshold: int = DEFAULT_HOST_BLOCK_THRESHOLD):
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, bool], dict] = {}
        self._host_blocks: dict[str, int] = {}
        self._host_block_threshold = max(1, int(host_block_threshold))
        self.hits = 0
        self.negative_hits = 0
        self.misses = 0
        self.host_fast_fails = 0

    @staticmethod
    def _key(url: str, catalog: bool) -> tuple[str, bool]:
        return (canonical_url(url), bool(catalog))

    @staticmethod
    def _host(url: str) -> str:
        return (urlsplit(url).netloc or "").lower()

    @staticmethod
    def _clone(results: tuple[SearchResult, ...], limit: int) -> list[SearchResult]:
        return [replace(item, excerpts=list(item.excerpts)) for item in results[:max(1, limit)]]

    def get(self, url: str, *, catalog: bool = False, limit: int = 80,
            browser: bool = True, opencode: bool = True, paid: bool = True) -> tuple[str, object]:
        """Return ``('ok', results)``, ``('error', message)``, or ``('miss', None)``."""
        key = self._key(url, catalog)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self.misses += 1
                return "miss", None
            if entry["status"] == "ok":
                results = entry["results"]
                if entry["limit"] >= limit or len(results) < entry["limit"]:
                    self.hits += 1
                    return "ok", self._clone(results, limit)
                self.misses += 1
                return "miss", None
            # Negative hit only when previous attempt already tried at least the same readers.
            if ((not browser or entry["browser"])
                    and (not opencode or entry["opencode"])
                    and (not paid or entry["paid"])):
                self.negative_hits += 1
                return "error", entry["message"]
            self.misses += 1
            return "miss", None

    def get_ok(self, url: str, *, catalog: bool = False, limit: int = 80) -> list[SearchResult] | None:
        key = self._key(url, catalog)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or entry["status"] != "ok":
                return None
            self.hits += 1
            return self._clone(entry["results"], limit)

    def put_ok(self, url: str, results: list[SearchResult], *, catalog: bool = False,
               limit: int = 80, section_chars: int = PAGE_SECTION_CHARS) -> None:
        if not results:
            return
        key = self._key(url, catalog)
        stored = tuple(replace(item, excerpts=list(item.excerpts)) for item in results)
        with self._lock:
            self._entries[key] = {
                "status": "ok",
                "results": stored,
                "limit": max(int(limit), len(stored)),
                "section_chars": int(section_chars),
            }

    def put_error(self, url: str, exc: BaseException, *, catalog: bool = False,
                  browser: bool = True, opencode: bool = True, paid: bool = True) -> None:
        key = self._key(url, catalog)
        with self._lock:
            prev = self._entries.get(key)
            if prev and prev["status"] == "ok":
                return
            self._entries[key] = {
                "status": "error",
                "message": str(exc)[:300] or type(exc).__name__,
                "browser": bool(browser) or bool(prev and prev.get("browser")),
                "opencode": bool(opencode) or bool(prev and prev.get("opencode")),
                "paid": bool(paid) or bool(prev and prev.get("paid")),
            }

    def host_blocked(self, url: str) -> bool:
        host = self._host(url)
        if not host:
            return False
        with self._lock:
            return self._host_blocks.get(host, 0) >= self._host_block_threshold

    def record_host_block(self, url: str) -> None:
        host = self._host(url)
        if not host:
            return
        with self._lock:
            self._host_blocks[host] = self._host_blocks.get(host, 0) + 1

    def clear_host_block(self, url: str) -> None:
        host = self._host(url)
        if not host:
            return
        with self._lock:
            self._host_blocks.pop(host, None)

    def note_host_fast_fail(self) -> None:
        with self._lock:
            self.host_fast_fails += 1

    def stats(self) -> dict:
        with self._lock:
            return {
                "hits": self.hits,
                "negative_hits": self.negative_hits,
                "misses": self.misses,
                "host_fast_fails": self.host_fast_fails,
                "entries": len(self._entries),
            }


_PAGE_CACHE: ContextVar[PageCache | None] = ContextVar("source_pages_cache", default=None)


def current_page_cache() -> PageCache | None:
    """Return the active run-scoped PageCache, or None outside a scope."""
    return _PAGE_CACHE.get()


@contextmanager
def page_cache_scope(cache: PageCache | None = None):
    """Bind a shared PageCache for the duration of a research run or worker thread."""
    active = cache if cache is not None else PageCache()
    token = _PAGE_CACHE.set(active)
    try:
        yield active
    finally:
        _PAGE_CACHE.reset(token)


def get_cached_page(url: str, *, catalog: bool = False, limit: int = 80) -> list[SearchResult] | None:
    cache = _PAGE_CACHE.get()
    if cache is None:
        return None
    return cache.get_ok(url, catalog=catalog, limit=limit)


def store_cached_page(url: str, results: list[SearchResult], *, catalog: bool = False,
                      limit: int = 80, section_chars: int = PAGE_SECTION_CHARS) -> None:
    cache = _PAGE_CACHE.get()
    if cache is not None:
        cache.put_ok(url, results, catalog=catalog, limit=limit, section_chars=section_chars)


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
            excerpt += "\n相关页面：" + "；".join(dict.fromkeys(links))[:8000]
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
               config_dir: str | None = None, cache: PageCache | None = None) -> list[SearchResult]:
    """HTTP → anonymous browser → OpenCode CLI webfetch → optional Exa Contents.

    Declared sources should succeed without Exa when possible. OpenCode is the
    local-agent fallback; Exa remains last-resort paid reading.
    When a run-scoped ``PageCache`` is active, positive and negative results are
    reused across stages and hard-blocked hosts skip slow local fallbacks.
    """
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("网页来源需要不带账号密码的 HTTP 或 HTTPS URL")

    active_cache = cache if cache is not None else _PAGE_CACHE.get()
    paid_already_out = bool(getattr(search_client, "contents_exhausted", ""))
    req_paid = bool(search_client is not None and allow_paid and not paid_already_out)
    if active_cache is not None:
        status, cached = active_cache.get(
            url, catalog=catalog, limit=limit,
            browser=browser, opencode=allow_opencode, paid=req_paid,
        )
        if status == "ok":
            return cached  # type: ignore[return-value]
        if status == "error":
            raise RuntimeError(str(cached))

    parser = parse_catalog if catalog else (
        lambda page, target, lim: parse_page(page, target, lim, section_chars=section_chars))
    errors: list[BaseException] = []
    browser_tried = False
    opencode_tried = False
    paid_tried = False

    try:
        results = _parse_with(parser, get_page(url, timeout), url, limit)
        if active_cache is not None:
            active_cache.put_ok(url, results, catalog=catalog, limit=limit, section_chars=section_chars)
        return results
    except CancelledError:
        raise
    except Exception as exc:
        errors.append(exc)

    host_fast_blocked = (
        active_cache is not None
        and active_cache.host_blocked(url)
        and bool(_HARD_BLOCK.search(str(errors[-1])))
    )
    if host_fast_blocked:
        active_cache.note_host_fast_fail()
        browser_tried = browser
        opencode_tried = allow_opencode
    else:
        if browser:
            browser_tried = True
            try:
                results = _read_via_browser(url, limit, timeout, catalog=catalog, section_chars=section_chars)
                if active_cache is not None:
                    active_cache.clear_host_block(url)
                    active_cache.put_ok(url, results, catalog=catalog, limit=limit, section_chars=section_chars)
                return results
            except CancelledError:
                raise
            except Exception as exc:
                errors.append(exc)

        if allow_opencode:
            try:
                from .opencode_cli import available, settings as opencode_settings
                if available(opencode_settings(config_dir)):
                    opencode_tried = True
                    results = _read_via_opencode(url, limit, catalog=catalog, section_chars=section_chars,
                                                 config_dir=config_dir)
                    if active_cache is not None:
                        active_cache.clear_host_block(url)
                        active_cache.put_ok(url, results, catalog=catalog, limit=limit, section_chars=section_chars)
                    return results
            except CancelledError:
                raise
            except Exception as exc:
                errors.append(exc)
                logger.info("OpenCode CLI webfetch unavailable for %s: %s", url, str(exc)[:160])

    if req_paid:
        paid_tried = True
        try:
            results = _read_via_paid(search_client, url, limit, catalog=catalog, section_chars=section_chars)
            if active_cache is not None:
                active_cache.put_ok(url, results, catalog=catalog, limit=limit, section_chars=section_chars)
            return results
        except CancelledError:
            raise
        except Exception as exc:
            errors.append(exc)
            if paid_reader_exhausted(exc):
                logger.info("Paid page reader unavailable for %s: %s", url, str(exc)[:160])
                # Prefer actionable local failures over a paid-quota message.
                # SearchClient.contents_exhausted stays set so callers skip Exa on later URLs.
                chosen: BaseException = exc
                for prev in reversed(errors[:-1]):
                    text = str(prev)
                    if ("截图组件未安装" in text or "Google Chrome" in text
                            or "HTTP 403" in text or "Forbidden" in text
                            or "页面未成功打开" in text
                            or "OpenCode" in text):
                        chosen = prev
                        break
                if active_cache is not None:
                    if not host_fast_blocked and any(_HARD_BLOCK.search(str(e)) for e in errors[:-1]):
                        active_cache.record_host_block(url)
                    active_cache.put_error(url, chosen, catalog=catalog,
                                           browser=browser_tried, opencode=opencode_tried, paid=False)
                if chosen is not exc:
                    raise chosen from exc
                raise

    final_exc = errors[-1]
    if active_cache is not None:
        if not host_fast_blocked and any(_HARD_BLOCK.search(str(e)) for e in errors):
            active_cache.record_host_block(url)
        active_cache.put_error(url, final_exc, catalog=catalog,
                               browser=browser_tried, opencode=opencode_tried, paid=paid_tried)
    raise final_exc


def follow_teaser_articles(items: list[SearchResult], *, search_client: SearchClient | None = None,
                           cancel_token=None, progress_cb=None, detail_pages: int = DEFAULT_DETAIL_PAGES,
                           detail_chars: int = PAGE_SECTION_CHARS, timeout: int = 15,
                           detail_timeout_seconds: float = DEFAULT_DETAIL_TIMEOUT_SECONDS) -> tuple[list[SearchResult], dict]:
    """For fetch-only Jobs without source_supply: follow RSS/catalog teasers into article bodies."""
    from .ai_dossier_sources import detail_sections
    from .sources import _get_page

    if detail_pages <= 0 or not items:
        return list(items), {"version": 1, "details": [], "successful": 0, "attempted": 0}

    out = list(items)
    details: list[dict] = []
    seen: set[str] = set()
    attempted = successful = skipped_by_timeout = 0
    timed_out = False
    started = time.monotonic()
    paid_error = str(getattr(search_client, "contents_exhausted", "") or "")
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
        if detail_timeout_seconds > 0 and (time.monotonic() - started) >= detail_timeout_seconds:
            timed_out = True
            skipped_by_timeout += 1
            out[index] = replace(item, excerpts=[*item.excerpts, "正文补读达到时间预算，以上是该来源已取得的内容。"])
            continue
        attempted += 1
        detail = {"url": target, "status": "unavailable", "sections": 0, "seed": index}
        details.append(detail)
        try:
            # fetch_page may re-raise HTTP 403 after Exa 402; contents_exhausted still marks quota.
            paid_error = paid_error or str(getattr(search_client, "contents_exhausted", "") or "")
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
            exhausted = str(getattr(search_client, "contents_exhausted", "") or "")
            if paid_reader_exhausted(exc) or exhausted:
                paid_error = (str(exc) if paid_reader_exhausted(exc) else exhausted)[:300]
                detail["fallback_error"] = paid_error
            out[index] = replace(item, excerpts=[*item.excerpts, "原网页未能补读，以上是该来源已取得的内容。"])

    return out, {"version": 1, "details": details, "successful": successful, "attempted": attempted,
                 "paid_error": paid_error, "timed_out": timed_out, "skipped_by_timeout": skipped_by_timeout,
                 "elapsed_seconds": round(time.monotonic() - started, 2)}

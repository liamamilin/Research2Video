"""
Web search API clients.

Providers: Parallel (default), Exa, Tavily, Brave Search, Serper (Google), plus a
keyless DuckDuckGo fallback. All return ranked URLs with LLM-ready excerpts.

Configured via ``system.yaml``::

    search:
      provider: "parallel"          # parallel | exa | tavily | brave | serper | duckduckgo
      api_key_env: "PARALLEL_API_KEY"
      api_key_envs:                 # per-provider keys (used when switching)
        exa: "EXA_API_KEY"
        tavily: "TAVILY_API_KEY"
        brave: "BRAVE_API_KEY"
        serper: "SERPER_API_KEY"
      mode: "advanced"              # turbo | fast | basic | advanced
      max_results: 10
      max_chars_per_result: 3000
      after_date: ""                # optional YYYY-MM-DD freshness bound
      fallback_provider: "duckduckgo"
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from .errors import CancelledError, SearchUnavailableError

logger = logging.getLogger(__name__)

_PARALLEL_ENDPOINT = "https://api.parallel.ai/v1/search"
_EXA_MCP_ENDPOINT = "https://mcp.exa.ai/mcp"  # Exa's public hosted MCP server: keyless, rate limits undocumented
_YOUCOM_ENDPOINT = "https://api.you.com/mcp?profile=free"  # keyless, you-search only, 100 queries a day
_DOUBAO_ENDPOINT = "https://open.feedcoopapi.com/search_api/global_search"
_DOUBAO_QUERY_CHARS = 100  # the API truncates longer queries and takes one phrase per request
_MAX_QUERIES = 5
_MAX_QUERY_CHARS = 200
_MAX_OBJECTIVE_CHARS = 5000

# Query size limits enforced with truncation
_ALLOWED_MODES = {"turbo", "fast", "basic", "advanced"}


@dataclass
class SearchResult:
    """A single web search result."""

    title: str
    url: str
    publish_date: Optional[str] = None
    excerpts: list[str] = field(default_factory=list)
    provider: str = ""

    def to_markdown(self) -> str:
        date = f" — published {self.publish_date}" if self.publish_date else ""
        lines = [f"### [{self.title or self.url}]({self.url}){date}"]
        for excerpt in self.excerpts:
            if excerpt.strip():
                lines.append(excerpt.strip())
        return "\n\n".join(lines)


@dataclass
class SearchConfig:
    """Search provider configuration."""

    provider: str = "parallel"
    api_key: str = ""
    api_key_env: str = "PARALLEL_API_KEY"
    mode: str = "advanced"
    max_results: int = 10
    max_chars_per_result: int = 3000
    max_chars_total: int = 60000
    after_date: str = ""
    timeout: int = 60
    fallback_provider: str = "duckduckgo"
    api_keys: dict = field(default_factory=dict)
    key_envs: dict = field(default_factory=dict)  # provider -> its key's env var (search.api_key_envs)
    fallback_providers: list = field(default_factory=list)

    @classmethod
    def from_system(cls, sys_config: dict) -> "SearchConfig":
        cfg = sys_config.get("search", {}) or {}
        api_key_env = cfg.get("api_key_env", "PARALLEL_API_KEY")
        api_key = os.environ.get(api_key_env, "")
        if cfg.get("provider", "parallel") == "parallel" and not api_key:
            logger.warning(
                "Environment variable '%s' not set; parallel search disabled",
                api_key_env,
            )
        after_date = cfg.get("after_date", "") or ""
        if not after_date and cfg.get("after_days"):
            after_date = (
                datetime.now() - timedelta(days=int(cfg["after_days"]))
            ).strftime("%Y-%m-%d")

        api_keys = {"parallel": api_key} if api_key else {}
        for provider, env_name in (cfg.get("api_key_envs") or {}).items():
            key = os.environ.get(env_name, "")
            if key:
                api_keys[provider] = key

        return cls(
            provider=cfg.get("provider", "parallel"),
            api_key=api_key,
            api_key_env=api_key_env,
            mode=cfg.get("mode", "advanced"),
            max_results=int(cfg.get("max_results", 10)),
            max_chars_per_result=int(cfg.get("max_chars_per_result", 3000)),
            max_chars_total=int(cfg.get("max_chars_total", 60000)),
            after_date=after_date,
            timeout=int(cfg.get("timeout", 60)),
            fallback_provider=cfg.get("fallback_provider", "duckduckgo"),
            api_keys=api_keys,
            key_envs={str(k): str(v) for k, v in (cfg.get("api_key_envs") or {}).items()},
            fallback_providers=list(cfg.get("fallback_providers") or []),
        )


class SearchClient:
    """Facade over search providers with fallback and deduplication."""

    def __init__(
        self,
        config: SearchConfig,
        cancel_token: Optional[threading.Event] = None,
        progress_cb=None,
    ):
        self.config = config
        self._cancel = cancel_token or threading.Event()
        self._progress = progress_cb or (lambda _: None)
        self._session_id: str = ""
        self.calls = 0
        #: provider -> why it gave nothing, from the last ``search`` (a provider that failed is otherwise only in the log)
        self.errors: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search(
        self,
        queries: list[str],
        objective: str = "",
        after_date: Optional[str] = None,
    ) -> list[SearchResult]:
        """Run a web search.

        Returns [] when a provider answered but found nothing. Raises
        ``SearchUnavailableError`` when no provider could answer at all: an
        empty list there would look like "nothing to find" and the caller would
        write from memory.
        """
        queries = self._clean_queries(queries)
        if not queries:
            logger.warning("search called without usable queries")
            return []

        if self._cancel.is_set():
            raise CancelledError("Cancelled before search")

        self.errors = {}
        providers = [self.config.provider]
        for fallback in ([self.config.fallback_provider]
                         + list(self.config.fallback_providers)):
            if fallback and fallback not in providers:
                providers.append(fallback)

        from core import search_health

        # Credits, keys and rate limits do not recover within one run: skip a provider that just failed that way
        # (shared with the other processes) instead of spending another doomed request on it.
        cooling = search_health.cooling()
        answered = False
        for provider in providers:
            if provider in cooling:
                self.errors[provider] = f"暂停使用（{cooling[provider]['reason'][:120]}）"
                continue
            try:
                if provider == "parallel":
                    results = self._search_parallel(
                        queries, objective, after_date=after_date
                    )
                elif provider == "exa":
                    results = self._search_exa(queries, after_date=after_date)
                elif provider == "exa_mcp":
                    results = self._search_exa_mcp(queries, objective)
                elif provider == "youcom":
                    results = self._search_youcom(queries, after_date=after_date)
                elif provider == "doubao":
                    results = self._search_doubao(queries)
                elif provider == "tavily":
                    results = self._search_tavily(queries, objective)
                elif provider == "brave":
                    results = self._search_brave(queries)
                elif provider == "serper":
                    results = self._search_serper(queries)
                elif provider == "duckduckgo":
                    results = self._search_duckduckgo(queries)
                else:
                    logger.error("Unknown search provider: %s", provider)
                    self.errors[provider] = "未知的搜索服务商"
                    continue
                answered = True
                search_health.record_answer(provider)
                if results:
                    self.calls += 1
                    return self._dedupe(results)
                self.errors[provider] = "没有返回结果"
                logger.warning(
                    "Search provider '%s' returned no results", provider
                )
            except CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - provider fallback
                self.errors[provider] = str(exc)[:300]
                search_health.record_failures({provider: self.errors[provider]})
                logger.warning(
                    "Search provider '%s' failed: %s", provider, str(exc)[:300]
                )

        if not answered:
            search_health.record_outage(self.errors)
            raise SearchUnavailableError(self.errors)
        return []

    @staticmethod
    def format_results(results: list[SearchResult]) -> str:
        """Render results as a Markdown block for LLM consumption."""
        if not results:
            return "(no search results)"
        blocks = [r.to_markdown() for r in results]
        header = f"({len(results)} sources retrieved from web search)\n"
        return header + "\n\n---\n\n".join(blocks)

    def read_page(self, url: str) -> SearchResult:
        """Read one exact public URL through Exa Contents when direct fetching is unavailable."""
        if self._cancel.is_set():
            raise CancelledError("Cancelled before reading page")
        key = self.config.api_keys.get("exa")
        if not key:
            raise RuntimeError("网页直接读取失败；可配置 EXA_API_KEY 使用 Exa 网页正文读取")
        self._progress({"type": "tool", "tool": "read_page", "url": url})
        body = self._post_json("https://api.exa.ai/contents", {
            "urls": [url], "text": True, "maxAgeHours": 0,
        }, api_key=key)
        from .source_pages import canonical_url
        for item in body.get("results") or []:
            if canonical_url(item.get("url") or "") == canonical_url(url) and item.get("text"):
                return SearchResult(title=item.get("title") or url, url=url,
                                    publish_date=item.get("publishedDate"),
                                    excerpts=[item["text"][:120000]], provider="exa_contents")
        raise RuntimeError("网页正文服务未取得指定页面的内容")

    # ------------------------------------------------------------------
    # Parallel Search API
    # ------------------------------------------------------------------

    def _search_parallel(
        self,
        queries: list[str],
        objective: str,
        after_date: Optional[str] = None,
    ) -> list[SearchResult]:
        cfg = self.config
        if not cfg.api_key:
            raise RuntimeError(
                f"missing API key (env {cfg.api_key_env})"
            )

        advanced: dict = {
            "max_results": cfg.max_results,
            "excerpt_settings": {
                "max_chars_per_result": cfg.max_chars_per_result,
            },
        }
        source_policy: dict = {}
        bound = after_date or cfg.after_date
        if bound:
            source_policy["after_date"] = bound
        if source_policy:
            advanced["source_policy"] = source_policy

        payload: dict = {
            "search_queries": [q[:_MAX_QUERY_CHARS] for q in queries],
            "mode": cfg.mode if cfg.mode in _ALLOWED_MODES else "advanced",
            "max_chars_total": cfg.max_chars_total,
            "advanced_settings": advanced,
        }
        if objective:
            payload["objective"] = objective[:_MAX_OBJECTIVE_CHARS]
        if self._session_id:
            payload["session_id"] = self._session_id

        self._progress(
            {"type": "search", "provider": "parallel", "queries": queries}
        )
        logger.info("Searching (parallel/%s): %s", cfg.mode, "; ".join(queries))

        body = self._post_json(_PARALLEL_ENDPOINT, payload, cfg.api_key)
        self._session_id = body.get("session_id", "") or self._session_id

        results = []
        for item in body.get("results", []):
            results.append(
                SearchResult(
                    title=item.get("title") or "",
                    url=item.get("url") or "",
                    publish_date=item.get("publish_date"),
                    excerpts=list(item.get("excerpts") or []),
                    provider="parallel",
                )
            )
        usage = body.get("usage") or []
        if usage:
            logger.debug("Search usage: %s", usage)
        return results

    def _post_json(self, url: str, payload: dict, api_key: Optional[str] = None,
                   header_name: str = "x-api-key", auth_prefix: str = "") -> dict:
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "BaseCodingCLi/2.0",
        }
        if api_key:
            headers[header_name] = f"{auth_prefix}{api_key}"
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(
                req, timeout=self.config.timeout
            ) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"connection failed: {exc.reason}") from exc

    # ------------------------------------------------------------------
    # Tavily / Brave / Serper adapters
    # ------------------------------------------------------------------

    def _provider_key(self, provider: str) -> str:
        key = self.config.api_keys.get(provider, "")
        env = self.config.key_envs.get(provider)
        # a provider with its own key variable is asked with that key only: the generic one (Parallel's) belongs to
        # another vendor and would come back as a 401
        if not key and provider == self.config.provider and not env:
            key = self.config.api_key
        if not key:
            raise RuntimeError(
                f"missing API key for provider '{provider}' "
                f"({'env ' + env if env else 'configure search.api_key_envs.' + provider})"
            )
        return key

    def _search_exa(self, queries: list[str], after_date: Optional[str] = None) -> list[SearchResult]:
        """Exa (https://docs.exa.ai): one request per query, page text as the excerpt, key in ``x-api-key``."""
        key = self._provider_key("exa")
        self._progress({"type": "search", "provider": "exa", "queries": queries})
        logger.info("Searching (exa): %s", "; ".join(queries))

        bound = after_date or self.config.after_date
        results: list[SearchResult] = []
        for query in queries[:3]:
            if self._cancel.is_set():
                raise CancelledError("Cancelled during search")
            payload: dict = {
                "query": query[:_MAX_QUERY_CHARS],
                "type": "auto",
                "numResults": self.config.max_results,
                "contents": {"text": {"maxCharacters": self.config.max_chars_per_result}},
            }
            if bound:
                payload["startPublishedDate"] = f"{bound}T00:00:00.000Z"
            body = self._post_json("https://api.exa.ai/search", payload, api_key=key)
            for item in body.get("results", []):
                results.append(SearchResult(
                    title=item.get("title") or "",
                    url=item.get("url") or "",
                    publish_date=(item.get("publishedDate") or "")[:10] or None,
                    excerpts=[item.get("text") or "", *(item.get("highlights") or [])],
                    provider="exa",
                ))
        return results

    #: Doubao search error codes worth saying in words (the rest are shown as the API's own message)
    _DOUBAO_ERRORS = {
        "10403": "账号或权限错误，检查 Key 和服务开通状态",
        "10408": "服务未付费开通",
        "10410": "没有可用的搜索套餐",
        "10412": "搜索额度不足",
        "700429": "请求太频繁（超过 10 QPS）",
        "700901": "API Key 无效",
    }

    def _mcp_call(self, endpoint: str, tool: str, arguments: dict) -> str:
        """Call one tool of a remote MCP server (JSON-RPC over HTTP; the reply is a server-sent-event stream whose last
        ``data:`` line with a result carries the answer) and return its text. Any error, in HTTP, in JSON-RPC or in the
        tool's own ``isError``, raises: an empty string must mean "found nothing", never "was refused"."""
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
        request = urllib.request.Request(
            endpoint, data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                     "User-Agent": "BaseCodingCLi/2.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code}{'：被限流' if exc.code == 429 else ''}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"connection failed: {exc.reason}") from exc

        replies = []
        for line in raw.splitlines():
            if line.startswith("data:"):
                try:
                    replies.append(json.loads(line[5:].strip()))
                except ValueError:
                    continue
        if not replies:  # a plain JSON body instead of a stream
            try:
                replies = [json.loads(raw)]
            except ValueError as exc:
                raise RuntimeError(f"unreadable reply: {raw[:120]}") from exc
        # a stream may carry log notifications before the answer
        reply = next((r for r in reversed(replies) if "result" in r or "error" in r), {})
        if reply.get("error"):
            raise RuntimeError(f"{(reply['error'] or {}).get('message') or reply['error']}"[:300])
        outcome = reply.get("result") or {}
        text = "\n".join(c.get("text") or "" for c in outcome.get("content") or [] if c.get("type") == "text")
        if outcome.get("isError"):
            raise RuntimeError(text[:300] or "the search tool reported an error")
        return text

    #: one result in the MCP tool's text: ``Title/URL/Published/Author/Highlights`` then the page text
    _EXA_MCP_RESULT = re.compile(
        r"^Title: (?P<title>.*)\nURL: (?P<url>.*)\nPublished: (?P<published>.*)\nAuthor: .*\nHighlights:\n",
        re.MULTILINE,
    )

    def _search_exa_mcp(self, queries: list[str], objective: str = "") -> list[SearchResult]:
        """Exa through its public MCP endpoint (what OpenCode's ``websearch`` tool uses): no key, free, and with
        undocumented rate limits, so it is a fallback, not a main provider. Plain JSON-RPC over HTTP; the reply is a
        server-sent event whose ``data:`` line carries the JSON-RPC response."""
        self._progress({"type": "search", "provider": "exa_mcp", "queries": queries})
        logger.info("Searching (exa_mcp): %s", "; ".join(queries))

        results: list[SearchResult] = []
        for query in queries[:3]:
            if self._cancel.is_set():
                raise CancelledError("Cancelled during search")
            text = self._mcp_call(_EXA_MCP_ENDPOINT, "web_search_exa", {
                "query": query[:_MAX_QUERY_CHARS],
                "numResults": self.config.max_results,
                "objective": (objective or query)[:4096],
            })
            found = list(self._EXA_MCP_RESULT.finditer(text))
            for i, match in enumerate(found):
                end = found[i + 1].start() if i + 1 < len(found) else len(text)
                body = text[match.end():end].strip()
                published = match.group("published").strip()
                results.append(SearchResult(
                    title=match.group("title").strip() if match.group("title") != "N/A" else "",
                    url=match.group("url").strip(),
                    publish_date=None if published in ("", "N/A") else published[:10],
                    excerpts=[body[: self.config.max_chars_per_result]],
                    provider="exa_mcp",
                ))
        return results

    def _search_youcom(self, queries: list[str], after_date: Optional[str] = None) -> list[SearchResult]:
        """You.com's free MCP profile: no key, ``you-search`` only, 100 queries a day. One request per query; a date
        bound becomes a ``freshness`` range."""
        self._progress({"type": "search", "provider": "youcom", "queries": queries})
        logger.info("Searching (youcom): %s", "; ".join(queries))

        bound = after_date or self.config.after_date
        results: list[SearchResult] = []
        for query in queries[:3]:
            if self._cancel.is_set():
                raise CancelledError("Cancelled during search")
            arguments: dict = {"query": query[:_MAX_QUERY_CHARS], "count": max(1, min(self.config.max_results, 100))}
            if bound:
                arguments["freshness"] = f"{bound}to{datetime.now().strftime('%Y-%m-%d')}"
            text = self._mcp_call(_YOUCOM_ENDPOINT, "you-search", arguments)
            try:
                groups = (json.loads(text).get("results") or {}) if text.strip() else {}
            except ValueError as exc:
                raise RuntimeError(f"unreadable result: {text[:120]}") from exc
            for item in [*(groups.get("web") or []), *(groups.get("news") or [])]:
                contents = item.get("contents") or {}
                excerpts = [e for e in (contents.get("highlights") or []) if e] or [item.get("description") or ""]
                results.append(SearchResult(
                    title=item.get("title") or "",
                    url=item.get("url") or "",
                    publish_date=(item.get("page_age") or "")[:10] or None,
                    excerpts=[e[: self.config.max_chars_per_result] for e in excerpts],
                    provider="youcom",
                ))
        return results

    def _search_doubao(self, queries: list[str]) -> list[SearchResult]:
        """Doubao search, Global edition (Volcengine, pay-as-you-go; 500 free calls a month per account).

        One phrase per request, ``Authorization: Bearer``. Errors come back as HTTP 200 with the code in the body, so
        both places are checked: an empty list from a rejected key would read as "nothing found".
        """
        key = self._provider_key("doubao")
        self._progress({"type": "search", "provider": "doubao", "queries": queries})
        logger.info("Searching (doubao): %s", "; ".join(queries))

        results: list[SearchResult] = []
        for query in queries[:3]:
            if self._cancel.is_set():
                raise CancelledError("Cancelled during search")
            body = self._post_json(
                _DOUBAO_ENDPOINT,
                {
                    "Query": query[:_DOUBAO_QUERY_CHARS],
                    "SearchType": "web",
                    "DocCount": max(1, min(self.config.max_results, 20)),
                    "MaxSnippetLength": max(100, min(self.config.max_chars_per_result, 1000)),
                    "MaxImageCountPerDoc": 0,
                },
                api_key=key, header_name="Authorization", auth_prefix="Bearer ",
            )
            error = (body.get("ResponseMetadata") or {}).get("Error") or {}
            result = body.get("Result") or {}
            code = str(error.get("Code") or (result.get("ErrorCode") or "") or "")
            if code and code != "0":
                message = error.get("Message") or result.get("ErrorMsg") or ""
                raise RuntimeError(f"{code} {self._DOUBAO_ERRORS.get(code, '')} {message}".replace("  ", " ").strip())
            for item in result.get("Documents") or []:
                texts = [(s.get("Text") or "").strip() for s in item.get("Snippet") or [] if s.get("Type") == "text"]
                info = item.get("DocumentInfo") or {}
                results.append(SearchResult(
                    title=item.get("Title") or "",
                    url=item.get("Url") or "",
                    publish_date=(info.get("PublishTime") or "")[:10] or None,
                    excerpts=[t for t in texts if t],
                    provider="doubao",
                ))
        return results

    def _search_tavily(self, queries: list[str], objective: str) -> list[SearchResult]:
        key = self._provider_key("tavily")
        self._progress({"type": "search", "provider": "tavily", "queries": queries})
        logger.info("Searching (tavily): %s", "; ".join(queries))

        results: list[SearchResult] = []
        for query in queries[:3]:
            if self._cancel.is_set():
                raise CancelledError("Cancelled during search")
            body = self._post_json(
                "https://api.tavily.com/search",
                {
                    "api_key": key,
                    "query": query,
                    "max_results": self.config.max_results,
                    "search_depth": "advanced",
                    "include_raw_content": False,
                },
                api_key=None,
            )
            for item in body.get("results", []):
                results.append(SearchResult(
                    title=item.get("title") or "",
                    url=item.get("url") or "",
                    publish_date=item.get("published_date"),
                    excerpts=[item.get("content") or ""],
                    provider="tavily",
                ))
        return results

    def _search_brave(self, queries: list[str]) -> list[SearchResult]:
        key = self._provider_key("brave")
        self._progress({"type": "search", "provider": "brave", "queries": queries})
        logger.info("Searching (brave): %s", "; ".join(queries))

        results: list[SearchResult] = []
        for query in queries[:3]:
            if self._cancel.is_set():
                raise CancelledError("Cancelled during search")
            url = (
                "https://api.search.brave.com/res/v1/web/search?"
                f"q={urllib.parse.quote(query)}&count={self.config.max_results}"
            )
            body = self._get_json(url, headers={"X-Subscription-Token": key})
            for item in (body.get("web") or {}).get("results", []):
                results.append(SearchResult(
                    title=item.get("title") or "",
                    url=item.get("url") or "",
                    publish_date=item.get("age"),
                    excerpts=[item.get("description") or ""],
                    provider="brave",
                ))
        return results

    def _search_serper(self, queries: list[str]) -> list[SearchResult]:
        key = self._provider_key("serper")
        self._progress({"type": "search", "provider": "serper", "queries": queries})
        logger.info("Searching (serper): %s", "; ".join(queries))

        results: list[SearchResult] = []
        for query in queries[:3]:
            if self._cancel.is_set():
                raise CancelledError("Cancelled during search")
            body = self._post_json(
                "https://google.serper.dev/search",
                {"q": query, "num": self.config.max_results},
                api_key=key,
                header_name="X-API-KEY",
            )
            for item in body.get("organic", []):
                results.append(SearchResult(
                    title=item.get("title") or "",
                    url=item.get("link") or "",
                    publish_date=item.get("date"),
                    excerpts=[item.get("snippet") or ""],
                    provider="serper",
                ))
        return results

    def _get_json(self, url: str, headers: dict) -> dict:
        request = urllib.request.Request(
            url,
            headers={**headers, "User-Agent": "BaseCodingCLi/2.0"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"connection failed: {exc.reason}") from exc

    # ------------------------------------------------------------------
    # DuckDuckGo fallback (keyless)
    # ------------------------------------------------------------------

    def _search_duckduckgo(self, queries: list[str]) -> list[SearchResult]:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            try:
                from ddgs import DDGS  # type: ignore[no-redef]
            except ImportError:
                raise RuntimeError("未安装 duckduckgo-search")

        self._progress(
            {"type": "search", "provider": "duckduckgo", "queries": queries}
        )
        logger.info("Searching (duckduckgo): %s", "; ".join(queries))

        results: list[SearchResult] = []
        with DDGS() as ddgs:
            for query in queries[:3]:
                if self._cancel.is_set():
                    raise CancelledError("Cancelled during search")
                for item in ddgs.text(query, max_results=self.config.max_results):
                    results.append(
                        SearchResult(
                            title=item.get("title") or "",
                            url=item.get("href") or item.get("url") or "",
                            excerpts=[item.get("body") or ""],
                            provider="duckduckgo",
                        )
                    )
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_queries(queries: list[str]) -> list[str]:
        cleaned = []
        for query in queries:
            q = " ".join(str(query).split())
            if q and q not in cleaned:
                cleaned.append(q)
        return cleaned[:_MAX_QUERIES]

    @staticmethod
    def _dedupe(results: list[SearchResult]) -> list[SearchResult]:
        seen = set()
        deduped = []
        for result in results:
            key = result.url.rstrip("/").lower()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(result)
        return deduped

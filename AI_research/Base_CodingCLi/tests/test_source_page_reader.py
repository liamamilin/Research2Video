"""Unified public-page reader: HTTP → browser → optional paid reader."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core import source_pages
from core.search import SearchResult


ARTICLE = "<article><h1>Title</h1><p>" + ("正文段落。" * 20) + "</p></article>"


def test_paid_reader_exhausted_detects_quota_codes():
    assert source_pages.paid_reader_exhausted(RuntimeError("HTTP 402: no credits"))
    assert source_pages.paid_reader_exhausted(RuntimeError("HTTP 401"))
    assert source_pages.paid_reader_exhausted(RuntimeError("HTTP 429 rate"))
    assert not source_pages.paid_reader_exhausted(RuntimeError("HTTP 403"))


def test_fetch_page_uses_browser_before_paid(monkeypatch):
    order = []

    def get_page(url, timeout):
        order.append("http")
        raise RuntimeError("HTTP 403")

    def browser(*a, **k):
        order.append("browser")
        return [SearchResult("From browser", "https://example.com/a", excerpts=["browser body"])]

    def paid(*a, **k):
        order.append("paid")
        return [SearchResult("From paid", "https://example.com/a", excerpts=["paid body"])]

    monkeypatch.setattr(source_pages, "_read_via_browser", browser)
    monkeypatch.setattr(source_pages, "_read_via_paid", paid)
    client = SimpleNamespace(read_page=lambda url: None)
    got = source_pages.fetch_page("https://example.com/a", 4, 10, get_page=get_page,
                                  search_client=client, browser=True, allow_paid=True,
                                  allow_opencode=False)
    assert got[0].title == "From browser" and order == ["http", "browser"]


def test_fetch_page_falls_back_to_paid_and_skips_when_quota_known(monkeypatch):
    def get_page(url, timeout):
        raise RuntimeError("HTTP 500 upstream")

    monkeypatch.setattr(source_pages, "_read_via_browser",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("browser offline")))

    def paid(client, url, limit, *, catalog, section_chars):
        raise RuntimeError("HTTP 402: insufficient credits")

    monkeypatch.setattr(source_pages, "_read_via_paid", paid)
    with pytest.raises(RuntimeError, match="402"):
        source_pages.fetch_page("https://example.com/a", 4, 10, get_page=get_page,
                                search_client=SimpleNamespace(), browser=True, allow_paid=True,
                                allow_opencode=False)
    with pytest.raises(RuntimeError, match="browser offline"):
        source_pages.fetch_page("https://example.com/a", 4, 10, get_page=get_page,
                                search_client=SimpleNamespace(), browser=True, allow_paid=False,
                                allow_opencode=False)


def test_fetch_page_prefers_local_block_over_paid_quota(monkeypatch):
    def get_page(url, timeout):
        raise RuntimeError("HTTP 403 Forbidden")

    monkeypatch.setattr(source_pages, "_read_via_browser",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("页面未成功打开（HTTP 403）")))
    monkeypatch.setattr(source_pages, "_read_via_paid",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("HTTP 402: no credits")))
    with pytest.raises(RuntimeError, match="403"):
        source_pages.fetch_page("https://example.com/a", 4, 10, get_page=get_page,
                                search_client=SimpleNamespace(), browser=True, allow_paid=True,
                                allow_opencode=False)


def test_fetch_page_http_success_skips_fallbacks(monkeypatch):
    monkeypatch.setattr(source_pages, "_read_via_browser",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("browser")))
    monkeypatch.setattr(source_pages, "_read_via_paid",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("paid")))
    got = source_pages.fetch_page("https://example.com/a", 4, 10,
                                  get_page=lambda url, timeout: ARTICLE, browser=True)
    assert "正文段落" in got[0].excerpts[0]


def test_follow_teaser_articles_appends_bodies_and_notes_failures(monkeypatch):
    teasers = [
        SearchResult("One", "https://news.example/1", excerpts=["短摘要一"], provider="rss"),
        SearchResult("Two", "https://news.example/2", excerpts=["短摘要二"], provider="rss"),
    ]

    def fake_fetch(url, limit, timeout, *, get_page, search_client=None, **k):
        if url.endswith("/1"):
            return [SearchResult("One body", url, excerpts=["完整正文一段。" * 30], provider="page")]
        raise RuntimeError("HTTP 403")

    monkeypatch.setattr(source_pages, "fetch_page", fake_fetch)
    items, receipt = source_pages.follow_teaser_articles(teasers, detail_pages=4)
    assert receipt["successful"] == 1 and receipt["attempted"] == 2
    assert any("完整正文" in "\n".join(i.excerpts) for i in items)
    assert "未能补读" in "\n".join(items[1].excerpts)
    assert receipt["details"][1]["status"] == "unavailable"


def test_follow_teaser_skips_paid_after_quota(monkeypatch):
    teasers = [
        SearchResult("A", "https://news.example/a", excerpts=["a"], provider="rss"),
        SearchResult("B", "https://news.example/b", excerpts=["b"], provider="rss"),
    ]
    paid = []

    def fake_fetch(url, limit, timeout, *, get_page, search_client=None, allow_paid=True, **k):
        if search_client is not None and allow_paid:
            paid.append(url)
            raise RuntimeError("HTTP 402: no credits")
        raise RuntimeError("HTTP 403")

    monkeypatch.setattr(source_pages, "fetch_page", fake_fetch)
    _, receipt = source_pages.follow_teaser_articles(
        teasers, search_client=SimpleNamespace(), detail_pages=4)
    assert paid == ["https://news.example/a"]
    assert receipt["paid_error"] and "402" in receipt["paid_error"]


def test_follow_teaser_skips_paid_when_client_marks_contents_exhausted(monkeypatch):
    """fetch_page may re-raise local 403 after Exa 402; contents_exhausted still stops later paid reads."""
    teasers = [
        SearchResult("A", "https://news.example/a", excerpts=["a"], provider="rss"),
        SearchResult("B", "https://news.example/b", excerpts=["b"], provider="rss"),
    ]
    client = SimpleNamespace(contents_exhausted="")
    paid = []

    def fake_fetch(url, limit, timeout, *, get_page, search_client=None, allow_paid=True, **k):
        if search_client is not None and allow_paid:
            paid.append(url)
            client.contents_exhausted = "HTTP 402: credits limit"
            raise RuntimeError("HTTP 403 Forbidden")  # preferred local error after quota
        raise RuntimeError("HTTP 403 Forbidden")

    monkeypatch.setattr(source_pages, "fetch_page", fake_fetch)
    _, receipt = source_pages.follow_teaser_articles(
        teasers, search_client=client, detail_pages=4)
    assert paid == ["https://news.example/a"]
    assert "402" in receipt["paid_error"]


def test_fetch_page_skips_paid_when_contents_already_exhausted(monkeypatch):
    order = []

    def get_page(url, timeout):
        order.append("http")
        raise RuntimeError("HTTP 403")

    monkeypatch.setattr(source_pages, "_read_via_browser",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("browser offline")))
    monkeypatch.setattr(source_pages, "_read_via_paid",
                        lambda *a, **k: order.append("paid") or (_ for _ in ()).throw(AssertionError("paid")))
    client = SimpleNamespace(contents_exhausted="HTTP 402: credits limit")
    with pytest.raises(RuntimeError, match="browser offline"):
        source_pages.fetch_page("https://example.com/a", 4, 10, get_page=get_page,
                                search_client=client, browser=True, allow_paid=True,
                                allow_opencode=False)
    assert order == ["http"]


def test_engine_follow_only_for_fetch_only_jobs(monkeypatch):
    from core.engine import ResearchEngine
    from core import sources

    engine = ResearchEngine.__new__(ResearchEngine)
    engine.config_dir = "config"
    engine.sys = {}
    engine._cancel = SimpleNamespace(is_set=lambda: False)
    engine._progress = None
    engine.workspace_dir = "."
    engine._source_search_client = SimpleNamespace()

    hit = SearchResult("t", "https://a.test/story", excerpts=["teaser"], provider="rss")
    monkeypatch.setattr(sources, "resolve", lambda specs, config_dir: list(specs))
    monkeypatch.setattr(sources, "fetch_all_strict", lambda specs, **k: ([hit], {specs[0]: 1}))
    monkeypatch.setattr(source_pages, "follow_teaser_articles",
                        lambda items, **k: ([SearchResult("t", items[0].url,
                                                          excerpts=["teaser", "长正文" * 40],
                                                          provider="page")],
                                           {"version": 1, "details": [{"url": items[0].url, "status": "read"}],
                                            "successful": 1, "attempted": 1, "paid_error": ""}))

    job = {"name": "science", "search": False, "sources": ["rss:https://a.test/feed"],
           "_file": "chains/ai_science_updates"}
    block, (items, stats) = engine._fetch_sources_strict(job)
    assert "长正文" in block and "正文补读" in block and stats["rss:https://a.test/feed"] == 1

    searchable = {**job, "search": True}
    monkeypatch.setattr(sources, "fetch_all_strict", lambda specs, **k: ([hit], {specs[0]: 1}))
    # searchable path is not used by _fetch_sources_strict normally; follow guard skips when search!=False
    items2, note = engine._follow_teaser_details(searchable, [hit], None)
    assert items2 == [hit] and note == ""


def test_page_cache_reuses_positive_and_negative_results_across_stages(monkeypatch):
    calls = {"http": 0, "browser": 0}

    def get_page(url, timeout):
        calls["http"] += 1
        # fetch_page may pass the raw URL (with utm); cache keys strip tracking.
        from urllib.parse import urlsplit
        if urlsplit(url).path.rstrip("/").endswith("/ok"):
            return ARTICLE
        raise RuntimeError("HTTP 403 Forbidden")

    def browser(*a, **k):
        calls["browser"] += 1
        raise RuntimeError("HTTP 403 Forbidden")

    monkeypatch.setattr(source_pages, "_read_via_browser", browser)
    with source_pages.page_cache_scope() as cache:
        first = source_pages.fetch_page("https://cache.example/ok?utm_source=x", 4, 10,
                                        get_page=get_page, allow_opencode=False)
        first[0].excerpts.append("mutated-by-caller")
        second = source_pages.fetch_page("https://cache.example/ok", 4, 10,
                                         get_page=get_page, allow_opencode=False)
        assert calls["http"] == 1
        assert "mutated-by-caller" not in second[0].excerpts

        with pytest.raises(RuntimeError, match="403"):
            source_pages.fetch_page("https://cache.example/bad", 4, 10,
                                    get_page=get_page, allow_opencode=False)
        with pytest.raises(RuntimeError, match="403"):
            source_pages.fetch_page("https://cache.example/bad", 4, 10,
                                    get_page=get_page, allow_opencode=False)
        assert calls["http"] == 2 and calls["browser"] == 1
        stats = cache.stats()
        assert stats["hits"] == 1 and stats["negative_hits"] == 1


def test_page_cache_host_hard_block_skips_slow_fallbacks(monkeypatch):
    calls = {"http": 0, "browser": 0, "opencode": 0}

    def get_page(url, timeout):
        calls["http"] += 1
        raise RuntimeError("HTTP 403 Forbidden")

    def browser(*a, **k):
        calls["browser"] += 1
        raise RuntimeError("页面未成功打开（HTTP 403）")

    def opencode(*a, **k):
        calls["opencode"] += 1
        raise RuntimeError("HTTP 403")

    monkeypatch.setattr(source_pages, "_read_via_browser", browser)
    monkeypatch.setattr(source_pages, "_read_via_opencode", opencode)
    monkeypatch.setattr("core.opencode_cli.available", lambda *a, **k: True)

    with source_pages.page_cache_scope() as cache:
        for i in range(4):
            with pytest.raises(RuntimeError, match="403"):
                source_pages.fetch_page(f"https://blocked.example/post-{i}", 4, 10,
                                        get_page=get_page, browser=True, allow_opencode=True)
        # First 2 URLs establish the host hard-block; next 2 fast-fail after HTTP without Chrome/OpenCode.
        assert calls["http"] == 4
        assert calls["browser"] == 2
        assert calls["opencode"] == 2
        assert cache.stats()["host_fast_fails"] == 2


def test_dossier_enrich_and_agent_read_webpage_share_page_cache(monkeypatch):
    from core import ai_dossier_sources, sources
    from core.research import ResearchAgent
    from core.llm import LLMConfig
    from core.search import SearchConfig

    http_calls = []

    def fake_get_page(url, timeout=15):
        http_calls.append(url)
        return ARTICLE

    monkeypatch.setattr(sources, "_get_page", fake_get_page)
    url = "https://shared.example/story"
    with source_pages.page_cache_scope() as cache:
        # Stage R6 reads the page once.
        got = source_pages.fetch_page(url, 24, 10, get_page=fake_get_page, allow_opencode=False)
        assert got and len(http_calls) == 1

        # Stage R7 (ResearchAgent.read_webpage) reuses cached content without calling SearchClient.read_page.
        agent = ResearchAgent(LLMConfig(model="m", base_url="http://localhost"), SearchConfig())
        monkeypatch.setattr(agent.search, "read_page",
                            lambda u: (_ for _ in ()).throw(AssertionError("should use cache")))
        tool_text = agent._execute_tool("read_webpage", {"url": url})
        assert "正文段落" in tool_text

        # Stage R8 (ai_dossier_sources.enrich inside ThreadPoolExecutor) also hits the same cache.
        report = f"### 1.1 标题\n- **证据来源**：{url}\n正文"
        text, receipt = ai_dossier_sources.enrich(
            {"_file": "ai_media/weekly_deep_research", "dossier_sources": True},
            report, url,
        )
        assert "正文段落" in text and receipt["pages"][0]["status"] == "read"
        assert len(http_calls) == 1
        assert cache.stats()["hits"] >= 2


def test_follow_teaser_articles_respects_time_budget(monkeypatch):
    teasers = [
        SearchResult("A", "https://slow.example/1", excerpts=["teaser 1"], provider="rss"),
        SearchResult("B", "https://slow.example/2", excerpts=["teaser 2"], provider="rss"),
    ]
    now = [100.0]
    monkeypatch.setattr(source_pages.time, "monotonic", lambda: now[0])

    def fake_fetch(url, *a, **k):
        now[0] += 5.0
        return [SearchResult("Body", url, excerpts=["完整正文。" * 30], provider="page")]

    monkeypatch.setattr(source_pages, "fetch_page", fake_fetch)
    items, receipt = source_pages.follow_teaser_articles(teasers, detail_pages=4, detail_timeout_seconds=3.0)
    assert receipt["attempted"] == 1 and receipt["successful"] == 1
    assert receipt["timed_out"] is True and receipt["skipped_by_timeout"] == 1
    assert "时间预算" in "\n".join(items[1].excerpts)

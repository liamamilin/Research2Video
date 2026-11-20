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

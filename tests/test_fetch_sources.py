"""Sources fetched directly (no web search): parsers on real saved pages, and the fail-loud rules."""

from __future__ import annotations

import json
import pathlib
import urllib.error

import pytest

from core import sources
from core.engine import ResearchEngine
from core.errors import SourcesUnavailableError
from core.llm import LLMConfig
from core.research import ResearchAgent, ResearchConfig
from core.search import SearchConfig, SearchResult

FIX = pathlib.Path(__file__).parent / "fixtures" / "sources"


def test_tldr_issue_parses_to_items_without_sponsors():
    items = sources.parse_tldr((FIX / "tldr_ai.html").read_text(), "2026-10-21")
    assert len(items) >= 10 and all(i.url.startswith("http") and i.title and i.excerpts[0] for i in items)
    assert not any("sponsor" in i.title.lower() or "doubleclick" in i.url or "tldrnewsletter" in i.url for i in items)
    assert all("utm_source" not in i.url for i in items) and items[0].publish_date == "2026-10-21"


def test_hugging_face_papers_and_models_parse():
    papers = sources.parse_hf_papers(json.loads((FIX / "hf_daily_papers.json").read_text()), 5)
    assert papers and papers[0].url.startswith("https://huggingface.co/papers/") and papers[0].excerpts[0]
    models = sources.parse_hf_models(json.loads((FIX / "hf_models.json").read_text()), 5)
    assert models[0].url == "https://huggingface.co/" + models[0].title and "downloads" in models[0].excerpts[0]


def test_github_search_and_artificial_analysis_parse():
    repos = sources.parse_gh_search(json.loads((FIX / "gh_search.json").read_text()), 5)
    assert repos[0].url.startswith("https://github.com/") and "stars" in repos[0].excerpts[0]
    rows = sources.parse_aa_models((FIX / "aa_models.html").read_text(), 10)
    assert len(rows) == 10 and "Intelligence Index" in rows[0].excerpts[0] and "cost per task" in rows[0].excerpts[0]
    # the rows share one page: de-duplicating by URL (as fetching every source does) must keep all of them
    from core.search import SearchClient
    assert len(SearchClient._dedupe(rows)) == 10


def test_a_page_that_answers_200_with_no_items_is_a_failure(monkeypatch):
    monkeypatch.setattr(sources, "_get_page", lambda url, timeout, accept="": "<html>redesigned</html>")
    with pytest.raises(ValueError, match="结构可能变了"):  # every day parses to nothing: loud, not an empty list
        sources.fetch_tldr("ai")


def test_todays_tldr_page_before_the_issue_is_out_is_skipped(monkeypatch):
    page = (FIX / "tldr_ai.html").read_text()
    monkeypatch.setattr(sources, "_get_page", lambda url, timeout, accept="": "<html></html>" if len(url.split("/")[-1]) and url.endswith(sources.datetime.date.today().isoformat()) else page)
    assert sources.fetch_tldr("ai")


def test_tldr_skips_days_without_an_issue(monkeypatch):
    page = (FIX / "tldr_ai.html").read_text()
    calls = []

    def fake(url, timeout, accept=""):
        calls.append(url)
        if len(calls) <= 2:  # a weekend
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        return page

    monkeypatch.setattr(sources, "_get_page", fake)
    items = sources.fetch_tldr("ai", limit=2)
    assert len({i.publish_date for i in items}) == 3 and len(calls) == 5  # three issues after two missing days


def _stub(monkeypatch, outcomes):
    def fake(spec, limit=8, timeout=20):
        out = outcomes[spec]
        if isinstance(out, Exception):
            raise out
        return out
    monkeypatch.setattr(sources, "fetch_source", fake)


def test_strict_fetch_reports_each_source_and_keeps_going(monkeypatch):
    hit = SearchResult(title="t", url="https://a.test", excerpts=["x"], provider="tldr")
    _stub(monkeypatch, {"a": [hit], "b": RuntimeError("HTTP 500"), "c": []})
    items, stats = sources.fetch_all_strict(["a", "b", "c"])
    assert [i.url for i in items] == ["https://a.test"]
    assert stats["a"] == 1 and "HTTP 500" in stats["b"] and "没有条目" in stats["c"]


def test_strict_fetch_with_nothing_from_any_source_raises(monkeypatch):
    _stub(monkeypatch, {"a": RuntimeError("HTTP 500"), "b": []})
    with pytest.raises(SourcesUnavailableError) as raised:
        sources.fetch_all_strict(["a", "b"])
    assert "HTTP 500" in str(raised.value) and "中止" in str(raised.value)
    with pytest.raises(SourcesUnavailableError):
        sources.fetch_all_strict([])  # a fetch-only Job with no sources is a config error, not "no news"


def test_the_engine_names_failed_sources_in_the_prompt_block(monkeypatch):
    hit = SearchResult(title="t", url="https://a.test", excerpts=["x"], provider="tldr")
    _stub(monkeypatch, {"a": [hit], "b": RuntimeError("HTTP 500")})
    engine = ResearchEngine.__new__(ResearchEngine)
    block, (items, stats) = engine._fetch_sources_strict({"sources": ["a", "b"]})
    assert "a: 1 items" in block and "b: 失败：HTTP 500" in block and "不得写成覆盖了它们" in block and items


class _FakeSearch:
    def __getattr__(self, name):
        pytest.fail("a fetch-only run touched web search")


def test_a_fetch_only_run_never_searches_and_credits_the_fetched_urls(monkeypatch):
    agent = ResearchAgent(LLMConfig(api_key="x", model="m", base_url="http://x.test"), SearchConfig(), ResearchConfig())
    agent.search = _FakeSearch()  # replaced by run_fetch_only; the original must not be reachable either
    seen = {}

    class Reply:
        content = "# report"

    def chat(messages, tools=None):
        seen["tools"], seen["system"] = tools, messages[0]["content"]
        return Reply()

    monkeypatch.setattr(agent.llm, "chat", chat)
    out = agent.run_fetch_only("task https://in-prompt.test/x", fetched_urls=["https://a.test"], stats={"tldr:ai": 12})
    assert out == "# report" and seen["tools"] is None and "ONLY the fetched" in seen["system"]
    assert {"https://a.test", "https://in-prompt.test/x"} <= agent.retrieved_urls
    assert agent.get_usage()["fetched"] == {"tldr:ai": 12} and agent.get_usage()["searches"] == 0
    with pytest.raises(RuntimeError, match="switched off"):
        agent.search.search(["q"])


def test_the_counts_are_the_items_the_model_is_given_not_the_ones_dropped_as_repeats(monkeypatch):
    same = lambda n: [sources.SearchResult(title=f"t{i}", url="https://x.test/page", excerpts=["e"]) for i in range(n)]  # noqa: E731
    monkeypatch.setattr(sources, "fetch_source", lambda spec, limit=8, timeout=20: same(5))
    items, stats = sources.fetch_all_strict(["rss:a"], 8)
    assert len(items) == 1 and stats == {"rss:a": 1}

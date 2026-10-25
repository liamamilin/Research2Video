"""When no search provider can answer, the run stops with the reasons instead of writing from memory."""

from __future__ import annotations

import pytest

import core.research as research
from core.errors import SearchUnavailableError
from core.llm import LLMConfig
from core.research import ResearchAgent, ResearchConfig
from core.search import SearchClient, SearchConfig


def _client(monkeypatch, behaviour: dict) -> SearchClient:
    """A client whose providers each raise, or return a list, as given."""
    cfg = SearchConfig(provider="parallel", fallback_provider="duckduckgo", api_keys={"parallel": "k"})
    client = SearchClient(cfg)

    def make(name):
        def call(*a, **k):
            outcome = behaviour[name]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return call

    monkeypatch.setattr(client, "_search_parallel", make("parallel"))
    monkeypatch.setattr(client, "_search_duckduckgo", make("duckduckgo"))
    return client


def test_every_provider_failing_raises_with_each_reason(monkeypatch):
    client = _client(monkeypatch, {
        "parallel": RuntimeError("HTTP 402: Insufficient credit"),
        "duckduckgo": RuntimeError("202 Ratelimit"),
    })
    with pytest.raises(SearchUnavailableError) as raised:
        client.search(["q"])
    message = str(raised.value)
    assert "parallel" in message and "402" in message and "Ratelimit" in message and "中止" in message
    assert client.calls == 0


def test_a_provider_that_answers_with_nothing_is_not_an_outage(monkeypatch):
    client = _client(monkeypatch, {"parallel": RuntimeError("HTTP 402"), "duckduckgo": []})
    assert client.search(["q"]) == []  # the search worked; there was just nothing to find


def test_a_fallback_that_answers_keeps_the_run_going(monkeypatch):
    from core.search import SearchResult

    hit = SearchResult(title="t", url="https://a.test", excerpts=["x"], provider="duckduckgo")
    client = _client(monkeypatch, {"parallel": RuntimeError("HTTP 402"), "duckduckgo": [hit]})
    assert [r.url for r in client.search(["q"])] == ["https://a.test"]


class _DeadSearch:
    calls = 0

    def __init__(self):
        self.attempts = 0

    def search(self, queries, objective="", after_date=None):
        self.attempts += 1
        raise SearchUnavailableError({"parallel": "HTTP 402"})


def _agent(monkeypatch, mode: str) -> ResearchAgent:
    monkeypatch.setattr(research, "_SEARCH_RETRY_DELAY", 0)
    agent = ResearchAgent(LLMConfig(api_key="x", model="m", base_url="http://x.test"), SearchConfig(), ResearchConfig(mode=mode))
    agent.search = _DeadSearch()
    return agent


def test_the_search_tool_ends_the_agent_run_instead_of_telling_the_model(monkeypatch):
    agent = _agent(monkeypatch, "agent")
    with pytest.raises(SearchUnavailableError):
        agent._execute_tool("search_web", {"queries": ["q"]})
    assert agent.search.attempts == 2  # one retry, then it stops


def test_the_pipeline_does_not_write_a_report_without_sources(monkeypatch):
    agent = _agent(monkeypatch, "pipeline")
    monkeypatch.setattr(agent, "_plan_queries", lambda prompt: ["q"])
    monkeypatch.setattr(agent.llm, "chat", lambda *a, **k: pytest.fail("wrote a report with no sources"))
    with pytest.raises(SearchUnavailableError):
        agent.run("task")


def test_a_transient_outage_is_retried_once(monkeypatch):
    from core.search import SearchResult

    agent = _agent(monkeypatch, "agent")
    hit = SearchResult(title="t", url="https://a.test", excerpts=["x"], provider="parallel")
    calls = []

    def flaky(queries, objective="", after_date=None):
        calls.append(1)
        if len(calls) == 1:
            raise SearchUnavailableError({"parallel": "connection failed"})
        return [hit]

    agent.search.search = flaky
    assert "https://a.test" in agent._execute_tool("search_web", {"queries": ["q"]})

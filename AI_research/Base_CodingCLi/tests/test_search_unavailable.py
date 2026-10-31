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

    def __init__(self, reason="connection failed"):
        self.attempts = 0
        self.reason = reason

    def search(self, queries, objective="", after_date=None):
        self.attempts += 1
        raise SearchUnavailableError({"parallel": self.reason})


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


def test_credits_and_rate_limits_are_not_retried_within_the_run(monkeypatch):
    agent = _agent(monkeypatch, "agent")
    agent.search = _DeadSearch("HTTP 402: NO_MORE_CREDITS")
    with pytest.raises(SearchUnavailableError):
        agent._execute_tool("search_web", {"queries": ["q"]})
    assert agent.search.attempts == 1


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


@pytest.fixture()
def health(tmp_path):
    from core import search_health
    search_health.use_state_dir(str(tmp_path))
    yield search_health
    search_health.use_state_dir(None)


@pytest.mark.parametrize("reason, kind", [
    ('HTTP 402: {"error":"You have exceeded your credits limit","tag":"NO_MORE_CREDITS"}', "permanent"),
    ("HTTP 401: invalid api key", "permanent"),
    ("HTTP 429：被限流", "rate_limit"),
    ("https://duckduckgo.com/?q=x 202 Ratelimit", "rate_limit"),
    ("connection reset by peer", None),
    ("没有返回结果", None),
])
def test_failures_are_classified_by_status_and_provider_text(reason, kind):
    from core.search_health import classify
    assert classify(reason) == kind


def test_a_provider_out_of_credits_is_skipped_until_its_cooldown_ends(monkeypatch, health):
    from core.search import SearchResult

    hit = SearchResult(title="t", url="https://a.test", excerpts=["x"], provider="duckduckgo")
    client = _client(monkeypatch, {"parallel": RuntimeError("HTTP 402: NO_MORE_CREDITS"), "duckduckgo": [hit]})
    client.search(["q"])
    monkeypatch.setattr(client, "_search_parallel", lambda *a, **k: pytest.fail("asked a provider that is out of credits"))
    assert [r.url for r in client.search(["q"])] == ["https://a.test"]
    assert "暂停使用" in client.errors["parallel"]
    later = health.PERMANENT_COOLDOWN + 1
    real = health.time.time
    monkeypatch.setattr(health.time, "time", lambda: real() + later)
    assert health.cooling() == {}


def test_every_provider_down_is_an_outage_until_one_answers(monkeypatch, health):
    from core.search import SearchResult

    client = _client(monkeypatch, {"parallel": RuntimeError("HTTP 402"), "duckduckgo": RuntimeError("202 Ratelimit")})
    with pytest.raises(SearchUnavailableError):
        client.search(["q"])
    assert set(health.outage()["reasons"]) == {"parallel", "duckduckgo"}
    # while both cool down, a search fails at once without a request
    with pytest.raises(SearchUnavailableError):
        client.search(["q"])
    health.clear()
    hit = SearchResult(title="t", url="https://a.test", excerpts=["x"], provider="duckduckgo")
    client = _client(monkeypatch, {"parallel": RuntimeError("connection failed"), "duckduckgo": [hit]})
    health.record_outage({"parallel": "HTTP 402"})
    client.search(["q"])
    assert health.outage() is None


def test_without_a_state_dir_nothing_is_shared(monkeypatch):
    from core import search_health
    search_health.use_state_dir(None)
    client = _client(monkeypatch, {"parallel": RuntimeError("HTTP 402"), "duckduckgo": RuntimeError("HTTP 402")})
    with pytest.raises(SearchUnavailableError):
        client.search(["q"])
    assert search_health.cooling() == {} and search_health.outage() is None

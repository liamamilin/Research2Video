"""Exa as a search provider (core/search.py) and the settings page's search test."""

from __future__ import annotations

import json

import pytest

from core.errors import SearchUnavailableError
from core.search import SearchClient, SearchConfig


def _config(**over) -> SearchConfig:
    base = dict(provider="exa", api_keys={"exa": "exa-key"}, max_results=5, max_chars_per_result=400,
                fallback_provider="", timeout=5)
    return SearchConfig(**{**base, **over})


class _Reply:
    def __init__(self, body):
        self._body = json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_exa_is_asked_per_query_with_its_key_and_the_page_text_comes_back_as_the_excerpt(monkeypatch):
    sent = []

    def urlopen(request, timeout=None):
        sent.append((request.full_url, {k.lower(): v for k, v in request.header_items()}, json.loads(request.data)))
        return _Reply({"results": [
            {"title": "T", "url": "https://a.test/x", "publishedDate": "2026-10-20T08:00:00.000Z",
             "text": "page text", "highlights": ["h1"]},
            {"title": "", "url": "https://a.test/y"}]})

    monkeypatch.setattr("core.search.urllib.request.urlopen", urlopen)
    hits = SearchClient(_config(after_date="2026-10-01")).search(["first", "second"])
    url, headers, body = sent[0]
    assert url == "https://api.exa.ai/search" and headers["x-api-key"] == "exa-key"
    assert body["query"] == "first" and body["numResults"] == 5
    assert body["contents"] == {"text": {"maxCharacters": 400}} and body["startPublishedDate"] == "2026-10-01T00:00:00.000Z"
    assert len(sent) == 2
    assert hits[0].url == "https://a.test/x" and hits[0].publish_date == "2026-10-20"
    assert hits[0].excerpts == ["page text", "h1"] and hits[0].provider == "exa"
    assert len(hits) == 2  # the same urls from the second query are de-duplicated


def test_a_missing_key_is_reported_by_name_not_swallowed():
    client = SearchClient(_config(api_keys={}))
    with pytest.raises(SearchUnavailableError) as raised:
        client.search(["q"])
    assert "exa" in client.errors["exa"] and "exa" in raised.value.reasons


def test_the_search_test_asks_the_chosen_provider_alone(monkeypatch):
    from web.routes import config as routes

    seen = {}

    class Fake:
        def __init__(self, cfg):
            seen["fallbacks"] = (cfg.fallback_provider, cfg.fallback_providers)
            self.errors = {"exa": "HTTP 402: out of credits"}

        def search(self, *a, **k):
            return []

    monkeypatch.setattr(routes, "SearchClient", Fake)
    out = routes.test_search({"config": {"search": {"provider": "exa", "fallback_provider": "duckduckgo"}}}, user=None)
    assert seen["fallbacks"] == ("", [])  # DuckDuckGo answering for a dead provider would read as "ok"
    assert out["ok"] is False and out["error"] == "HTTP 402: out of credits"


def test_a_provider_with_its_own_key_variable_never_falls_back_to_the_generic_key():
    # provider exa, EXA_API_KEY unset, but PARALLEL_API_KEY (the generic search.api_key) is: that key is not Exa's
    client = SearchClient(_config(api_keys={}, api_key="parallel-key", key_envs={"exa": "EXA_API_KEY"}))
    with pytest.raises(SearchUnavailableError):
        client.search(["q"])
    assert "EXA_API_KEY" in client.errors["exa"]


# --- Doubao search (Volcengine, Global edition) ---------------------------------------------------------------

def test_doubao_posts_one_phrase_per_request_with_a_bearer_key(monkeypatch):
    sent = []

    def fake_open(request, timeout=None):
        sent.append((request.full_url, dict(request.header_items()), json.loads(request.data)))
        return _Reply({"ResponseMetadata": {"RequestId": "r"}, "Result": {"ErrorCode": 0, "Documents": [
            {"Rank": 0, "Url": "https://d.test/a", "Title": "T",
             "Snippet": [{"Type": "text", "Text": "正文"}, {"Type": "image", "Image": {}}],
             "DocumentInfo": {"PublishTime": "2026-10-20 08:00:00"}}]}})

    monkeypatch.setattr("core.search.urllib.request.urlopen", fake_open)
    hits = SearchClient(_config(provider="doubao", api_keys={"doubao": "db-key"}, max_results=50)).search(["x" * 150, "second"])
    url, headers, body = sent[0]
    assert url == "https://open.feedcoopapi.com/search_api/global_search" and headers["Authorization"] == "Bearer db-key"
    assert len(body["Query"]) == 100 and body["SearchType"] == "web" and body["DocCount"] == 20  # the API's caps
    assert len(sent) == 2 and hits[0].excerpts == ["正文"] and hits[0].publish_date == "2026-10-20" and hits[0].provider == "doubao"


@pytest.mark.parametrize("body, shown", [
    ({"ResponseMetadata": {"Error": {"Code": "700901", "Message": "invalid"}}, "Result": None}, "API Key 无效"),
    ({"ResponseMetadata": {}, "Result": {"ErrorCode": 10412, "ErrorMsg": "no quota", "Documents": []}}, "搜索额度不足"),
])
def test_doubao_errors_arrive_as_http_200_and_are_not_an_empty_result(monkeypatch, body, shown):
    monkeypatch.setattr("core.search.urllib.request.urlopen", lambda request, timeout=None: _Reply(body))
    client = SearchClient(_config(provider="doubao", api_keys={"doubao": "k"}))
    with pytest.raises(SearchUnavailableError):
        client.search(["q"])
    assert shown in client.errors["doubao"]


def test_doubao_without_a_key_names_the_variable():
    client = SearchClient(_config(provider="doubao", api_keys={}, key_envs={"doubao": "DOUBAO_SEARCH_API_KEY"}))
    with pytest.raises(SearchUnavailableError):
        client.search(["q"])
    assert "DOUBAO_SEARCH_API_KEY" in client.errors["doubao"]

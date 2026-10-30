"""Discovery promotes observed readable endpoints, not invented model URLs."""
import json
from threading import Event
from types import SimpleNamespace

import pytest

from core import source_discovery as discovery
from core.search import SearchResult
from core.source_supply_store import SupplyStore


def test_endpoint_discovery_uses_feed_and_section_links_not_single_news_article():
    html = '<title>Vendor</title><link rel="alternate" type="application/rss+xml" href="/feed.xml">' \
           '<nav><a href="/blog">Blog</a><a href="/login">Login</a></nav>'
    got = discovery.endpoints("https://vendor.example/blog/one-story", html)
    assert {r["spec"] for r in got} == {"rss:https://vendor.example/feed.xml", "catalog:https://vendor.example/blog"}
    assert discovery.endpoints("https://vendor.example/random/article", "<h1>Article</h1>") == []


def test_model_assignment_only_accepts_observed_ids_and_known_columns():
    candidates = [{"id": "one", "spec": "rss:https://real.example/feed"}]
    reply = json.dumps({"sources": [{"id": "invented", "jobs": ["job"]},
                                    {"id": "one", "jobs": ["unknown", "job"], "url": "https://invented.example"}]})
    got = discovery.select(reply, candidates, {"job": {}})
    assert len(got) == 1 and got[0]["spec"] == candidates[0]["spec"] and got[0]["jobs"] == ["job"]


def test_complete_discovery_activates_once_and_keeps_failed_trials(tmp_path, monkeypatch):
    from core.video import screenshots
    monkeypatch.setattr(screenshots, "public_url", lambda url: url)
    engine = SimpleNamespace(config_dir=str(tmp_path / "config"), workspace_dir=str(tmp_path),
                             _cancel=Event(), _progress=lambda *a: None, sys={})
    profile = {"key": "pilot", "jobs": {"job": {"query": "AI release notes"}}, "discovery_probes": 3}
    search = SimpleNamespace(calls=1, search=lambda *a, **k: [SearchResult("Vendor", "https://vendor.example/blog")])
    def reply(messages, **k):
        candidates = json.loads(messages[1]["content"])["candidates"]
        return SimpleNamespace(content=json.dumps({"sources": [{"id": r["id"], "jobs": ["job"]} for r in candidates]}))
    llm = SimpleNamespace(chat=reply, usage={"total_tokens": 10})
    def probe(spec, **k):
        if spec.startswith("rss:"):
            raise RuntimeError("HTTP 503")
        return [SearchResult("AI release", "https://vendor.example/blog/1", excerpts=["concrete workflow"])]
    html = '<title>Vendor</title><link rel="alternate" type="application/rss+xml" href="/feed">' \
           '<a href="/blog">Blog</a>'
    got = discovery.discover(engine, profile, search=search, llm=llm, get_page=lambda *a: html, probe=probe)
    assert got["activated"] and any(r.get("error") == "HTTP 503" for r in got["trials"])
    count = len(SupplyStore(tmp_path / "state", "pilot").pool()["sources"])
    discovery.discover(engine, profile, search=search, llm=llm, get_page=lambda *a: html, probe=probe)
    assert len(SupplyStore(tmp_path / "state", "pilot").pool()["sources"]) == count
    assert not (tmp_path / "config/sources.yaml").exists()


def test_invalid_model_structure_fails_instead_of_silent_empty_success():
    with pytest.raises(ValueError):
        discovery.select('{"items":[]}', [], {})


def test_page_catalog_and_english_locale_aliases_share_a_discovery_identity():
    assert discovery.endpoint_key("catalog:https://vendor.example/en-US/blog") == discovery.endpoint_key("page:https://vendor.example/blog/")
    assert discovery.endpoint_key("rss:https://vendor.example/feed") != discovery.endpoint_key("page:https://vendor.example/feed")


def test_discovery_budget_explores_each_column_before_extra_endpoints(tmp_path, monkeypatch):
    from core.video import screenshots
    monkeypatch.setattr(screenshots, "public_url", lambda url: url)
    engine = SimpleNamespace(config_dir=str(tmp_path / "config"), workspace_dir=str(tmp_path),
                             _cancel=Event(), _progress=lambda *a: None, sys={})
    profile = {"key": "pilot", "jobs": {f"j{i}": {"query": str(i)} for i in range(5)}, "discovery_probes": 5}
    search = SimpleNamespace(calls=5, search=lambda qs, **k: [SearchResult("Vendor", f"https://v{qs[0]}.example/blog")])
    tried = []
    def probe(spec, **k):
        tried.append(spec)
        return [SearchResult("Example", "https://source.example", excerpts=["readable source"])]
    llm = SimpleNamespace(chat=lambda *a, **k: SimpleNamespace(content='{"sources":[]}'), usage={})
    discovery.discover(engine, profile, search=search, llm=llm, probe=probe,
        get_page=lambda *a: '<link rel="alternate" type="application/rss+xml" href="/feed"><a href="/blog">Blog</a>')
    assert len({__import__('urllib.parse', fromlist=['urlsplit']).urlsplit(s.partition(':')[2]).hostname for s in tried}) == 5

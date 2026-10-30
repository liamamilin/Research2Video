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


def test_institutional_entry_paths_keep_collections_and_reports_out_of_one_off_items():
    page = '<title>Museum</title><a href="/collections">Collections</a><a href="/stories">Stories</a>'
    rows = discovery.endpoints('https://museum.example/stories/a-discovery', page, paths=['collections', 'stories'])
    assert {r['spec'] for r in rows} == {'catalog:https://museum.example/collections', 'catalog:https://museum.example/stories'}
    assert not discovery.recurring_path('https://museum.example/collections/object-123', ['collections'])


def test_source_discovery_angles_rotate_weekly_without_extra_queries():
    profile={'jobs':{'one':{'query':['first angle','second angle']},'two':{'query':'fixed angle'}},'discovery_queries':2}
    assert discovery.search_queries(profile,now=0) == ['first angle','fixed angle']
    assert discovery.search_queries(profile,now=604800) == ['second angle','fixed angle']


def test_a_source_registered_for_another_matrix_is_still_available(tmp_path):
    config, jobs=tmp_path/'config',tmp_path/'jobs'
    config.mkdir(); jobs.mkdir()
    (config/'sources.yaml').write_text('museum:\n  spec: rss:https://museum.example/feed\n')
    (jobs/'one.yaml').write_text('sources: [source:museum]\n')
    (jobs/'two.yaml').write_text('sources: [rss:https://other.example/feed]\n')
    engine=SimpleNamespace(config_dir=str(config),jobs_dir=str(jobs))
    profile={'jobs':{'one':{},'two':{}}}
    assert discovery.declared_everywhere(engine,profile) == set()
    (jobs/'two.yaml').write_text('sources: [source:museum]\n')
    assert discovery.declared_everywhere(engine,profile) == {'rss:https://museum.example/feed'}


def test_chinese_institution_sections_and_rss_anchors_are_real_discovery_entries():
    page='<nav><a href="/xwzx/kyjz/">研究进展</a><a href="/rss.xml">RSS</a></nav>'
    rows=discovery.endpoints('https://institute.example/',page)
    assert {r['spec'] for r in rows} == {'catalog:https://institute.example/xwzx/kyjz', 'rss:https://institute.example/rss.xml'}


def test_legacy_absolute_dot_segments_are_normalized_before_trial():
    rows=discovery.endpoints('https://institute.example/kj/', '<a href="https://institute.example/../../syky">科研动态</a>')
    assert rows[0]['spec']=='catalog:https://institute.example/syky'


def test_institution_home_requires_real_article_cards_to_be_a_catalog():
    page='<title>Institute News</title><main>'+''.join(
        f'<a href="/news/result-{i}">Concrete experiment and observation {i}</a>' for i in range(3))+'</main>'
    rows=discovery.endpoints('https://institute.example/language/zh/home',page)
    assert any(r['spec']=='catalog:https://institute.example/language/zh/home' for r in rows)
    assert not discovery.endpoints('https://institute.example/', '<main><p>Welcome to the institute</p></main>')


def test_discovery_uses_existing_contents_reader_when_http_is_refused(tmp_path, monkeypatch):
    from core.video import screenshots
    monkeypatch.setattr(screenshots, 'public_url', lambda url: url)
    engine = SimpleNamespace(config_dir=str(tmp_path/'config'), workspace_dir=str(tmp_path),
                             _cancel=Event(), _progress=lambda *a: None, sys={})
    profile={'key':'pilot','jobs':{'job':{'query':'institute news'}},'discovery_probes':1}
    search=SimpleNamespace(calls=1, search=lambda *a,**k:[SearchResult('Institute','https://institute.example/')],
        read_page=lambda url:SearchResult('Institute',url,excerpts=['[RSS](https://institute.example/feed)']))
    llm=SimpleNamespace(chat=lambda *a,**k:SimpleNamespace(content='{"sources":[]}'),usage={})
    def blocked(*a):
        raise OSError('HTTP 403')
    result=discovery.discover(engine,profile,search=search,llm=llm,get_page=blocked,
        probe=lambda *a,**k:[SearchResult('Research story','https://institute.example/news/1',excerpts=['real process'])])
    assert result['candidates']==1
    assert any(t.get('reader')=='contents' for t in result['trials'])

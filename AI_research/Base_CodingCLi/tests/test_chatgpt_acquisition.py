"""The acquisition matrix reads public material and keeps its nine topic routes distinct."""
from pathlib import Path

import pytest

from core import matrices, source_history, sources
from core.config import load_job
from core.engine import ResearchEngine
from core.errors import CancelledError
from core.search import SearchClient, SearchConfig, SearchResult
from core.source_pages import fetch_page, parse_catalog, parse_page
from core.video import content_columns, production, topic_store

ROOT = Path(__file__).resolve().parents[1]


def test_nine_categories_have_sources_unique_topic_ids_and_usable_video_bindings():
    config, jobs = str(ROOT / "config"), str(ROOT / "jobs")
    matrix = matrices.load_all(config)["chatgpt_acquisition"]
    assert not matrix.errors and len(matrix.jobs) == 9
    configs = production.load_all(config, jobs)
    columns = content_columns.load_all(config, configs=configs)
    registry = sources.load_registry(config)
    assert {f"chatgpt_s{i:02d}" for i in range(1, 7)} <= {tag for s in registry.values() for tag in s["tags"]}
    prefixes = []
    for number, job_name in enumerate(matrix.jobs, 1):
        job = load_job(jobs, job_name)
        assert sources.resolve(job["sources"], config) and job["search"] is False
        source = topic_store.sources(jobs)[job["topics"]["alias"]]
        assert not source.error
        prefixes.append(source.prefix)
        pid = "chatgpt_" + job_name.rsplit("/", 1)[-1]
        assert not configs[pid].errors
        assert not columns[pid].errors and columns[pid].name.startswith(f"T{number:02d}")
        report = "### 测试选题\n- **一句话**：从输入到结果\n- **来源网址**：https://example.org/article\n\n## 来源清单\nhttps://example.org/article"
        parsed = topic_store.parse(source, report, "2026-10-27")
        assert len(parsed) == 1 and parsed[0].code == source.prefix + "1"
    assert len(set(prefixes)) == 9


def test_page_sections_keep_the_correct_date_and_exclude_navigation():
    page = """<title>Updates</title><nav>Navigation garbage</nav><article><h1>Product updates</h1>
    <h2>October 26, 2026</h2><h3 id="slides">Slides</h3><p>Create and edit a presentation from your files.</p>
    <a href="/guide">Read the complete guide</a><h2>October 20, 2026</h2><h3>Locked chats</h3>
    <p>Keep selected conversations behind your account PIN.</p><script>secret script</script></article>"""
    rows = parse_page(page, "https://example.org/updates")
    assert [r.publish_date for r in rows] == ["2026-10-26", "2026-10-20"]
    assert rows[0].url == "https://example.org/updates#slides"
    assert "https://example.org/guide" in rows[0].excerpts[0]
    assert not any("garbage" in str(r) or "secret script" in str(r) for r in rows)


def test_catalog_keeps_distinct_video_and_episode_ids_and_each_cards_date():
    page = """<main><h1>Videos</h1><ul><li>
    <a href="https://www.youtube.com/watch?v=one&utm_source=test"><h3>First practical tutorial</h3>Aug 25, 2026</a>
    <a href="https://www.youtube.com/watch?v=two"><h3>Second useful tutorial</h3>Jul 29, 2026</a>
    <a href="https://podcasts.apple.com/us/podcast/show/id1?i=1">Episode one practical workflow</a>
    <a href="https://podcasts.apple.com/us/podcast/show/id1?i=2">Episode two useful workflow</a>
    </li></ul><a href="/login">Sign in to the service</a></main>"""
    rows = parse_catalog(page, "https://example.org/videos", limit=8)
    assert len({r.url for r in rows}) == 4
    assert [r.publish_date for r in rows[:2]] == ["2026-08-25", "2026-07-29"]
    assert rows[0].url.endswith("?v=one") and rows[-1].url.endswith("?i=2")


def test_official_youtube_feed_keeps_its_public_description():
    xml = '''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">
    <entry><title>ChatGPT tutorial</title><link href="https://www.youtube.com/watch?v=one"/>
    <published>2026-10-27T08:00:00Z</published><media:group><media:description>Use files to make a presentation.</media:description>
    </media:group></entry></feed>'''
    row = sources._parse_feed(xml, 4)[0]
    assert row.excerpts == ["Use files to make a presentation."] and row.url.endswith("?v=one")


def test_blocked_page_uses_exact_url_contents_not_unrelated_search(monkeypatch):
    client = SearchClient(SearchConfig(api_keys={"exa": "not-a-real-key"}))
    calls = []
    def post(url, payload, **kwargs):
        calls.append((url, payload))
        return {"results": [{"url": "https://example.org/pricing", "title": "Pricing",
                              "text": "## Plus\n\nA paid plan with these specific capabilities for regular users."}]}
    monkeypatch.setattr(client, "_post_json", post)
    def blocked(*args):
        raise OSError("403 Forbidden")
    rows = fetch_page("https://example.org/pricing", 8, 20, get_page=blocked, search_client=client)
    assert rows and calls[0][1]["urls"] == ["https://example.org/pricing"]
    assert calls[0][1]["maxAgeHours"] == 0
    monkeypatch.setattr(client, "_post_json", lambda *a, **k: {"results": [{"url": "https://other.org", "text": "wrong"}]})
    with pytest.raises(RuntimeError, match="指定页面"):
        client.read_page("https://example.org/pricing")


def test_fixed_source_search_filters_out_other_sites_and_counts_usage(monkeypatch):
    client = SearchClient(SearchConfig(provider="exa"))
    monkeypatch.setattr(client, "_search_exa", lambda *a, **k: [
        SearchResult("discussion", "https://www.reddit.com/r/ChatGPT/comments/123"),
        SearchResult("wrong community", "https://www.reddit.com/r/other/comments/123"),
        SearchResult("wrong host", "https://reddit.com.evil.org/r/ChatGPT/123")])
    rows = sources.fetch_source("search:site:reddit.com/r/ChatGPT/ Plus worth", search_client=client)
    assert len(rows) == 1 and rows[0].title == "discussion" and client.calls == 1


def test_source_cancellation_is_not_downgraded_to_partial_material(monkeypatch):
    monkeypatch.setattr(sources, "fetch_source", lambda *a, **k: (_ for _ in ()).throw(CancelledError("cancelled")))
    for fetch in (sources.fetch_all, sources.fetch_all_strict):
        with pytest.raises(CancelledError):
            fetch(["page:https://example.org"])


def test_history_only_advances_after_report_success_and_preserves_versions(tmp_path):
    a = SearchResult("Plus", "https://example.org/pricing#plus", excerpts=["Price 20"], provider="page")
    first, pending = source_history.prepare(tmp_path, "job", [a])
    assert "首次采集" in first and "新增 1" in first
    retry, _ = source_history.prepare(tmp_path, "job", [a])
    assert "新增 1" in retry  # failed writing did not consume the change
    source_history.commit(pending)
    same, _ = source_history.prepare(tmp_path, "job", [a])
    assert "未变化 1" in same and "暂无新题材" in same and "Price 20" not in same
    b = SearchResult("Plus", a.url, excerpts=["Price 25"], provider="page")
    changed, pending = source_history.prepare(tmp_path, "job", [b])
    assert "修改 1" in changed and "Price 20" in changed and "Price 25" in changed
    source_history.commit(pending)
    assert len(list(Path(pending["folder"]).glob("*.json"))) == 3
    other, _ = source_history.prepare(tmp_path, "other-job", [b])
    assert "首次采集" in other


def test_engine_exposes_page_changes_but_does_not_commit_before_writing(tmp_path, monkeypatch):
    engine = ResearchEngine.__new__(ResearchEngine)
    engine.config_dir = "config"
    engine.workspace_dir = str(tmp_path)
    item = SearchResult("Title", "https://example.org", excerpts=["Substantive source content"])
    monkeypatch.setattr(sources, "fetch_source", lambda *a, **k: [item])
    block, (items, stats) = engine._fetch_sources_strict({"name": "job", "source_history": True, "sources": ["rss:test"]})
    assert "信息源变化" in block and "Substantive source content" in block and stats == {"rss:test": 1}
    assert engine._source_pending and not (tmp_path / "state/source_history").exists()

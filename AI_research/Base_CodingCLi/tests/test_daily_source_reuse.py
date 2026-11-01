"""Shared public inputs and empty-report behavior of the existing daily Jobs."""
from contextlib import contextmanager
from dataclasses import replace

import pytest

from core import source_supply as supply, sources
from core.config import load_job
from core.search import SearchResult
from core.source_pages import fetch_page
from tests.test_source_supply import setup, JOB, item  # noqa: F401


def test_public_markdown_keeps_release_sections_and_year_without_content_api():
    text = """# API updates
### October, 2026
#### Oct 30
Released GPT-6 Rosalind with biological reasoning and scientific tools.
#### Oct 30
A separate same-day update adds another scientific tool interface.
#### Oct 28
Eligible subscribers can activate API credits from their billing settings.
"""
    client = type("Client", (), {"read_page": lambda *a: pytest.fail("public Markdown needs no content API")})()
    rows = fetch_page("https://docs.example/changelog.md", 8, 15,
                      get_page=lambda *a: text, search_client=client)
    assert [r.publish_date for r in rows] == ["2026-10-30", "2026-10-28"]
    assert "Rosalind" in rows[0].excerpts[0] and "credits" in rows[1].excerpts[0]
    assert 'separate same-day update' in rows[0].excerpts[1]


def test_catalog_uses_existing_anonymous_browser_when_content_api_is_unavailable(monkeypatch):
    from core.video import screenshots
    monkeypatch.setattr(sources, "_get_page", lambda *a: (_ for _ in ()).throw(OSError("403")))
    client = type("Client", (), {"read_page": lambda *a: (_ for _ in ()).throw(RuntimeError("402"))})()
    class Page:
        url = "https://docs.example/videos"

        def wait_for_function(self, *a, **kw):
            pass

        def content(self):
            return '<main><a href="/videos/accounts"><h3>Switch between accounts</h3></a></main>'
    @contextmanager
    def public_page(url, **kwargs):
        assert url == Page.url
        yield Page()
    monkeypatch.setattr(screenshots, "page_for_capture", public_page)
    rows = supply.read_page(Page.url, catalog=True, search_client=client)
    assert len(rows) == 1 and rows[0].url.endswith('/videos/accounts')


def test_chatgpt_reuses_ai_raw_archive_when_its_live_read_fails(setup, monkeypatch):
    engine, job, profile, _ = setup
    spec = "rss:https://updates.example/feed"
    job.update(sources=[spec], _issue_id="daily-ai")
    actual = replace(item(provider="rss"), title="ChatGPT account switching",
                     excerpts=["Stay signed in to two accounts. Chats and subscriptions stay separate."])
    monkeypatch.setattr(sources, "fetch_source", lambda *a, **k: [actual])
    supply.collect(engine, job, profile)
    cg_job = {**job, "_file": "chatgpt_acquisition/t09_updates", "_issue_id": "daily-chatgpt"}
    cg_profile = {**profile, "key": "chatgpt", "jobs": {cg_job['_file']: {"mode": "changes"}}}
    monkeypatch.setattr(sources, "fetch_source", lambda *a, **k: (_ for _ in ()).throw(OSError("timeout")))
    rows, _, _, receipt = supply.collect(engine, cg_job, cg_profile)
    assert any("two accounts" in str(r.excerpts) for r in rows)
    assert receipt['sources'][spec]['cache_items'] == 1
    assert receipt['sources'][spec]['live_status'] == 'unavailable'
    assert not receipt['quiet']


def test_initial_observation_can_still_supply_a_dated_current_announcement(setup, monkeypatch):
    engine, job, profile, _ = setup
    job['_collection_window'] = {'days': 1, 'start': '2026-10-30T00:00:00+08:00',
                                 'end': '2026-10-31T00:00:00+08:00', 'timezone': 'Asia/Shanghai'}
    monkeypatch.setattr(supply, 'read_page', lambda *a, **k: [replace(item(), publish_date='2026-10-30')])
    rows, _, _, receipt = supply.collect(engine, job, profile)
    assert '首次观察材料' in rows[0].excerpts[0]
    assert '仍可作为本期选题' in supply.material_block(rows, receipt)


def test_daily_jobs_share_source_identities_without_new_jobs():
    config, jobs = 'config', 'jobs'
    ai = load_job(jobs, JOB)
    cg = load_job(jobs, 'chatgpt_acquisition/t09_updates')
    common = set(sources.resolve(ai['sources'], config)) & set(sources.resolve(cg['sources'], config))
    assert {'rss:https://openai.com/news/rss.xml', 'rss:https://aicatchup.com/rss.xml',
            'rss:https://ratelimitnow.com/whats-new.xml',
            'page:https://learn.chatgpt.com/docs/whats-new.md',
            'page:https://developers.openai.com/api/docs/changelog.md'} <= common


def test_public_picture_retains_paragraph_intent_without_guessing_a_subject():
    from core.video.visual_materials import needs_for
    version = {'sections': [{'title': '切换账号', 'paragraphs': [
        {'text': 'ChatGPT 可以保留两个账号，打开账号菜单切换。',
         'citations': ['https://news.example/guides/chatgpt-account-switching']}]}]}
    [need] = needs_for(version)
    assert not need.get('visual_subject')
    assert '公开资料' in need['visual_goal']


def test_chinese_tutorial_words_match_the_supplied_english_menu_steps():
    from core.video.visual_matching import terms
    assert {'account', 'switch', 'add', 'settings', 'subscription'} <= terms('在设置里添加并切换账号，订阅各自独立。')

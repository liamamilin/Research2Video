"""Real delivery boundaries: late page changes, failed reports, article depth and pool rotation."""
from dataclasses import replace
from datetime import datetime
import json
from threading import Event
from types import SimpleNamespace

import pytest
import yaml

from core import source_supply as supply, sources
from core.errors import CancelledError, SourcesUnavailableError
from core.search import SearchResult
from core.source_supply_store import SupplyStore

JOB = "monitoring/ai_flash_discovery"


def item(n=1, text="original", provider="page"):
    return SearchResult(f"Section {n}", f"https://source.example/notes#s{n}", excerpts=[text], provider=provider)


@pytest.fixture
def setup(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    profile = {"key": "pilot", "jobs": {JOB: {"mode": "changes"}}, "scan_limit": 80,
               "detail_pages": 0, "material_chars": 100000, "pool_per_job": 2}
    client = SimpleNamespace(calls=0)
    engine = SimpleNamespace(config_dir=str(config), workspace_dir=str(tmp_path), _cancel=Event(),
                             _source_options=lambda specs: {"search_client": client})
    job = {"_file": JOB, "sources": ["page:https://source.example/notes"], "sources_limit": 2}
    return engine, job, profile, SupplyStore(tmp_path / "state", "pilot")


def test_late_section_change_is_delivered_before_unchanged_front_page(setup, monkeypatch):
    engine, job, profile, storage = setup
    rows = [item(i) for i in range(25)]
    monkeypatch.setattr(supply, "read_page", lambda *a, **k: rows)
    _, _, initial, _ = supply.collect(engine, job, profile)
    # Initial scan establishes background, without seven successive batches of old news.
    storage.commit(initial, "background", "first.md")
    assert supply.collect(engine, job, profile)[0] == []
    rows[-1] = item(24, "New allowance doubled. Full user task remains available.")
    got, _, pending, receipt = supply.collect(engine, job, profile)
    assert len(got) == 1 and "doubled" in got[0].excerpts[0]
    assert "上次已交付内容" in got[0].excerpts[0]
    assert receipt["sources"][job["sources"][0]]["scanned"] == 25
    assert supply.section_key(rows[-1]) in pending["delivered"]


def test_failed_report_does_not_consume_changes_and_overflow_is_retained(setup, monkeypatch):
    engine, job, profile, storage = setup
    rows = [item(i) for i in range(4)]
    monkeypatch.setattr(supply, "read_page", lambda *a, **k: rows)
    storage.commit(supply.collect(engine, job, profile)[2], "background", "first.md")
    rows[:] = [replace(r, excerpts=["new " + str(i)]) for i, r in enumerate(rows)]
    first = supply.collect(engine, job, profile)
    repeated = supply.collect(engine, job, profile)  # writer failed; no commit
    assert [r.url for r in first[0]] == [r.url for r in repeated[0]]
    storage.commit(first[2], "report", "second.md")
    remaining = supply.collect(engine, job, profile)
    assert len(remaining[0]) == 2
    assert not {r.url for r in remaining[0]} & {r.url for r in first[0]}


@pytest.mark.parametrize("provider", ["rss", "catalog", "tldr", "gh_new", "github", "arxiv"])
def test_teaser_follows_article_and_failed_detail_preserves_teaser(setup, monkeypatch, provider):
    engine, job, profile, _ = setup
    job["sources"] = ["rss:https://source.example/feed"]
    profile["detail_pages"] = 2
    monkeypatch.setattr(sources, "fetch_source", lambda *a, **k: [item(provider=provider, text="short teaser")])
    monkeypatch.setattr(supply, "read_page", lambda *a, **k: [item(9, "Concrete input → process → usable output.")])
    got, _, _, receipt = supply.collect(engine, job, profile)
    assert "usable output" in "\n".join(r.to_markdown() for r in got)
    assert receipt["details"][0]["status"] == "read"
    monkeypatch.setattr(supply, "read_page", lambda *a, **k: (_ for _ in ()).throw(ValueError("unavailable")))
    got, _, _, receipt = supply.collect(engine, job, profile)
    assert "short teaser" in got[0].excerpts[0] and receipt["details"][0]["error"] == "unavailable"


def test_context_columns_keep_current_comparison_not_just_news(setup, monkeypatch):
    engine, job, profile, storage = setup
    profile["jobs"][JOB]["mode"] = "context"
    monkeypatch.setattr(supply, "read_page", lambda *a, **k: [item(text="price 25; latency 80 ms")])
    storage.commit(supply.collect(engine, job, profile)[2], "context", "first.md")
    assert "当前背景" in supply.collect(engine, job, profile)[0][0].excerpts[0]


@pytest.mark.parametrize("status,budget,expected", [("deferred", 100000, 1), ("deferred", 1, 0), ("selected", 100000, 0)])
def test_unchanged_sources_reoffer_unused_material_without_calling_it_new(setup, monkeypatch, status, budget, expected):
    engine, job, profile, storage = setup
    profile['retry_items'] = 3
    now = [100000.0]
    monkeypatch.setattr(supply.time, 'time', lambda: now[0])
    monkeypatch.setattr(supply, 'read_page', lambda *a, **k: [item()])
    pending = supply.collect(engine, job, profile)[2]
    material_id = pending['candidates'][0]['id']
    report = '<!-- material-use: ' + json.dumps({status: [material_id]}) + ' -->'
    storage.commit(pending, report, 'first.md')
    now[0] += 90000
    profile['material_chars'] = budget
    got, _, pending, receipt = supply.collect(engine, job, profile)
    assert not any(row['changed'] for row in receipt['sources'].values())
    assert len(got) == receipt['retry_items'] == expected
    assert receipt['quiet'] is (not expected)
    if expected:
        assert pending['candidates'][0]['id'] == material_id
        assert '此前未采用' in supply.material_block(got, receipt)
        assert '原发布日期' in supply.material_block(got, receipt)
        assert '未记录为已采用' in got[0].to_markdown()


def test_full_length_section_is_not_dropped_by_its_markdown_header(setup, monkeypatch):
    engine, job, profile, _ = setup
    job["sources"] = ["rss:https://source.example/feed"]
    profile.update(detail_pages=1, detail_chars=30000)
    monkeypatch.setattr(sources, "fetch_source", lambda *a, **k: [item(provider="rss", text="teaser")])
    monkeypatch.setattr(supply, "read_page", lambda *a, **k: [item(9, "x" * 24000)])
    got, _, _, receipt = supply.collect(engine, job, profile)
    assert receipt["details"][0]["sections"] == 1 and len(got[-1].excerpts[0]) == 24000


def test_one_failed_source_does_not_block_other_and_all_fail_is_not_quiet_day(setup, monkeypatch):
    engine, job, profile, _ = setup
    job["sources"].append("rss:https://bad.example/feed")
    monkeypatch.setattr(supply, "read_page", lambda *a, **k: [item()])
    def fail(*a, **k):
        raise ValueError("HTTP 403")
    monkeypatch.setattr(sources, "fetch_source", fail)
    got, stats, _, receipt = supply.collect(engine, job, profile)
    assert got and "403" in stats[job["sources"][1]]
    assert receipt["sources"][job["sources"][1]]["error"]
    monkeypatch.setattr(supply, "read_page", fail)
    with pytest.raises(SourcesUnavailableError):
        supply.collect(engine, job, profile)


@pytest.mark.parametrize('adopted', [False, True])
def test_unavailable_sources_can_still_deliver_eligible_deferred_material(setup, monkeypatch, adopted):
    engine, job, profile, storage = setup
    profile['retry_items'] = 3
    now = [100000.0]
    monkeypatch.setattr(supply.time, 'time', lambda: now[0])
    monkeypatch.setattr(supply, 'read_page', lambda *a, **k: [item()])
    pending = supply.collect(engine, job, profile)[2]
    key = pending['candidates'][0]['id']
    status = 'selected' if adopted else 'deferred'
    storage.commit(pending, '<!-- material-use: ' + json.dumps({status: [key]}) + ' -->', 'first.md')
    now[0] += 90000
    monkeypatch.setattr(supply, 'read_page', lambda *a, **k: (_ for _ in ()).throw(OSError('offline')))
    if adopted:
        with pytest.raises(SourcesUnavailableError):
            supply.collect(engine, job, profile)
    else:
        rows, _, _, receipt = supply.collect(engine, job, profile)
        assert len(rows) == receipt['retry_items'] == 1 and not receipt['quiet']
        assert all(row['scanned'] == 0 and row['error'] for row in receipt['sources'].values())
        assert '原发布日期' in supply.material_block(rows, receipt)


def test_cancellation_keeps_baseline(setup, monkeypatch):
    engine, job, profile, storage = setup
    engine._cancel.set()
    with pytest.raises(CancelledError):
        supply.collect(engine, job, profile)
    assert storage.baseline(JOB) == {"items": {}}


def test_pool_is_scoped_merged_and_rotates_without_changing_manual_registry(setup, monkeypatch):
    engine, job, profile, storage = setup
    fixed = engine.config_dir + "/sources.yaml"
    with open(fixed, "w") as handle:
        handle.write("manual:\n  spec: rss:https://manual.example/feed\n")
    storage.add({"spec": "rss:https://auto.example/feed", "name": "Auto", "jobs": [JOB], "origin": "search"})
    storage.add({"spec": "rss:https://auto.example/feed", "name": "Auto", "jobs": ["other/job"], "origin": "search"})
    assert len(storage.pool()["sources"]) == 1
    assert set(storage.pool()["sources"][0]["jobs"]) == {JOB, "other/job"}
    calls = []
    monkeypatch.setattr(sources, "fetch_source", lambda spec, **k: calls.append(spec) or [item(3, provider="rss")])
    monkeypatch.setattr(supply, "read_page", lambda *a, **k: [item()])
    _, _, pending, _ = supply.collect(engine, job, profile)
    storage.commit(pending, "https://source.example/notes", "report.md")
    row = storage.pool()["sources"][0]
    assert calls == ["rss:https://auto.example/feed"]
    assert row["stats"]["used"] == 1 and row["next_at"][JOB] > pending["at"]
    assert "auto.example" not in open(fixed).read()
    pool = [dict(row, stats={"used": 50}, last={"at": 50}, next_at={}),
            dict(row, spec="new", stats={}, last={}, next_at={}),
            dict(row, spec="other", jobs=["other/job"], next_at={})]
    assert {r["spec"] for r in supply.choose_pool(pool, JOB, 2, 100)} == {row["spec"], "new"}


def test_page_reader_retains_detail_after_original_section_cutoff(monkeypatch):
    monkeypatch.setattr(sources, "_get_page", lambda *a: "<article><h1>Update</h1><p>" + "a" * 7000 +
                        " usage doubled with concrete workflow</p></article>")
    got = supply.read_page("https://source.example/notes", browser=False)
    assert "usage doubled" in got[0].excerpts[0]


def test_ai_profile_keeps_its_five_research_jobs():
    from core.video.research_dossier import PRODUCTIONS
    configured = supply.profiles("config")["ai_media"]
    assert set(configured["jobs"]) == {PRODUCTIONS[key] for key in ["flash_daily", "ai_media_opensource_digest",
           "ai_media_arena_digest", "ai_media_business_digest", "ai_media_deep_dive"]}
    assert supply.for_job("config", {"_file": "science/not_a_column"}) is None


def test_professional_catalog_reads_news_paths_and_short_chinese_titles():
    from core.source_pages import parse_catalog
    rows=parse_catalog('<main><a href="/news/experiment">新型材料实验过程</a>'
                       '<a href="/publications/one">A concrete research method</a>'
                       '<a href="/login">Login</a></main>','https://institute.example/news')
    assert len(rows)==2 and rows[0].title=='新型材料实验过程'


def test_source_catalog_exposes_automatic_pool_as_read_only(setup):
    from web.services.source_catalog import catalog
    engine, job, profile, storage = setup
    folder = __import__("pathlib").Path(engine.config_dir) / "source_supply"
    folder.mkdir()
    (folder / "pilot.yaml").write_text(yaml.safe_dump({**profile, "jobs": {JOB: {"direction": "AI updates"}}}))
    storage.add({"spec": "rss:https://auto.example/feed", "name": "Auto", "jobs": [JOB], "reason": "Useful release details"})
    result = catalog(engine.config_dir, str(folder / "no-jobs"), engine.workspace_dir + "/state")
    row = result["sources"][0]
    assert row["automatic"] and row["id"] == "" and row["used_by"][0]["job"] == JOB
    assert "报告引用 0" in row["note"]


def test_engine_routes_supply_but_other_jobs_keep_original_adapter(setup, monkeypatch):
    from core.engine import ResearchEngine
    engine, job, profile, _ = setup
    monkeypatch.setattr(supply, "for_job", lambda *a: profile)
    monkeypatch.setattr(supply, "collect", lambda *a: ([item()], {"page": 1}, {"p": 1},
                        {"first_collection": True}))
    actual = ResearchEngine.__new__(ResearchEngine)
    actual.config_dir = engine.config_dir
    block, (got, stats) = actual._fetch_sources_strict(job)
    assert "矩阵信息采集" in block and got and stats == {"page": 1}
    assert actual._supply_pending == {"p": 1}


@pytest.mark.parametrize("writer_fails", [False, True])
def test_native_report_save_advances_delivery_and_preserves_original_material(tmp_path, monkeypatch, writer_fails):
    from pathlib import Path
    from core import ai_dossier_sources, report_meta, state
    from core.engine import ResearchEngine
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "config"
    (config / "source_supply").mkdir(parents=True)
    (config / "system.yaml").write_text("{}")
    (config / "source_supply/pilot.yaml").write_text(yaml.safe_dump({"jobs": {JOB: {"mode": "changes"}}, "detail_pages": 0}))
    path = tmp_path / ("jobs/" + JOB + ".yaml")
    path.parent.mkdir(parents=True)
    path.write_text("name: Native supply\nsearch: false\nsources: [page:https://source.example/notes]\n"
                    "prompt: Research the supplied material\noutput: output/{date}_supply.md\n")
    monkeypatch.setattr(state, "_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(state, "_HISTORY_DIR", str(tmp_path / "state/history"))
    monkeypatch.setattr(report_meta, "META_FILE", str(tmp_path / "state/report_meta.jsonl"))
    monkeypatch.setattr(ai_dossier_sources, "enrich", lambda *a, **k: ("", {}))
    monkeypatch.setattr(supply, "read_page", lambda *a, **k: [item(text="Concrete input, processing steps, final output.")])
    class Agent:
        def __init__(self, **kw):
            self.materials, self.retrieved_urls = [], set()
        def run_fetch_only(self, prompt, **kw):
            assert "processing steps" in prompt
            if writer_fails:
                raise RuntimeError("Writer interrupted")
            return "# Complete research dossier\nConcrete example and result https://source.example/notes"
        def get_usage(self):
            return {"searches": 0, "total_tokens": 1}
        def close(self):
            pass
    monkeypatch.setattr("core.engine.ResearchAgent", Agent)
    engine = ResearchEngine(str(config), str(tmp_path / "jobs"), workspace_dir=str(tmp_path))
    result = engine.run_job(JOB)
    storage = SupplyStore(tmp_path / "state", "pilot")
    assert bool(storage.baseline(JOB)["items"]) is not writer_fails
    if not writer_fails:
        assert Path(result + ".source-supply.json").is_file()
        meta = json.loads(Path(report_meta.META_FILE).read_text().splitlines()[-1])
        material = json.loads(Path(meta["material"]["path"]).read_text())["material"]
        assert "Concrete input, processing steps, final output" in material


def test_new_dated_announcement_on_price_page_is_the_change_not_background(setup, monkeypatch):
    engine, job, profile, storage = setup
    spec = job["sources"][0]
    monkeypatch.setattr(supply.source_coverage, "targets", lambda *a, **k: [{"spec": spec, "role": "state", "cell": "", "max_age_hours": 24}])
    page = ["Business $25 per seat. Unlimited subject to abuse guardrails."]
    monkeypatch.setattr(supply, "read_page", lambda *a, **k: [item(text=page[0])])
    storage.commit(supply.collect(engine, job, profile)[2], "background", "first.md")
    page[0] += " Starting in December, plan usage will have a shared limit across all of ChatGPT and Codex."
    got, _, pending, _ = supply.collect(engine, job, profile)
    header = got[0].excerpts[0].split("\n", 2)[1]
    assert "页面新增带生效时间的公告：Starting in December, plan usage will have a shared limit" in header
    assert "页面变化不等于事件刚刚发生" not in header
    storage.commit(pending, "announced", "second.md")
    # The announcement stays on the page; the next edit around it is no longer the announcement.
    page[0] = page[0].replace("$25", "$30")
    header = supply.collect(engine, job, profile)[0][0].excerpts[0].split("\n", 2)[1]
    assert "生效时间的公告" not in header and "页面变化不等于事件刚刚发生" in header


def test_announcements_pick_dated_sentences_only():
    assert supply.announcements("Prices may vary. Data from May. Effective Jan 5, 2027 the price rises.") == [
        "Effective Jan 5, 2027 the price rises."]
    assert supply.announcements("说明。自 2026 年 12 月 1 日起，Plus 调整为 25 美元。其余不变。") == [
        "自 2026 年 12 月 1 日起，Plus 调整为 25 美元。"]


def test_every_news_original_is_read_exa_comes_last_and_its_quota_stops_the_run(setup, monkeypatch):
    from core.errors import SearchUnavailableError
    engine, job, profile, _ = setup
    del profile["detail_pages"]
    profile["material_chars"] = 3000  # far too small: news is not bounded by it
    job.update(sources=["rss:https://news.example/feed"], sources_limit=8)
    monkeypatch.setattr(sources, "fetch_source", lambda *a, **k: [
        replace(item(i, provider="rss", text="teaser"), url=f"https://news.example/a{i}", publish_date="2026-11-02")
        for i in range(8)])
    readers = []
    def read(url, *, search_client=None, browser=True, **k):
        readers.append((url[-2:], "exa" if search_client is not None else "direct"))
        if url.endswith("a0") and search_client is None:
            raise ValueError("403")  # direct and browser refused: only the paid reader is left
        return [SearchResult("Body", url, excerpts=["正文" * 3000])]
    monkeypatch.setattr(supply, "read_page", read)
    engine._source_options = lambda specs: {"search_client": SimpleNamespace(calls=0)}
    got, _, _, receipt = supply.collect(engine, job, profile)
    assert receipt["detail_budget"]["successful"] == 8 and ("a0", "exa") in readers
    assert sum(r.title == "Body" for r in got) == 8
    def out_of_quota(url, *, search_client=None, **k):
        if search_client is None:
            raise ValueError("403")
        raise RuntimeError("HTTP 402: insufficient credits")
    monkeypatch.setattr(supply, "read_page", out_of_quota)
    with pytest.raises(SearchUnavailableError):
        supply.collect(engine, job, profile)


def test_news_a_run_did_not_hand_over_is_owed_to_the_next_run(setup, monkeypatch):
    engine, job, profile, storage = setup
    day = {"days": 1, "timezone": "Asia/Shanghai"}
    job.update(sources=["tldr:ai"], _collection_window={**day, "start": "2026-11-01T00:10:00+08:00", "end": "2026-11-02T00:10:00+08:00"})
    news = [replace(item(1, "first", provider="tldr"), url="https://news.example/1", publish_date="2026-11-01")]
    monkeypatch.setattr(sources, "fetch_source", lambda spec, **k: list(news))
    pending = supply.collect(engine, job, profile)[2]
    pending["at"] = datetime(2026, 11, 2, 0, 10).timestamp()
    storage.commit(pending, "first", "first.md")
    # The next day's range starts at 11-02 00:10; an 11-02 item the last run never saw is still owed,
    # while what that run already handed over is not offered again.
    news.append(replace(item(2, "late", provider="tldr"), url="https://news.example/2", publish_date="2026-11-02"))
    job["_collection_window"] = {**day, "start": "2026-11-03T00:10:00+08:00", "end": "2026-11-04T00:10:00+08:00"}
    got = supply.collect(engine, job, profile)[0]
    assert [r.url for r in got] == ["https://news.example/2"] and "本期补交" in got[0].excerpts[0]


def test_dated_news_goes_before_old_page_sections_and_is_not_cut_per_source(setup, monkeypatch):
    engine, job, profile, storage = setup
    job.update(sources=["page:https://source.example/notes", "tldr:ai"], sources_limit=3,
               _collection_window={"days": 1, "start": "2026-11-02T00:10:00+08:00", "end": "2026-11-03T00:10:00+08:00",
                                   "timezone": "Asia/Shanghai"})
    pages = [item(i, "旧段落" * 400) for i in range(30)]
    news = [replace(item(100 + i, f"news {i}", provider="tldr"), url=f"https://news.example/{i}", publish_date="2026-11-02")
            for i in range(6)]
    monkeypatch.setattr(supply, "read_page", lambda *a, **k: pages)
    monkeypatch.setattr(sources, "fetch_source", lambda spec, **k: news)
    storage.commit(supply.collect(engine, job, profile)[2], "first", "first.md")
    pages[:] = [item(i, "改过的旧段落" * 400) for i in range(30)]  # a long page re-rendered: every section "changed"
    news[:] = [replace(n, excerpts=[n.excerpts[0] + " update"]) for n in news]
    profile["material_chars"] = 9000
    got = supply.collect(engine, job, profile)[0]
    delivered = [r.url for r in got]
    assert all(f"https://news.example/{i}" in delivered for i in range(6))  # all six, past the per-source cap of 3
    assert delivered.index("https://news.example/0") < min(
        (n for n, u in enumerate(delivered) if u.startswith("https://source.example")), default=len(delivered))


def test_a_newly_appeared_page_section_goes_before_ones_waiting_for_days(tmp_path, monkeypatch):
    from core.material_archive import MaterialArchive
    archive = MaterialArchive(tmp_path)
    old = [item(i, f"old {i}") for i in range(5)]
    archive.record("page:https://source.example/notes", old, now=1000)
    archive.record("page:https://source.example/notes", old + [item(9, "new section")], now=90000)
    seen = archive.first_seen("page:https://source.example/notes")
    assert seen[supply.section_key(item(9))] == 90000 and seen[supply.section_key(old[0])] == 1000


def test_collect_offers_todays_new_section_before_a_backlog_from_days_ago(setup, monkeypatch, tmp_path):
    from core.material_archive import MaterialArchive
    engine, job, profile, storage = setup
    job.update(_issue_id="issue-1", sources_limit=2)
    spec = job["sources"][0]
    storage.commit(supply.collect(engine, job, profile)[2] if False else {"profile": "pilot", "job": JOB, "at": 1, "delivered": {},
                   "sources": {}, "initial_baseline": {supply.section_key(item(0)): {"fingerprint": "x", "text": ""}},
                   "candidates": []}, "first", "first.md")
    backlog = [item(i, f"old {i}") for i in range(1, 6)]
    MaterialArchive(tmp_path / "state").record(spec, backlog, now=1000)  # waiting since days ago
    page = backlog + [item(9, "new today")]
    monkeypatch.setattr(supply, "read_page", lambda *a, **k: page)
    got = supply.collect(engine, job, profile)[0]
    assert "new today" in got[0].excerpts[0]

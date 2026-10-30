"""Real delivery boundaries: late page changes, failed reports, article depth and pool rotation."""
from dataclasses import replace
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


def test_full_length_section_is_not_dropped_by_its_markdown_header(setup, monkeypatch):
    engine, job, profile, _ = setup
    job["sources"] = ["rss:https://source.example/feed"]
    profile["detail_pages"] = 1
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

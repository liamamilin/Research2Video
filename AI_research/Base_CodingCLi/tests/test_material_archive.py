from dataclasses import replace
from datetime import datetime
from threading import Event
from types import SimpleNamespace

from core import material_collection, research_window, source_supply, sources
from core.material_archive import MaterialArchive
from core.search import SearchResult


def test_archive_keeps_versions_and_reverts_without_rewriting_history(tmp_path):
    archive = MaterialArchive(tmp_path)
    first = SearchResult(
        "A", "https://example.test/a", "2026-10-26", ["Original"], "rss"
    )
    changed = replace(first, excerpts=["Changed"])
    archive.record("rss:demo", [first], now=100)
    archive.record("rss:demo", [first], now=200)
    archive.record("rss:demo", [changed], now=300)
    archive.record("rss:demo", [first], now=400)
    assert archive.read("rss:demo", before=250)[0][0].excerpts == ["Original"]
    assert archive.read("rss:demo", before=350)[0][0].excerpts == ["Changed"]
    assert archive.read("rss:demo", before=450)[0][0].excerpts == ["Original"]
    with archive.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 3


def test_weekly_supply_recovers_items_that_disappeared_from_feed(tmp_path, monkeypatch):
    import yaml

    from tests.test_source_supply import JOB

    root = tmp_path / "config"
    (root / "matrices").mkdir(parents=True)
    (root / "matrices/demo.yaml").write_text(f"name: Demo\njobs: [{JOB}]\n")
    policy = {
        "schema_version": 1,
        "matrices": ["demo"],
        "window": {"mode": "previous_slot"},
    }
    (root / "production_policy.yaml").write_text(yaml.safe_dump(policy))
    spec = "rss:https://example.test/feed"
    archive = MaterialArchive(tmp_path / "state")
    older = SearchResult(
        "Earlier this week",
        "https://example.test/early",
        "2026-10-25",
        ["Process"],
        "rss",
    )
    newer = SearchResult(
        "Latest", "https://example.test/new", "2026-10-30", ["Result"], "rss"
    )
    archive.record(
        spec,
        [older],
        now=datetime.fromisoformat("2026-10-25T08:00:00+08:00").timestamp(),
    )
    monkeypatch.setattr(sources, "fetch_source", lambda *a, **kw: [newer])
    engine = SimpleNamespace(
        config_dir=str(root),
        workspace_dir=str(tmp_path),
        _cancel=Event(),
        _source_options=lambda s: {"search_client": None},
    )
    job = research_window.freeze(
        {"_file": JOB, "sources": [spec], "sources_limit": 5, "collection_days": 7},
        datetime(2026, 10, 31, 2, 40),
    )
    profile = {"key": "demo", "jobs": {JOB: {"mode": "context"}}, "detail_pages": 0}
    got, _, _, receipt = source_supply.collect(engine, job, profile)
    assert {r.title for r in got} == {"Earlier this week", "Latest"}
    assert receipt["sources"][spec]["archived_items"] == 1
    assert "无法保证回溯" in receipt["sources"][spec]["archive_coverage"]["scope"]
    import sqlite3

    def cache_unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("cache busy")

    monkeypatch.setattr(MaterialArchive, "record", cache_unavailable)
    fresh, _, _, degraded = source_supply.collect(engine, job, profile)
    assert [r.title for r in fresh] == ["Latest"]
    assert degraded["sources"][spec]["archive_error"] == "cache busy"


def test_collector_uses_public_adapters_without_search_or_models(tmp_path, monkeypatch):
    config = tmp_path / "config"
    (config / "matrices").mkdir(parents=True)
    (config / "matrices/demo.yaml").write_text("name: Demo\njobs: [demo]\n")
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    (jobs / "demo.yaml").write_text(
        "sources: [rss:https://example.test/feed, search:private]\n"
    )
    calls = []
    monkeypatch.setattr(
        sources,
        "fetch_source",
        lambda spec, **kw: (
            calls.append(spec) or [SearchResult("A", "https://example.test/a")]
        ),
    )
    engine = SimpleNamespace(
        config_dir=str(config),
        jobs_dir=str(jobs),
        workspace_dir=str(tmp_path),
        _cancel=Event(),
    )
    result = material_collection.collect(
        engine, {"revision": "v1", "matrices": ["demo"], "collection": {}}
    )
    assert calls == ["rss:https://example.test/feed"]
    assert result["usage"] == {"total_tokens": 0, "llm_calls": 0, "searches": 0}
    assert result["sources"]["search:private"]["status"] == "skipped"


def test_pruning_touches_cache_only_and_keeps_recently_seen_old_reference(tmp_path):
    archive = MaterialArchive(tmp_path)
    item = SearchResult("Docs", "https://example.test/docs")
    archive.record("page:docs", [item], now=1)
    archive.record("page:docs", [item], now=10 * 86400)
    sentinel = tmp_path / "video.mp4"
    sentinel.write_bytes(b"original")
    archive.prune(2, now=11 * 86400)
    assert archive.read("page:docs", before=11 * 86400)[0]
    assert sentinel.read_bytes() == b"original"

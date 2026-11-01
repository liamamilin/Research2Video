import json
from datetime import datetime
from pathlib import Path

import pytest

from core import research_window as window
from core import source_supply, sources
from core.search import SearchResult
from tests.test_source_supply import setup  # noqa: F401


def period(days=7):
    return window.freeze({"collection_days": days}, datetime(2026, 10, 31, 2, 40))[
        "_collection_window"
    ]


def row(day, name="item", provider="rss"):
    return SearchResult(
        name, f"https://example.test/{name}", day, ["concrete example"], provider
    )


def test_window_is_frozen_and_does_not_modify_the_job():
    job = {"collection_days": 7}
    frozen = window.freeze(job, datetime(2026, 10, 31, 2, 40))
    assert frozen["_collection_window"]["start"] == "2026-10-24T02:40:00+08:00"
    assert window.freeze(frozen, datetime(2026, 11, 3)) is frozen
    assert "_collection_window" not in job
    assert window.freeze({}, datetime.now()) == {}


@pytest.mark.parametrize("bad", [0, -1, 32, "7", True])
def test_invalid_window_is_not_silently_used(bad):
    with pytest.raises(ValueError):
        window.freeze({"collection_days": bad}, datetime.now())


def test_date_boundaries_and_timezone_are_applied():
    data = [
        row("2026-10-23", "old"),
        row("2026-10-24", "start"),
        row("Thu, 29 Oct 2026 12:00:00 GMT", "rfc"),
        row("2026-10-31T02:40:00+08:00", "end"),
        row("2026-10-31T02:41:00+08:00", "future"),
    ]
    assert {r.title for r in window.select(data, period())} == {"start", "rfc", "end"}


def test_rfc_numeric_offsets_retain_their_real_timezone():
    data = [
        row("Fri, 23 Oct 2026 18:40:01 +0000", "inside"),
        row("Fri, 23 Oct 2026 18:39:59 +0000", "outside"),
    ]
    assert [r.title for r in window.select(data, period())] == ["inside"]


def test_reference_pages_and_model_creation_dates_are_not_new_news():
    data = [
        row(None, "unknown"),
        row("2024-01-01", "docs", "page"),
        row("2020-01-01", "model", "hf_models"),
    ]
    selected = window.select(data, period(), reference=True)
    assert len(selected) == 3 and all("背景" in x.excerpts[0] for x in selected)
    assert all(x.excerpts == ["concrete example"] for x in data)


def test_dated_announcements_are_foreground_and_old_news_is_excluded():
    got = window.select(
        [row("2024-01-01", "old", "page"), row("2026-10-30", "new", "page")], period()
    )
    assert [r.title for r in got] == ["new"] and got[0].excerpts == ["concrete example"]


def test_partial_historical_api_failure_is_saved_as_coverage(monkeypatch):
    def get(url, *a):
        if "2026-10-29" in url:
            raise TimeoutError("date unavailable")
        return "[]"

    monkeypatch.setattr(sources, "_get_page", get)
    got = sources.fetch_hf_papers("daily", collection_window=period())
    assert got.coverage["2026-10-29"]["status"] == "unavailable"
    assert got.coverage["2026-10-28"]["status"] == "read"


def test_one_busy_day_cannot_displace_every_other_day_before_source_limit():
    data = [row("2026-10-30", str(i)) for i in range(20)] + [
        row("2026-10-25", "older_in_week")
    ]
    assert window.select(data, period())[1].title == "older_in_week"


def test_weekly_tldr_reads_the_whole_period_instead_of_only_three_issues(monkeypatch):
    page = (Path(__file__).parent / "fixtures/sources/tldr_ai.html").read_text()
    calls = []
    monkeypatch.setattr(
        sources, "_get_page", lambda url, *a, **k: calls.append(url) or page
    )
    got = sources.fetch_source("tldr:ai", limit=1, collection_window=period())
    assert len(calls) == 8 and {r.publish_date for r in got} == set(
        window.dates(period())
    )


def test_hf_daily_papers_queries_each_day_and_retains_earlier_papers(monkeypatch):
    calls = []

    def get(url, *a, **k):
        from urllib.parse import parse_qs, urlsplit

        calls.append(url)
        day = parse_qs(urlsplit(url).query)["date"][0]
        return json.dumps([{"paper": {"id": day, "title": day}, "publishedAt": day}])

    monkeypatch.setattr(sources, "_get_page", get)
    got = sources.fetch_source("hf_papers:daily", limit=2, collection_window=period())
    assert len(calls) == 8 and len(got) == 8


def test_supply_filters_before_slicing_and_records_the_window(setup, monkeypatch):
    engine, job, profile, _ = setup
    job = window.freeze({**job, "collection_days": 7}, datetime(2026, 10, 31, 2, 40))
    profile["jobs"][job["_file"]]["mode"] = "context"
    job["sources"] = ["rss:https://example.test/feed"]
    job["sources_limit"] = 1
    monkeypatch.setattr(
        sources,
        "fetch_source",
        lambda *a, **k: [row("2026-09-01", "old"), row("2026-10-29", "this_week")],
    )
    got, _, _, receipt = source_supply.collect(engine, job, profile)
    assert [x.title for x in got] == ["this_week"]
    assert receipt["collection_window"] == period()
    assert "近 7 天" in source_supply.material_block(got, receipt)


def test_engine_prompt_and_plain_source_route_share_the_frozen_window(monkeypatch):
    from core.engine import ResearchEngine

    engine = ResearchEngine.__new__(ResearchEngine)
    engine.config_dir = "config"
    engine._now = lambda: datetime(2026, 10, 31, 2, 40)
    engine._fetch_supply = lambda job: None
    engine._source_options = lambda specs: {}
    engine._outcomes_block = lambda: ""
    engine._reported_events = lambda: ""
    job = window.freeze(
        {
            "collection_days": 7,
            "name": "weekly",
            "prompt": "Keep examples",
            "sources": ["rss:https://example.test/feed"],
        },
        engine._now(),
    )
    observed = {}

    def fetch(*a, **k):
        observed.update(k)
        return [row("2026-10-29", "this_week"), row("2026-08-01", "old")], {}

    monkeypatch.setattr(sources, "fetch_all_strict", fetch)
    block, _ = engine._fetch_sources_strict(job)
    assert "this_week" in block and "/old" not in block
    assert observed[
        "collection_window"
    ] == period() and "近 7 天" in engine._build_prompt(job)


def test_native_research_saves_period_in_original_material_and_usage(
    tmp_path, monkeypatch
):
    from core import ai_dossier_sources, report_meta, state
    from core.engine import ResearchEngine

    monkeypatch.chdir(tmp_path)
    config = tmp_path / "config"
    config.mkdir()
    (config / "system.yaml").write_text("{}")
    (config / "source_supply").mkdir()
    (config / "source_supply/pilot.yaml").write_text(
        "jobs:\n  weekly:\n    mode: context\ndetail_pages: 0\n"
    )
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    (jobs / "weekly.yaml").write_text(
        "name: Weekly\ncollection_days: 7\nsearch: false\nsources: [rss:https://example.test/feed]\nprompt: Keep specific examples\noutput: output/{date}_weekly.md\n"
    )
    monkeypatch.setattr(state, "_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(state, "_HISTORY_DIR", str(tmp_path / "state/history"))
    monkeypatch.setattr(
        report_meta, "META_FILE", str(tmp_path / "state/report_meta.jsonl")
    )
    monkeypatch.setattr(ai_dossier_sources, "enrich", lambda *a, **k: ("", {}))
    monkeypatch.setattr(
        sources,
        "fetch_source",
        lambda *a, **k: [
            row("2026-10-25", "week_start"),
            row("2026-10-30", "week_end"),
            row("2026-09-01", "stale"),
        ],
    )

    class Agent:
        def __init__(self, **kw):
            self.materials, self.retrieved_urls = [], set()

        def run_fetch_only(self, prompt, **kw):
            assert (
                "近 7 天" in prompt
                and "week_start" in prompt
                and "week_end" in prompt
                and "/stale" not in prompt
            )
            return "# 本周研究底稿\n时间范围：2026-10-24 至 2026-10-31。\n具体输入、处理过程与输出结果 https://example.test/week_end"

        def get_usage(self):
            return {"searches": 0, "total_tokens": 1}

        def close(self):
            pass

    monkeypatch.setattr("core.engine.ResearchAgent", Agent)
    engine = ResearchEngine(str(config), str(jobs), workspace_dir=str(tmp_path))
    engine._now = lambda: datetime(2026, 10, 31, 2, 40)
    result = engine.run_job("weekly")
    assert result
    receipt = json.loads(Path(result + ".source-supply.json").read_text())
    assert receipt["collection_window"] == period()
    meta = json.loads(Path(report_meta.META_FILE).read_text().splitlines()[-1])
    material = json.loads(Path(meta["material"]["path"]).read_text())["material"]
    assert (
        "近 7 天" in material and "week_start" in material and "/stale" not in material
    )
    history = json.loads(
        (tmp_path / "state/history/weekly.jsonl").read_text().splitlines()[-1]
    )
    assert history["usage"]["collection_window"] == period()

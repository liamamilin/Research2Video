"""Read-path costs stay bounded while edits, versions and historical attribution stay accurate."""

from __future__ import annotations

import io
from collections import Counter
from contextlib import contextmanager
from datetime import datetime

from core import config, fileio, matrices
from core.state import StateManager
from core.video import autopilot_store as ap, outputs, store, topic_store
from tests.test_production import prod_env  # noqa: F401
from tests.test_video_board import make_script, render
from tests.test_video_scripts import video_env  # noqa: F401
from web.routes import config as config_routes, video_recipes


def test_system_reads_reuse_parsing_without_sharing_mutations_or_hiding_edits(tmp_path):
    path = tmp_path / "system.yaml"
    path.write_text("ai:\n  model: first\n", encoding="utf-8")
    first = config.load_system_config(str(tmp_path))
    first["ai"]["model"] = "mutation"
    assert config.load_system_config(str(tmp_path))["ai"]["model"] == "first"
    path.write_text("ai:\n  model: newer\n", encoding="utf-8")
    assert config.load_system_config(str(tmp_path))["ai"]["model"] == "newer"


def test_admin_system_snapshot_cache_preserves_parser_and_independent_results(video_env, monkeypatch):
    path = video_env / "config" / "system.yaml"
    path.write_text("ai:\n  model: first\n", encoding="utf-8")
    config_routes._parsed_system_text.cache_clear()
    original = config_routes.parse_yaml
    reads = []

    def parse(text):
        reads.append(text)
        return original(text)

    monkeypatch.setattr(config_routes, "parse_yaml", parse)
    first = config_routes.get_system_config(user={})
    first["parsed"]["ai"]["model"] = "mutation"
    second = config_routes.get_system_config(user={})
    assert second["parsed"]["ai"]["model"] != "mutation" and len(reads) == 1
    path.write_text(path.read_text(encoding="utf-8") + "\n# new snapshot\n", encoding="utf-8")
    third = config_routes.get_system_config(user={})
    assert third["content"].endswith("# new snapshot\n") and len(reads) == 2


def test_month_history_discovers_jobs_once_and_never_attributes_ambiguous_short_names(monkeypatch):
    names = [f"research/job_{i}" for i in range(20)]
    scans = []
    monkeypatch.setattr(config, "list_jobs", lambda folder: scans.append(folder) or names)
    monkeypatch.setattr(StateManager, "get_history", lambda job, limit=5000: [
        {"ts": "2026-10-23", "status": "success", "usage": {"searches": 3}}] if "/" in job else [
        {"ts": "2026-10-22", "status": "success", "usage": {"searches": 2}}])
    matrix = matrices.Matrix("test", "Test", members=tuple(matrices.Member(job) for job in names))
    assert matrices.month_searches(matrix, datetime(2026, 10, 23)) == 100
    assert len(scans) == 1
    assert len(matrices.history_of("research/job_0", known_jobs=names + ["other/job_0"])) == 1
    assert len(scans) == 1


def test_matrix_schedule_snapshot_uses_one_store_and_batch_history(video_env, monkeypatch):
    from web.routes import matrices as matrix_routes
    from web.services import managed_scheduler as managed

    storage = managed.store()
    for index in range(6):
        storage.save(f"research/task_{index}", "0 2 * * *", timezone="UTC")
    opened = []
    monkeypatch.setattr(managed, "store", lambda: opened.append(1) or storage)
    monkeypatch.setattr(storage, "runs", lambda *args: (_ for _ in ()).throw(AssertionError("per-plan history read")))

    plans = matrix_routes._plans()

    assert len(plans) == 6 and len(opened) == 1
    assert all(plan["enabled"] and plan["next_runs"] and plan["period"]["kind"] == "daily"
               for plan in plans.values())


def test_batch_run_list_uses_one_connection_and_keeps_items_and_status_filter(video_env, monkeypatch):
    ids = []
    for i in range(8):
        run = ap.create_run({"n": i}, [{"column_key": "flash", "topic": {"title": "第一条"}},
                                         {"column_key": "flash", "topic": {"title": "第二条"}}])
        ap.update_run(run["id"], status="DONE" if i % 2 else "CANCELLED", started_at=i)
        ids.append(run["id"])
    expected = [ap.get_run(ids[i]) for i in (7, 5, 3)]
    real_connect, connections = ap.connect, []

    @contextmanager
    def counted(*args, **kwargs):
        connections.append(1)
        with real_connect(*args, **kwargs) as conn:
            yield conn

    monkeypatch.setattr(ap, "connect", counted)
    monkeypatch.setattr(ap, "get_run", lambda *a: (_ for _ in ()).throw(AssertionError("per-row read")))
    assert ap.list_runs(limit=3, status="DONE") == expected
    assert len(connections) == 1
    assert ap.list_runs(limit=0) == []


def test_shipments_batch_details_instead_of_reading_each_video_again(prod_env, monkeypatch):
    for _ in range(12):
        render(make_script(status="APPROVED"))

    def per_row(*args):
        raise AssertionError("the publication list must batch its detail reads")

    monkeypatch.setattr(store, "get_render", per_row)
    monkeypatch.setattr(store, "list_rechecks", per_row)
    monkeypatch.setattr(store, "list_publications", per_row)
    from web.routes.video import shipments

    rows = shipments(user={})
    assert len(rows) == 12 and all(r["render"]["status"] == "COMPLETED" for r in rows)


def test_report_counts_match_output_visibility_without_loading_versions(prod_env, monkeypatch):
    make_script("research/loose")
    archived = make_script("research/loose")
    store.update_script(archived["id"], archived=1)
    make_script("research/loose", status="FAILED")
    make_script("medical/flash_discovery")
    args = str(prod_env / "config"), str(prod_env / "jobs")
    expected = Counter((s["job"], s["report_path"]) for s in outputs.collect(*args)["scripts"] if s["status"] != "FAILED")
    monkeypatch.setattr(store, "current_versions", lambda *a: (_ for _ in ()).throw(AssertionError("unneeded versions")))
    assert outputs.count_by_report(*args) == expected


def test_recipe_warnings_read_the_source_directory_once(video_env, monkeypatch):
    original, calls = topic_store.sources, []
    monkeypatch.setattr(topic_store, "sources", lambda folder: calls.append(folder) or original(folder))
    assert video_recipes.list_recipes(user={})
    assert len(calls) == 1


def test_automation_labels_read_current_versions_in_a_batch(prod_env, monkeypatch):
    from core.video import credibility
    from web.routes.video import autopilot_overview

    script = make_script()
    expected = credibility.label(store.get_version(script["id"], script["current_version"]))
    run = ap.create_run({}, [{"column_key": script["column_key"], "topic": {"title": "选题"}}])
    ap.update_item(run["items"][0]["id"], script_id=script["id"])

    def per_row(*args):
        raise AssertionError("automation overviews must batch script reads")

    monkeypatch.setattr(store, "get_script", per_row)
    monkeypatch.setattr(store, "get_version", per_row)
    result = autopilot_overview(user={})
    assert result["runs"][0]["items"][0]["credibility"] == expected


def test_log_tail_seeks_past_old_data_and_preserves_utf8_and_incomplete_last_line(monkeypatch):
    raw = ("old line\n" * 200000 + "第一条\r\n" + "末条" * 30).encode()
    reads = []

    class Measured(io.BytesIO):
        def read(self, size=-1):
            result = super().read(size)
            reads.append(len(result))
            return result

    monkeypatch.setattr("builtins.open", lambda *args, **kwargs: Measured(raw))
    assert fileio.tail_lines("example.log", 2) == ["第一条\n", "末条" * 30]
    assert sum(reads) < 100000
    assert fileio.tail_lines("example.log", 0) == []

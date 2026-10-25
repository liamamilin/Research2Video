"""Regressions for console load costs, edited configuration and concurrent health probes."""

from __future__ import annotations

import io
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
import yaml

from core import health, yaml_cache
from core.config import load_job
from core.video import production, recipes, store, topic_store
from tests.test_production import prod_env  # noqa: F401
from tests.test_video_scripts import video_env  # noqa: F401
from web.routes import outputs as output_routes, video


def test_cached_yaml_never_shares_mutations_and_reads_fresh_file_text(tmp_path):
    path = tmp_path / "job.yaml"
    path.write_text("name: one\nkeywords: [first]\n", encoding="utf-8")
    first = load_job(str(tmp_path), "job")
    first["keywords"].append("mutation")
    assert load_job(str(tmp_path), "job")["keywords"] == ["first"]
    path.write_text("name: two\nkeywords: [other]\n", encoding="utf-8")
    assert load_job(str(tmp_path), "job")["name"] == "two"
    with path.open(encoding="utf-8") as fh:
        assert yaml_cache.load_yaml(fh)["keywords"] == ["other"]
    assert yaml_cache.load_yaml(io.StringIO(path.read_text()))["name"] == "two"


def test_bad_yaml_is_not_cached_and_cannot_poison_the_next_edit():
    with pytest.raises(yaml.YAMLError):
        yaml_cache.load_yaml("bad: [")
    assert yaml_cache.load_yaml("bad: [fixed]") == {"bad": ["fixed"]}


def test_broken_multi_audience_recipe_is_a_config_error_not_a_list_crash(prod_env):
    path = prod_env / "config" / "video_recipes" / "flash.yaml"
    data = yaml.safe_load(path.read_text())
    data["tags"].pop("domain")
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    config = prod_env / "config" / "production" / "broken_audience.yaml"
    config.write_text("job: monitoring/ai_flash_discovery\nrecipe: flash\naudience: non_tech\n", encoding="utf-8")
    every = production.load_all(str(prod_env / "config"), str(prod_env / "jobs"))
    assert not every["broken_audience"].usable
    assert any("domain" in e for e in every["broken_audience"].errors)
    assert output_routes.list_outputs(user={})["scripts"] == []


def test_a_recipe_that_disappears_during_read_is_reported_on_its_own(tmp_path, monkeypatch):
    folder = tmp_path / "video_recipes"
    folder.mkdir()
    (folder / "gone.yaml").write_text("name: gone")
    original = open

    def fail_recipe(path, *args, **kwargs):
        if str(path).endswith("gone.yaml"):
            raise FileNotFoundError(str(path))
        return original(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fail_recipe)
    entry = recipes.load(str(tmp_path))["gone"]
    assert entry.error and not entry.usable


def test_a_recipe_disappearing_during_audience_expansion_does_not_break_the_catalog(video_env, monkeypatch):
    path = video_env / "config" / "video_recipes" / "flash.yaml"
    original = open
    reads = 0

    def disappear_during_expansion(candidate, *args, **kwargs):
        nonlocal reads
        if str(candidate) == str(path):
            reads += 1
            if reads > 1:
                raise FileNotFoundError(str(candidate))
        return original(candidate, *args, **kwargs)

    monkeypatch.setattr("builtins.open", disappear_during_expansion)
    every = recipes.expanded(str(video_env / "config"))
    assert every["flash"].error and not every["flash"].usable
    assert every["deep_dive"].usable


def test_columns_parse_the_recipe_catalog_once(video_env, monkeypatch):
    original = recipes.expanded
    calls = []

    def counted(config_dir):
        calls.append(config_dir)
        return original(config_dir)

    monkeypatch.setattr(recipes, "expanded", counted)
    shown = video.list_columns(user={})
    assert shown and len(calls) == 1


def test_variant_columns_reuse_one_request_catalog_and_reflect_later_edits(video_env, monkeypatch):
    original = recipes.expanded
    scans = []
    monkeypatch.setattr(recipes, "expanded", lambda path: scans.append(path) or original(path))
    columns = video._known_columns()
    first = columns.get("flash-nr")
    assert first is not None
    assert columns.get("flash-nr") is first
    assert columns.get("missing-nr") is None
    assert columns.get("missing-nr") is None
    assert len(scans) == 1
    path = video_env / "config" / "video_recipes" / "flash.yaml"
    data = yaml.safe_load(path.read_text())
    data["name"] = "新名称"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert video._known_columns().get("flash-nr").name == "新名称"
    assert len(scans) == 2


def test_configs_discover_sources_once_per_load_and_reflect_new_edits(prod_env, monkeypatch):
    original = topic_store.sources
    calls = []

    def counted(jobs_dir):
        calls.append(jobs_dir)
        return original(jobs_dir)

    monkeypatch.setattr(topic_store, "sources", counted)
    args = str(prod_env / "config"), str(prod_env / "jobs")
    production.load_all(*args)
    assert len(calls) == 1
    path = prod_env / "config" / "production" / "flash.yaml"
    data = yaml.safe_load(path.read_text())
    data["name"] = "立即生效的新名称"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert production.load_all(*args)["flash"].name == "立即生效的新名称"
    assert len(calls) == 2


def test_a_malformed_referenced_job_does_not_break_all_configs(prod_env):
    (prod_env / "jobs" / "research" / "loose.yaml").write_text("bad: [", encoding="utf-8")
    (prod_env / "config" / "production" / "bad_job.yaml").write_text(
        "job: research/loose\nrecipe: deep_dive\n", encoding="utf-8")
    every = production.load_all(str(prod_env / "config"), str(prod_env / "jobs"))
    assert not every["bad_job"].usable
    assert any("配置加载失败" in error for error in every["bad_job"].errors)
    assert every["flash"].usable


def test_script_lists_and_board_do_not_read_each_version_separately(prod_env, monkeypatch):
    for n in range(12):
        script = store.create_script("flash", {"title": f"topic {n}"})
        with store.connect(write=True) as conn:
            conn.execute("INSERT INTO script_versions (script_id, version, title, sections, length_check, edit_type,"
                         " created_by, created_at) VALUES (?, 1, ?, '[]', '{}', 'draft', '', '2026-10-23')",
                         (script["id"], f"version {n}"))
        store.update_script(script["id"], current_version=1, status="DRAFT")

    def per_row_read(*args, **kwargs):
        raise AssertionError("list pages must use batch reads")

    monkeypatch.setattr(store, "get_version", per_row_read)
    monkeypatch.setattr(store, "list_renders", per_row_read)
    assert len(video.list_scripts(user={})) == 12
    assert len(output_routes.list_outputs(user={})["scripts"]) == 12
    assert next(s for s in video.home_board(user={})["stages"] if s["key"] == "draft")["count"] == 12


def test_batched_versions_follow_current_version_and_include_archived(video_env):
    script = store.create_script("flash", {"title": "x"})
    with store.connect(write=True) as conn:
        for n in (1, 2):
            conn.execute("INSERT INTO script_versions (script_id, version, title, sections, length_check, edit_type,"
                         " created_by, created_at) VALUES (?, ?, ?, '[]', '{}', 'draft', '', '2026-10-23')",
                         (script["id"], n, f"v{n}"))
    store.update_script(script["id"], current_version=2, archived=1)
    assert store.current_titles()[script["id"]] == "v2"
    assert store.current_versions([script["id"], "missing"])[script["id"]]["title"] == "v2"
    assert store.current_versions([]) == {}


def test_concurrent_health_checks_cannot_remove_each_others_probes(tmp_path, monkeypatch):
    barrier = threading.Barrier(8)
    original = health.tempfile.NamedTemporaryFile
    names = []

    def overlapping(*args, **kwargs):
        handle = original(*args, **kwargs)
        names.append(handle.name)
        barrier.wait(timeout=10)
        return handle

    monkeypatch.setattr(health.tempfile, "NamedTemporaryFile", overlapping)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: health._paths_check(str(tmp_path), str(tmp_path)), range(8)))
    assert len(names) == len(set(names)) == 16
    assert all(check["status"] == health.OK for result in results for check in result)
    assert list(tmp_path.iterdir()) == []


def test_api_errors_are_actionable_and_timings_are_available(client, web_env, monkeypatch):
    def failed(**kwargs):
        raise KeyError("private implementation detail")

    assert "app;dur=" in client.get("/api/health").headers["server-timing"]
    monkeypatch.setattr(health, "run_checks", failed)
    assert client.get("/api/live").status_code == 200
    from fastapi.testclient import TestClient

    tolerant = TestClient(client.app, raise_server_exceptions=False)
    response = tolerant.get("/api/health")
    tolerant.close()
    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "internal_error" and error["details"]["error_id"]
    assert "private implementation" not in response.text

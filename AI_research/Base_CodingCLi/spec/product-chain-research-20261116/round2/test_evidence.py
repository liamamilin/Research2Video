"""Audit reproductions, not product changes. All state writes use pytest tmp dirs.

Run this file separately from tests/: its plugin imports the repository fixtures.
Network and model calls are prohibited by an autouse fixture.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import socket
from threading import Event
from types import SimpleNamespace

import pytest
import yaml

pytest_plugins = ["tests.conftest"]

from tests.test_video_scripts import ROOT, _csrf, _login, video_env  # noqa: F401
from tests.test_production import prod_env  # noqa: F401
from tests.test_content_columns import column_env  # noqa: F401
from tests.test_matrices import matrix_env, MEDICAL  # noqa: F401


@pytest.fixture(autouse=True)
def no_external_calls(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Audit must not contact a network/provider")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    from core.llm import LLMClient
    monkeypatch.setattr(LLMClient, "chat", blocked)


def emit(case, **values):
    print("AUDIT " + json.dumps({"case": case, **values}, ensure_ascii=False))


def test_real_collect_keeps_coverage_source_after_job_reference_removed(tmp_path, monkeypatch):
    from core import source_supply, sources
    from core.search import SearchResult

    config = tmp_path / "config"
    config.mkdir()
    shutil.copyfile(ROOT / "config" / sources.REGISTRY_FILE, config / sources.REGISTRY_FILE)
    job = yaml.safe_load((ROOT / "jobs/monitoring/ai_flash_discovery.yaml").read_text())
    job["_file"] = "monitoring/ai_flash_discovery"
    profile = copy.deepcopy(source_supply.for_job(str(ROOT / "config"), job))
    job["sources"] = [s for s in job["sources"] if s != "source:cg_openai_news"]
    target = "rss:https://openai.com/news/rss.xml"
    assert target not in sources.resolve(job["sources"], str(config))
    calls = []

    def fake_fetch(spec, **kwargs):
        calls.append(spec)
        return [SearchResult("Example material", "https://example.org/article", excerpts=["Example details."], provider="rss")]

    def fake_read(url, **kwargs):
        return [SearchResult("Example page", url, excerpts=["Example details."], provider="page")]

    monkeypatch.setattr(sources, "fetch_source", fake_fetch)
    monkeypatch.setattr(source_supply, "read_page", fake_read)
    engine = SimpleNamespace(config_dir=str(config), workspace_dir=str(tmp_path), _cancel=Event(),
                             _source_options=lambda specs: {"search_client": SimpleNamespace(calls=0)})
    _, _, _, receipt = source_supply.collect(engine, job, profile)
    assert target in calls and target in receipt["sources"]
    emit("collector_after_removal", target_called=True, target_receipt=receipt["sources"][target],
         scope="Real collector; mocked remote content; isolated supply state")


def test_new_render_request_reads_live_overrides_but_saved_request_stays_fixed(column_env, monkeypatch):
    from core import matrices
    from core.video import content_columns, store, screenshots
    from web.services import video_render

    config = column_env / "config"
    source = matrices.load_all(str(config))["ai"]
    matrices.save(str(config), source.key, name=source.name, domain=source.domain, keywords=source.keywords,
                  members=source.members, render={"video_speed": 1.0})
    binding = config / "production/flash.yaml"
    raw = yaml.safe_load(binding.read_text())
    raw["render"] = {"production_mode": "standard"}
    binding.write_text(yaml.safe_dump(raw, allow_unicode=True))
    context = content_columns.snapshot(str(config), "flash", "monitoring/ai_flash_discovery")
    context["render"]["voice_id"] = "voxcpm_anchor"
    script = store.create_script("flash", {"title": "同一份讲稿"}, production="flash", content_context=context)
    version = store.add_version(script["id"], title="同一份讲稿", sections=[{
        "title": "例子", "paragraphs": [{"text": "运行转换命令，查看生成的文件。", "citations": [], "evidence": "UNCITED"}]}],
        length_check={}, edit_type="GENERATED")
    store.update_script(script["id"], status="APPROVED", approved_version=version)
    monkeypatch.setattr(video_render, "get_client", lambda: SimpleNamespace(base_url="http://unused.invalid"))
    monkeypatch.setattr(screenshots, "render_materials", lambda *a, **k: [])
    first = video_render.create(store.get_script(script["id"]))
    matrices.save(str(config), source.key, name=source.name, domain=source.domain, keywords=source.keywords,
                  members=source.members, render={"video_speed": 1.5})
    raw["render"]["production_mode"] = "fast"
    binding.write_text(yaml.safe_dump(raw, allow_unicode=True))
    # A new request is prohibited while the previous one is active. This is an
    # important counterexample to a broad "settings change running renders" claim.
    with pytest.raises(video_render.RenderError, match="已有进行中"):
        video_render.create(store.get_script(script["id"]))
    store.update_render(first["id"], status="FAILED", error_code="AUDIT_FIXTURE")
    second = video_render.create(store.get_script(script["id"]))
    before = first["request"]["production_context"]["matrix"]["settings"]
    after = second["request"]["production_context"]["matrix"]["settings"]
    assert before["video_speed"] == 1.0 and after["video_speed"] == 1.5
    assert before["production_mode"] == "standard" and after["production_mode"] == "fast"
    assert before["voice_id"] == after["voice_id"] == "voxcpm_anchor"
    assert store.get_render(first["id"])["request"] == first["request"]
    assert store.get_script(script["id"])["content_context"] == context
    emit("new_request_live_settings", before=before, after=after, old_request_unchanged=True,
         concurrent_request_rejected=True,
         scope="Real render.create and temporary SQLite; renderer not submitted")


def test_actual_matrix_copy_creates_jobs_but_not_production_bindings(client, matrix_env):
    from core.config import load_job
    from core.video import audience_strategy, question_material

    _login(client)
    cfg = matrix_env / "config"
    directory = cfg / "production"
    directory.mkdir(exist_ok=True)
    (directory / "original.yaml").write_text("name: original\njob: monitoring/ai_flash_discovery\nrecipe: flash\nauto: 0\n")
    before = sorted(p.name for p in directory.glob("*.yaml"))
    response = client.post("/api/matrices/ai/copy", json=MEDICAL, headers=_csrf(client))
    assert response.status_code == 201, response.text
    copied = response.json()
    member = copied["members"][0]
    job = load_job(str(matrix_env / "jobs"), member["job"])
    after = sorted(p.name for p in directory.glob("*.yaml"))
    assert before == after and member.get("schedule") is None
    emit("matrix_copy_api", new_job=member["job"], production_files_unchanged=before == after,
         has_schedule=member.get("schedule") is not None,
         selection_channel=audience_strategy.job_channel(job),
         question_enrichment=question_material.enabled(job, {"question_material": {"enabled": True}}),
         scope="Real copy HTTP route with authenticated temporary test user; generic template semantics")


def test_actual_topic_shapes_are_missing_from_both_picker_association_readers(video_env):
    from core.video import store
    from web.services import topic_progress

    rows = json.loads(Path(__file__).with_name("topic-shapes.json").read_text())
    for row in rows:
        made = store.create_script(row["column_key"], row["topic"], production=row["production"])
        store.update_script(made["id"], status="APPROVED")
    base = rows[1]["topic"]["from_item"]
    direct = rows[1]["topic"]["id"]
    assert base in [item["id"] for item in rows[0]["topic"]["episode"]["items"]]
    old_reader = store.scripts_by_topic()
    new_reader = topic_progress.scripts_by_topic([base, direct])
    assert base not in old_reader and base not in new_reader
    assert len(old_reader[direct]) == len(new_reader[direct]) == 1
    emit("topic_picker_association", report_topic_id=base, single_topic_id=direct,
         actual_episode_contains_base=True, actual_single_from_item=base,
         old_reader_base_links=len(old_reader.get(base, [])),
         new_reader_base_links=len(new_reader.get(base, [])), direct_links=len(new_reader[direct]),
         scope="Actual two topic JSON shapes; isolated SQLite; both real association services")

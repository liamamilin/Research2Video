"""Business columns reuse existing production, preserve its auto boundary and old script keys."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from core import matrices
from core.video import auto, content_columns, production, store
from tests.test_production import prod_env  # noqa: F401
from tests.test_video_scripts import REPORT, ROOT, _csrf, _login, llm, script_json, video_env  # noqa: F401


@pytest.fixture
def column_env(prod_env):
    config = prod_env / "config"
    shutil.copyfile(ROOT / "config/column_types.yaml", config / "column_types.yaml")
    content_columns.save(str(config), "model_review", {
        "name": "模型横评", "matrix": "medical", "type": "comparison", "promise": "讲清对象差异与适用条件",
        "production_ids": ["flash"], "render": {}, "render_inheritance": "source_compatible"})
    return prod_env


def test_shipped_catalog_reuses_ten_domains_and_all_five_ai_bindings():
    config, jobs = str(ROOT / "config"), str(ROOT / "jobs")
    matrices_by_key = matrices.load_all(config)
    configs = production.load_all(config, jobs)
    columns = content_columns.load_all(config, configs=configs)
    assert set(content_columns.types(config)) == set(content_columns.TYPE_KEYS)
    assert not {key: c.errors for key, c in columns.items() if c.errors}
    domains = [m for m in matrices_by_key.values() if not m.archived and m.content.get("role", "domain") == "domain"]
    assert len(domains) == 10
    assert not matrices_by_key["education"].jobs
    assert matrices_by_key["education"].content["status"] == "planned"
    for key in ("flash_daily", "ai_media_opensource_digest", "ai_media_arena_digest",
                "ai_media_business_digest", "ai_media_deep_dive"):
        c = content_columns.for_production(config, key, configs=configs)
        assert c and c.matrix == "ai_media"
        assert set(c.render) == {"packaging"}
        assert c.render["packaging"]["label"] == c.name
    assert matrices_by_key["tech"].content["role"] == matrices_by_key["software"].content["role"] == "source"
    assert matrices_by_key["ai"].archived


def test_column_edit_never_changes_production_mtime_or_auto_ledger(column_env):
    config = column_env / "config"
    files = {f: (f.read_bytes(), f.stat().st_mtime_ns) for f in (config / "production").glob("*.yaml")}
    before = auto.pending(str(config), str(column_env / "jobs"), str(column_env / "state"), str(column_env), 2e9)
    column = content_columns.load_all(str(config))["model_review"]
    content_columns.save(str(config), column.key, {**column.data(), "name": "模型比较"}, expected_revision=column.revision)
    after = auto.pending(str(config), str(column_env / "jobs"), str(column_env / "state"), str(column_env), 2e9)
    assert after == before
    assert all((f.read_bytes(), f.stat().st_mtime_ns) == old for f, old in files.items())


def test_binding_has_one_owner_and_edits_require_current_revision(column_env):
    config = str(column_env / "config")
    c = content_columns.load_all(config)["model_review"]
    with pytest.raises(ValueError, match="已属于栏目"):
        content_columns.save(config, "duplicate", c.data())
    with pytest.raises(FileExistsError):
        content_columns.save(config, c.key, c.data(), expected_revision="stale")
    # Files edited outside the UI also surface duplicate ownership; neither wins by filename.
    source = Path(config) / "content_columns/model_review.yaml"
    shutil.copyfile(source, source.with_name("duplicate.yaml"))
    all_columns = content_columns.load_all(config)
    assert all_columns["model_review"].errors and all_columns["duplicate"].errors
    assert content_columns.for_production(config, "flash") is None


def test_cross_domain_render_defaults_have_explicit_priority_and_legacy_mode(column_env):
    config = str(column_env / "config")
    all_matrices = matrices.load_all(config)
    for key, speed in (("ai", 1.25), ("medical", 1.5)):
        m = all_matrices[key]
        matrices.save(config, key, name=m.name, domain=m.domain, keywords=m.keywords, members=m.members,
                      render={"video_speed": speed})
    source = matrices.load_all(config)["ai"]
    context = content_columns.snapshot(config, "flash", "monitoring/ai_flash_discovery")
    assert context["matrix"] == "medical" and context["source_matrix"] == "ai"
    assert content_columns.render_settings(config, source, context, {}) == {"video_speed": 1.25}
    context["render_inheritance"] = "content_matrix"
    assert content_columns.render_settings(config, source, context, {}) == {"video_speed": 1.5}
    context["render"] = {"video_speed": 2}
    assert content_columns.render_settings(config, source, context, {}) == {"video_speed": 2}
    assert content_columns.render_settings(config, source, context, {"video_speed": 1}) == {"video_speed": 1}
    assert content_columns.render_settings(config, source, {}, {}) == {"video_speed": 1.25}


def test_workflow_records_business_identity_before_spawn_and_versions_keep_it(column_env, monkeypatch):
    from web.services import video_workflow
    seen = []
    monkeypatch.setattr(video_workflow, "spawn", lambda sid, by: seen.append(store.get_script(sid)))
    script = video_workflow.start("flash", {"title": "测试模型更新", "_production": "flash"}, "editor")
    assert seen[0]["production"] == "flash"
    assert script["column_key"] == "flash"
    assert script["content_column_id"] == "model_review" and script["content_matrix_id"] == "medical"
    assert script["source_matrix_id"] == "ai"
    assert "_production" not in script["topic"]
    no = store.add_version(script["id"], title="测试", sections=[], length_check={}, edit_type="manual")
    assert store.get_version(script["id"], no)["meta"]["content_context"] == script["content_context"]
    old = store.create_script("flash", {"title": "旧入口"})
    assert old["column_key"] == "flash" and old["content_context"] == {} and old["content_column_id"] == ""


def test_shared_type_changes_editorial_goal_without_restricting_report_or_object_count(column_env):
    from core.video import columns
    config = str(column_env / "config")
    recipe = columns.get(config, "flash")
    context = content_columns.snapshot(config, "flash", "monitoring/ai_flash_discovery")
    result = content_columns.apply_script_context(recipe, context)
    assert result.key == recipe.key and result.topic_kind == recipe.topic_kind
    assert result.research_prompt == recipe.research_prompt
    assert "对象数量沿用材料" in result.script_style and "讲清对象差异" in result.script_style
    assert content_columns.apply_script_context(recipe, {}) is recipe


def test_column_brief_reaches_single_call_writer_and_saved_context_stays_frozen(column_env, llm):
    from core.video import columns, writer

    config = str(column_env / "config")
    c = content_columns.load_all(config)["model_review"]
    content_columns.save(config, c.key, {**c.data(), "notes": "围绕批量处理解释价格与响应速度的取舍。"})
    context = content_columns.snapshot(config, "flash", "monitoring/ai_flash_discovery")
    recipe = content_columns.apply_script_context(columns.get(config, "flash"), context)
    assert columns.get(config, "flash").script_style not in recipe.script_style
    llm.replies = [script_json()]
    written = writer.write(report=REPORT, citation_check=None, topic={"title": "批量任务怎样选模型"},
                           column_style=recipe.script_style, target_minutes=recipe.target_minutes, llm_config=None)
    assert len(llm.calls) == 1
    assert "围绕批量处理解释价格与响应速度的取舍" in llm.calls[0][1]["content"]
    assert written.meta["creative_direction"] == recipe.script_style
    content_columns.save(config, c.key, {**c.data(), "notes": "改为长文档问题。"})
    frozen = content_columns.apply_script_context(columns.get(config, "flash"), context)
    assert frozen.script_style == recipe.script_style
    assert "改为长文档问题" not in frozen.script_style


def test_saved_presentation_strategy_reaches_render_direction_and_old_contexts_still_work(column_env):
    config = str(column_env / "config")
    c = content_columns.load_all(config)["model_review"]
    content_columns.save(config, c.key, {**c.data(), "presentation": {
        "strategy": "先出现价格，再突出响应速度；同一对象保持配色。", "preferred_visuals": ["散点图"]}})
    context = content_columns.snapshot(config, "flash", "monitoring/ai_flash_discovery")
    direction = content_columns.render_direction(context)
    assert "先出现价格，再突出响应速度" in direction and "散点图" in direction
    assert content_columns.render_direction({}) == ""
    assert content_columns.render_direction({"column": "old", "promise": "旧目标"}) == "栏目：。讲解目标：旧目标。"


def test_saved_business_owner_and_source_owner_remain_distinct(column_env):
    from core.video import content_guard, outputs
    config, jobs = str(column_env / "config"), str(column_env / "jobs")
    context = content_columns.snapshot(config, "flash", "monitoring/ai_flash_discovery")
    script = store.create_script("flash", {"title": "跨领域比较"}, production="flash", content_context=context)
    owners, _ = outputs.script_owners(config, jobs, [script])
    assert owners[script["id"]]["matrix"] == "medical"
    assert owners[script["id"]]["source_matrix"] == "ai"
    assert owners[script["id"]]["content_column_name"] == "模型横评"
    assert outputs.collect(config, jobs)["scripts"][0]["matrix"] == "medical"
    store.add_version(script["id"], title="跨领域比较", sections=[], length_check={}, edit_type="manual")
    assert content_guard.history(config, jobs)[0]["scope"] == "matrix:ai"
    # Removing a binding later must not erase the saved source or break output counts.
    (Path(config) / "production/flash.yaml").unlink()
    owners, _ = outputs.script_owners(config, jobs, [store.get_script(script["id"])])
    assert owners[script["id"]]["job"] == "monitoring/ai_flash_discovery"
    matrices.remove(config, "medical")
    outputs.count_by_report(config, jobs)


def test_matrix_business_metadata_survives_legacy_save_without_changing_prompt_fields(column_env):
    config = str(column_env / "config")
    m = matrices.load_all(config)["ai"]
    meta = {"name": "AI 与科技", "role": "domain", "audience": "开发者"}
    matrices.save(config, m.key, name=m.name, domain=m.domain, keywords=m.keywords, members=m.members, content=meta)
    matrices.save(config, m.key, name="改显示名称", domain=m.domain, keywords=m.keywords, members=m.members)
    after = matrices.load_all(config)[m.key]
    assert after.content == meta and after.domain == m.domain and after.members == m.members


def test_api_column_list_edit_conflict_and_viewer_permissions(client, column_env):
    _login(client, "editor", "editor-pass-123")
    body = client.get("/api/content-columns?matrix=medical").json()
    assert len(body["types"]) == 6
    c = body["columns"][0]
    assert c["key"] == "model_review" and c["bindings"][0]["source_matrix"] == "ai"
    payload = {**content_columns.load_all(str(column_env / "config"))[c["key"]].data(),
               "expected_revision": c["revision"], "name": "模型与工具横评"}
    payload.pop("schema_version")
    response = client.put("/api/content-columns/model_review", json=payload, headers=_csrf(client))
    assert response.status_code == 200 and response.json()["name"] == "模型与工具横评"
    assert client.put("/api/content-columns/model_review", json=payload, headers=_csrf(client)).status_code == 409
    _login(client, "viewer", "viewer-pass-123")
    assert client.get("/api/column-types").status_code == 200
    assert client.put("/api/content-columns/model_review", json=payload, headers=_csrf(client)).status_code == 403


def test_column_entry_reads_matrix_without_historical_report_counts(client, column_env, monkeypatch):
    from web.routes import matrices as routes

    _login(client, "viewer", "viewer-pass-123")
    monkeypatch.setattr(routes, "_latest_reports", lambda *args: pytest.fail("column entry should not count all historical scripts"))
    response = client.get("/api/matrices/medical?reports=false")
    assert response.status_code == 200 and response.json()["key"] == "medical"


@pytest.mark.parametrize("patch", [{"render": {"preferred_visuals": ["map"]}}, {"type": "kinetic_typography"},
                                   {"production_ids": ["../escape"]}, {"matrix": "missing"}])
def test_invalid_column_configuration_is_rejected(column_env, patch):
    config = str(column_env / "config")
    c = content_columns.load_all(config)["model_review"]
    with pytest.raises(ValueError):
        content_columns.save(config, c.key, {**c.data(), **patch})

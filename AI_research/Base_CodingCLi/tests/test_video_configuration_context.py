"""Configuration links resolve real recipe variants and preserve generation provenance."""
from tests.golden_video_cells import install_recipes
from core.video import recipes
from web.services.video_configuration import context
from tests.test_content_columns import column_env  # noqa: F401
from tests.test_production import prod_env  # noqa: F401
from tests.test_video_scripts import video_env  # noqa: F401


def test_audience_variant_links_to_base_editor_and_correct_shared_part_routes(tmp_path):
    config = tmp_path / "config"
    install_recipes(config)
    script = {"column_key": "ai-non_tech-quick_take-short"}
    out = context(script, None, str(config), str(tmp_path / "jobs"))
    links = {l["label"]: l for l in out["links"]}
    assert links["文稿配方与提示词"]["href"] == "/video-recipes/flash"
    assert links["受众与表达深度"]["href"] == "/video-recipes/parts/audience/non_tech"
    assert links["出片形式与配音"]["href"] == "/video-recipes/parts/output/motion"
    assert links["片头片尾与包装"]["href"] == "/video-recipes/parts/packaging/common"
    assert out["changed_since_generation"] is None


def test_config_changes_are_detected_without_modifying_saved_version(tmp_path):
    import copy
    config = tmp_path / "config"
    install_recipes(config)
    script = {"column_key": "flash"}
    version = {"meta": {"cell": recipes.provenance(str(config), "flash")}}
    original = copy.deepcopy(version)
    assert context(script, version, str(config), str(tmp_path / "jobs"))["changed_since_generation"] is False
    path = config / "video_recipes/flash.yaml"
    path.write_text(path.read_text().replace("target_minutes: 1.25", "target_minutes: 1.5"))
    assert context(script, version, str(config), str(tmp_path / "jobs"))["changed_since_generation"] is True
    assert version == original


def test_missing_recipe_leaves_detail_readable_without_guessed_links(tmp_path):
    out = context({"column_key": "removed"}, None, str(tmp_path), str(tmp_path))
    assert not out["recipe_available"] and out["links"] == []


def test_invalid_production_yaml_does_not_hide_saved_script(tmp_path):
    config = tmp_path / "config"
    install_recipes(config)
    (config / "production").mkdir()
    (config / "production/broken.yaml").write_text("job: [invalid")
    out = context({"column_key": "flash", "production": "broken"}, None, str(config), str(tmp_path / "jobs"))
    assert out["recipe_available"] and out["links"][0]["href"] == "/video-recipes/flash"


def test_matrix_defaults_and_task_configuration_have_distinct_links(tmp_path):
    config = tmp_path / "config"
    install_recipes(config)
    (config / "production").mkdir()
    (config / "production/flash.yaml").write_text("job: monitoring/source\nrecipe: flash\n")
    (config / "matrices").mkdir()
    (config / "matrices/media.yaml").write_text("name: 栏目\njobs: [monitoring/source]\nrender:\n  video_speed: 1.5\n")
    out = context({"column_key": "flash", "production": "flash"}, None, str(config), str(tmp_path / "jobs"))
    links = {link["label"]: link for link in out["links"]}
    assert links["矩阵的共用视频设置"]["href"] == "/matrices/media?tab=settings"
    assert links["任务的视频生成设置"]["href"] == "/matrices/media/video/monitoring%2Fsource"


def test_column_override_links_to_real_editor_and_explains_frozen_scope(column_env):
    from core.video import content_columns
    config = str(column_env / "config")
    saved = {**content_columns.snapshot(config, "flash", "monitoring/ai_flash_discovery"), "notes": "保存时的栏目方向"}
    script = {"column_key": "flash", "content_context": saved}
    version = {"meta": {"content_context": saved, "creative_direction": "模型实际接收的完整方向"}}
    column = content_columns.load_all(config)["model_review"]
    content_columns.save(config, column.key, {**column.data(), "notes": "之后更新的方向"})
    out = context(script, version, config, str(column_env / "jobs"))
    links = {link["label"]: link for link in out["links"]}
    assert links["栏目创作方向"]["href"] == "/matrices/medical?tab=columns&column=model_review"
    assert "覆盖" in links["文稿配方与提示词"]["next"]
    assert "之后新建" in links["栏目创作方向"]["scope"]
    assert out["writing"]["source"] == "content_column"
    assert out["writing"]["direction"] == "模型实际接收的完整方向"
    assert "本稿重新生成仍沿用" in out["writing"]["note"]
    assert saved["notes"] == "保存时的栏目方向"
    # An empty saved column direction still inherits the editable recipe style.
    inherited = context({**script, "content_context": {**saved, "notes": ""}}, None, config, str(column_env / "jobs"))
    assert inherited["writing"]["source"] == "recipe"


def test_removed_column_retains_saved_direction_without_an_editor_link(tmp_path):
    out = context({"column_key": "removed", "content_context": {"column": "old", "notes": "保留方向"}},
                  {"meta": {"creative_direction": "已保存方向"}}, str(tmp_path), str(tmp_path))
    assert out["links"] == [] and out["writing"]["source"] == "content_column"
    assert out["writing"]["direction"] == "已保存方向" and "已不存在" in out["writing"]["note"]


def test_article_processing_is_not_mislabeled_as_recipe_writing(tmp_path):
    for mode in ("read", "adapt"):
        out = context({"column_key": "removed", "topic": {"article": {"mode": mode}}}, None, str(tmp_path), str(tmp_path))
        assert out["writing"]["source"] == "article"
        assert ("原文直接朗读" if mode == "read" else "文章改编") in out["writing"]["note"]


def test_recipe_only_script_also_explains_its_saved_target_audience(tmp_path):
    config = tmp_path / "config"
    install_recipes(config)
    script = {"column_key": "ai-non_tech-quick_take-short", "topic": {"fields": {"目标受众": "保存时的观众"}}}
    out = context(script, None, str(config), str(tmp_path / "jobs"))
    link = next(link for link in out["links"] if link["label"] == "受众与表达深度")
    assert "受众已随选题保存" in link["next"]
    assert "后续新稿" in link["next"]
    assert out["writing"]["source"] == "recipe"


def test_broken_column_is_not_reported_as_deleted(column_env):
    from core.video import content_columns
    config = column_env / "config"
    saved = content_columns.snapshot(str(config), "flash", "monitoring/ai_flash_discovery")
    saved["notes"] = "已保存的栏目方向"
    (config / "content_columns/model_review.yaml").write_text("name: [broken")
    out = context({"column_key": "flash", "content_context": saved},
                  {"meta": {"creative_direction": "当时使用的方向"}}, str(config), str(column_env / "jobs"))
    assert out["writing"]["direction"] == "当时使用的方向"
    assert "配置存在错误" in out["writing"]["note"]
    assert "已不存在" not in out["writing"]["note"]

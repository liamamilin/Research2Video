"""Configuration links resolve real recipe variants and preserve generation provenance."""
from tests.golden_video_cells import install_recipes
from core.video import recipes
from web.services.video_configuration import context


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

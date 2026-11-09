from types import SimpleNamespace

import pytest
import yaml

from core.video import production, recipes, store
from core.video.production_settings import validate
from tests.test_video_scripts import video_env, _approved_script  # noqa: F401
from web.services import video_render


def test_explicit_fast_render_preserves_approved_words_and_packaging(video_env):
    script = _approved_script()
    old = video_render.create(script)
    store.update_render(old["id"], status="COMPLETED")
    fast = video_render.create(script, production_mode="fast")
    request = fast["request"]
    assert request["production_mode"] == "fast"
    assert request["video_form"] == "information_cards"
    assert request["visual_style"] == "editorial_light"
    assert request["chapters"] == old["request"]["chapters"]
    assert fast["source_sha256"] == old["source_sha256"]
    settings = request["production_context"]["content_recipe"]["settings"]
    assert settings["packaging"] == old["request"]["packaging"]
    assert settings["voice_id"] == old["request"]["voice_id"]


def test_matrix_settings_are_a_named_layer_below_current_explicit_choices(video_env, monkeypatch):
    script = {**_approved_script(), "production": "technology"}
    config = SimpleNamespace(usable=True, key="technology", render={
        "production_recipe": "ai_news", "voice_id": "zf_xiaoxiao", "packaging": {"sound": {"enabled": False}}})
    monkeypatch.setattr(production, "get", lambda *args: config)
    result = video_render.create(script, voice_id="zm_yunxi", production_mode="fast")["request"]
    assert result["production_recipe"] == "ai_news"
    assert result["voice_id"] == "zm_yunxi"
    assert result["production_context"]["matrix"]["settings"]["voice_id"] == "zf_xiaoxiao"
    assert result["production_context"]["matrix"]["settings"]["packaging"]["sound"]["enabled"] is False


def test_recipe_can_reference_autovideo_recipe_and_keep_only_explicit_overrides(video_env):
    path = video_env / "config/video_recipes/deep_dive.yaml"
    value = yaml.safe_load(path.read_text())
    value.setdefault("output", {})["production_recipe"] = "science_explainer"
    path.write_text(yaml.safe_dump(value, allow_unicode=True))
    recipe = recipes.find(str(video_env / "config"), "deep_dive")
    assert not recipe.error
    assert recipe.column.production_recipe == "science_explainer"
    request = video_render.create(_approved_script())["request"]
    assert request["production_recipe"] == "science_explainer"
    assert request["production_context"]["content_recipe"]["id"] == "research:deep_dive"


@pytest.mark.parametrize("matrix_voice,chosen_voice", [
    (None, None), ("zm_yunxi", None), ("zm_yunxi", "zf_xiaoxiao"),
])
def test_visual_recipe_keeps_app_narrator_and_voice_override_priority(video_env, monkeypatch, matrix_voice, chosen_voice):
    from core.video import columns

    config = SimpleNamespace(usable=True, key="tutorial", job="research/source", render={
        "production_recipe": "mixed_tutorial",
        **({"voice_id": matrix_voice} if matrix_voice else {}),
    })
    monkeypatch.setattr(production, "get", lambda *args: config)
    script = {**_approved_script(), "production": "tutorial"}
    request = video_render.create(script, voice_id=chosen_voice)["request"]
    narrator = columns.get(str(video_env / "config"), script["column_key"]).voice_id
    assert narrator == "voxcpm_anchor"
    content = request["production_context"]["content_recipe"]["settings"]
    assert content["voice_id"] == narrator and "video_form" not in content
    assert request.get("voice_id") == chosen_voice
    matrix = request["production_context"]["matrix"]["settings"]
    assert matrix.get("voice_id") == matrix_voice
    assert (request.get("voice_id") or matrix.get("voice_id") or content["voice_id"]) == (chosen_voice or matrix_voice or narrator)


def test_saving_old_matrix_editor_settings_keeps_explicit_render_overrides(tmp_path):
    root = tmp_path / "production"
    root.mkdir()
    (root / "tech.yaml").write_text("job: tech\nrecipe: digest\nrender:\n  production_mode: fast\n  video_form: information_cards\n  visual_style: editorial_light\n")
    production.save(str(tmp_path), "tech", job="tech", recipe="digest", name="技术")
    result = yaml.safe_load((root / "tech.yaml").read_text())
    assert result["render"]["production_mode"] == "fast"


@pytest.mark.parametrize("settings", [{"production_mode": "unknown"}, {"visual_style": None}, {"bogus": True}])
def test_invalid_upstream_render_settings_are_not_silently_dropped(settings):
    with pytest.raises(ValueError):
        validate(settings)


def test_matrix_speed_is_inherited_by_new_configs_and_preserves_old_requests(video_env, monkeypatch):
    from core import matrices

    matrices.save(str(video_env / "config"), "media", name="视频矩阵", domain="AI", keywords=[],
                  members=[matrices.Member("research/source")], render={"video_speed": 1.5})
    config = SimpleNamespace(usable=True, key="first", job="research/source", render={})
    monkeypatch.setattr(production, "get", lambda *args: config)
    script = {**_approved_script(), "production": "first"}
    first = video_render.create(script)
    assert first["request"]["production_context"]["matrix"]["settings"]["video_speed"] == 1.5
    store.update_render(first["id"], status="COMPLETED")
    # A new production config on the same Job inherits without another edit.
    config.key = "second"
    second = video_render.create({**script, "production": "second"}, production_mode="fast")
    assert second["request"]["production_context"]["matrix"]["settings"]["video_speed"] == 1.5
    store.update_render(second["id"], status="COMPLETED")
    # Deliberately choosing normal speed on a task overrides the matrix.
    config.render = {"video_speed": 1}
    third = video_render.create(script)
    assert third["request"]["production_context"]["matrix"]["settings"]["video_speed"] == 1
    assert store.get_render(first["id"])["request"] == first["request"]


def test_older_script_without_production_inherits_its_source_matrix(video_env, monkeypatch):
    from core import matrices
    from core.video import outputs

    matrices.save(str(video_env / "config"), "history", name="历史", domain="历史", keywords=[],
                  members=[], render={"video_speed": 1.5})
    parent = _approved_script("父稿")
    script = _approved_script("剪出的子稿")
    script["topic"]["parent_script"] = parent["id"]

    def owners(config_dir, jobs_dir, related):
        assert [s["id"] for s in related] == [script["id"], parent["id"]]
        return {script["id"]: {"matrix": "history"}}, []

    monkeypatch.setattr(outputs, "script_owners", owners)
    result = video_render.create(script)["request"]
    assert result["production_context"]["matrix"] == {
        "id": "research-matrix:history", "settings": {"video_speed": 1.5}}


def test_task_overrides_only_explicit_fields_including_nested_settings():
    from core.video.production_settings import inherit

    defaults = {"video_speed": 1.5, "voice_id": "voxcpm_anchor",
                "packaging": {"intro": {"seconds": 1.5}, "sound": {"enabled": True, "gain_db": -9}}}
    task = {"video_speed": 1, "packaging": {"sound": {"enabled": False}}}
    merged = inherit(defaults, task)
    assert merged["video_speed"] == 1 and merged["voice_id"] == "voxcpm_anchor"
    assert merged["packaging"] == {"intro": {"seconds": 1.5}, "sound": {"enabled": False, "gain_db": -9}}
    merged["packaging"]["intro"]["seconds"] = 3
    assert defaults["packaging"]["intro"]["seconds"] == 1.5
    assert task["packaging"]["sound"] == {"enabled": False}


def test_saved_column_keeps_source_defaults_when_binding_is_removed(video_env):
    from core import matrices

    config_dir = str(video_env / "config")
    matrices.save(config_dir, "source", name="研究来源", domain="来源", keywords=[],
                  members=[], render={"video_speed": 1.25})
    matrices.save(config_dir, "business", name="业务领域", domain="业务", keywords=[],
                  members=[], render={"video_speed": 1.5})
    script = {**_approved_script(), "production": "removed", "source_matrix_id": "source",
              "content_column_id": "model_review", "content_matrix_id": "business",
              "content_context": {"column": "model_review", "column_name": "横评", "matrix": "business",
                                  "render_inheritance": "source_compatible", "render": {"production_recipe": "mixed_comparison"}}}
    rendered = video_render.create(script)
    assert rendered["request"]["production_context"]["matrix"]["settings"]["video_speed"] == 1.25
    assert rendered["request"]["production_recipe"] == "mixed_comparison"
    store.update_render(rendered["id"], status="COMPLETED")
    matrices.remove(config_dir, "source")
    # A removed source still leaves the saved column's own settings usable.
    assert video_render.create(script)["request"]["production_context"]["matrix"]["id"] == "research-column:model_review"


def test_illustration_style_is_a_production_setting_sent_in_the_matrix_layer(video_env, monkeypatch):
    assert validate({"illustration_style": "geometric"}) == {"illustration_style": "geometric"}
    for bad in ("Geometric", "../flat", 3, ""):
        with pytest.raises(ValueError, match="illustration_style"):
            validate({"illustration_style": bad})
    script = {**_approved_script(), "production": "economy_illustrated"}
    config = SimpleNamespace(usable=True, key="economy_illustrated", render={"illustration_style": "geometric"})
    monkeypatch.setattr(production, "get", lambda *args: config)
    request = video_render.create(script)["request"]
    assert request["production_context"]["matrix"] == {
        "id": "research-production:economy_illustrated", "settings": {"illustration_style": "geometric"}}
    assert "illustration_style" not in request  # AutoVideo resolves it from the layer


def test_png_artifacts_are_downloadable_names():
    from core.video.autovideo import ARTIFACT_NAME
    assert ARTIFACT_NAME.fullmatch("cover-frame.png")
    assert not ARTIFACT_NAME.fullmatch("../cover-frame.png") and not ARTIFACT_NAME.fullmatch("cover.exe")

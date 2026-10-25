from pathlib import Path

import pytest

from core.video import packaging, recipes
from tests.test_video_scripts import video_env, autovideo, _approved_script, _login, _csrf  # noqa: F401
from web.services import video_render

ROOT = Path(__file__).resolve().parents[1]


def test_all_shipped_recipes_reference_the_common_packaging_part():
    for recipe in recipes.expanded(str(ROOT / "config")).values():
        assert not recipe.error
        assert recipe.column.packaging["preset"] == "common"
        assert recipe.uses["packaging"]["name"] == "common"
        assert recipe.column.package == {}


def test_common_nested_defaults_and_recipe_overrides_are_merged(video_env):
    shared = video_env / "config/video_parts/packaging/common.yaml"
    shared.write_text("preset: common\nintro: {seconds: 2}\noutro: {seconds: 4}\nsound: {enabled: false}\n")
    flash = recipes.find(str(video_env / "config"), "flash")
    assert flash.column.packaging["intro"]["seconds"] == 1.5  # recipe override
    assert flash.column.packaging["sound"] == {"enabled": False}  # inherited
    deep = recipes.find(str(video_env / "config"), "deep_dive")
    assert deep.column.packaging["intro"]["seconds"] == 2
    sent = video_render.packaging_request(flash.column, {"fields": {"发布日期": "2026-10-21"}})
    assert sent["intro"]["subtitle"] == "10月21日"
    assert "subtitle" not in video_render.packaging_request(flash.column, {"fields": {}})["intro"]


def test_manual_render_sends_recipe_selection_to_the_shared_entry_point(video_env, autovideo):
    render = video_render.create(_approved_script())
    assert "package" not in render["request"]
    assert render["request"]["packaging"]["label"] == "深度解读"
    assert render["request"]["packaging"]["recipe"] == "deep_dive"
    assert video_render.process(render["id"]) == "COMPLETED"
    assert autovideo.posts[0]["packaging"] == render["request"]["packaging"]


def test_common_packaging_is_editable_and_bad_changes_are_rejected(client, video_env):
    _login(client)
    rows = client.get("/api/video/recipes/parts").json()
    part = next(p for p in rows if p["kind"] == "packaging" and p["name"] == "common")
    assert {"flash", "deep_dive"} <= set(part["used_by"])
    response = client.put("/api/video/recipes/parts/packaging/common", headers=_csrf(client),
                          json={"content": "preset: common\nintro: {component: arbitrary_code}\n"})
    assert response.status_code == 422


@pytest.mark.parametrize("value", [{"source_card": {"sources": []}}, {"sound": {"component": "unknown"}},
                                   {"intro": {"seconds": True}}, {"enabled": "false"}])
def test_bad_packaging_selections_are_rejected_before_render(value):
    with pytest.raises(ValueError):
        packaging.validate(value)

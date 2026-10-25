"""Packaging follows the actual content palette, while old snapshots stay stable."""
import copy
import json
import re
from pathlib import Path

import pytest
from PIL import Image

from g2 import packaging_library as library
from g2.packaging_library.templates import render_clip_frames, visual_theme


@pytest.mark.parametrize("preset,style,expected", [
    ("information_motion", {"id": "legacy"}, "information_motion"),
    ("information_cards", {"id": "editorial_light"}, "editorial_light"),
    ("information_cards", {"id": "legacy"}, "information_cards"),
    (None, {"id": "legacy"}, "legacy_dark"),
    *[(form, {"id": "legacy"}, "creative_light") for form in (
        "kinetic_typography", "image_slideshow", "chat_dialogue", "object_showcase", "dynamic_collage")],
])
def test_palette_follows_rendered_form_instead_of_legacy_plan_label(preset, style, expected):
    ir = {"presentation": {"preset": preset}} if preset else {"style": {"preset": "minimal_dark"}}
    assert visual_theme(ir, style)["id"] == expected


def test_renderer_palette_changes_require_updating_packaging_match():
    root = Path(__file__).resolve().parents[2] / "g1/renderer/src"
    source = (root / "Root.tsx").read_text().lower()
    creative = (root / "CreativeForms.tsx").read_text().lower()
    for preset, name in [("information_motion", "paper"), (None, "colors")]:
        block = re.search(rf"const {name} = \{{([^}}]+)\}}", source).group(1)
        palette = dict(re.findall(r"(\w+):\s*'(#[0-9a-f]+)'", block))
        matched = visual_theme({"presentation": {"preset": preset}})
        assert matched["background"] == palette["bg"]
        assert matched["text"] == palette["ink" if name == "paper" else "text"]
        assert matched["muted"] == palette["muted"]
    theme = visual_theme({"presentation": {"preset": "chat_dialogue"}})
    assert f"cream = '{theme['background']}'" in creative
    assert f"const ink = '{theme['text']}'" in creative
    assert f"muted = '{theme['muted']}'" in creative


def test_fast_packaging_uses_the_frozen_content_tokens():
    theme = visual_theme({}, {"id": "editorial_light", "background": "#eeeeee"})
    assert theme["background"] == "#eeeeee"


@pytest.mark.parametrize("kind,cfg", [
    ("end_card", {"title": "专题速览", "lines": ["主要来源见简介"]}),
    ("source_card", {"title": "来源", "sources": [
        {"title": "官方说明", "domain": "example.com", "date": "2026-10-25"}]}),
])
@pytest.mark.parametrize("theme", [visual_theme({"presentation": {"preset": "information_motion"}}),
                                  visual_theme({}, {"id": "editorial_light"}), visual_theme({})])
def test_v2_removes_edge_strip_and_keeps_decoration_inside_content_safe_area(tmp_path, kind, cfg, theme):
    render_clip_frames(kind, cfg, "#1f7a4d", 1, tmp_path, version=2, theme=theme)
    with Image.open(tmp_path / "f0000.png") as frame:
        background = tuple(int(theme["background"][i:i + 2], 16) for i in (1, 3, 5))
        assert frame.size == (1920, 1080)
        assert all(frame.getpixel((0, y)) == background for y in range(1080))
        assert frame.getpixel((180, 72)) == (31, 122, 77)


def test_v1_ignores_new_theme_and_retains_historical_layout(tmp_path):
    render_clip_frames("end_card", {"title": "快讯"}, "#1f7a4d", 1, tmp_path,
                       version=1, theme=visual_theme({}))
    with Image.open(tmp_path / "f0000.png") as frame:
        assert frame.getpixel((0, 540)) == (31, 122, 77)
        assert frame.getpixel((40, 540)) == (246, 248, 251)


def test_new_selections_use_v2_but_old_snapshots_and_legacy_requests_remain_v1():
    current = library.resolve({"label": "专题速览"})
    assert current["component_versions"]["outro"]["version"] == 2
    assert current["component_versions"]["source_card"]["version"] == 2
    old = copy.deepcopy(current)
    old["version"] = 1
    for slot in ("outro", "source_card"):
        old["component_versions"][slot]["version"] = 1
    old["sha256"] = library._digest({k: v for k, v in old.items() if k != "sha256"})
    assert library.prepare_request({"packaging_resolved": old})["packaging_resolved"] == old
    legacy = library.prepare_request({"package": {"accent": "#1f7a4d", "outro": {
        "template": "end_card", "title": "旧片尾", "seconds": 2}}})
    assert legacy["packaging_resolved"]["component_versions"]["outro"]["version"] == 1


@pytest.mark.parametrize("preset,style,expected", [
    ("information_motion", {"id": "legacy"}, "information_motion"),
    ("information_cards", {"id": "editorial_light"}, "editorial_light"),
    (None, {"id": "legacy"}, "legacy_dark"),
])
def test_shared_package_stage_passes_actual_content_theme(monkeypatch, tmp_path, preset, style, expected):
    from g2 import run_g2 as pipeline
    from g2 import package_stage

    path = tmp_path / "ir.json"
    path.write_text(json.dumps({"presentation": {"preset": preset}, "scenes": []}))
    record = library.resolve()
    monkeypatch.setattr(pipeline, "IR_PATH", path)
    monkeypatch.setattr(pipeline, "packaging_record", lambda: record)
    monkeypatch.setattr(pipeline, "PACKAGING_INPUT", {"production_plan": {"style": style}})
    monkeypatch.setattr(package_stage, "build_package", lambda *args, **kwargs: kwargs)
    result = pipeline.run_package_stage(record["package"])
    assert result["visual_theme"]["id"] == expected
    assert result["packaging_record"] == record


def test_unregistered_renderer_version_fails_explicitly(tmp_path):
    with pytest.raises(ValueError, match="未注册的模板版本"):
        render_clip_frames("end_card", {"title": "片尾"}, "#1f7a4d", 1, tmp_path, version=99)

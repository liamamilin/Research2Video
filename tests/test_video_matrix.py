"""The content matrix: cells composed from four definitions (spec/video-matrix.md, phase 1)."""

from __future__ import annotations

import copy
import dataclasses
from pathlib import Path

import pytest
import yaml

from core.video import columns as video_columns
from core.video import matrix

ROOT = Path(__file__).resolve().parents[1]


def _shipped() -> dict:
    return yaml.safe_load((ROOT / "config" / "video_matrix.yaml").read_text(encoding="utf-8"))


def _write(tmp_path, data: dict) -> str:
    (tmp_path / "video_matrix.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return str(tmp_path)


def test_the_shipped_cells_keep_their_old_column_keys_and_are_enabled():
    cells = matrix.load(str(ROOT / "config"))
    assert {c.column.key: c.status for c in cells.values() if c.parts["audience"]["key"] == "developer"} == {
        "deep_dive": "enabled", "flash": "enabled", "point_short": "enabled",
        "ai-developer-roundup-long": "experimental", "ai-developer-comparison-long": "experimental"}
    # the second audience is defined but has never been rendered
    assert {c.id: c.status for c in cells.values() if c.parts["audience"]["key"] == "non_tech"} == {
        "ai-non_tech-deep_dive-long": "experimental", "ai-non_tech-one_point-short": "experimental",
        "ai-non_tech-quick_take-short": "experimental"}
    assert "ai-developer-quick_take-short" in cells
    assert cells["ai-developer-quick_take-short"].parts["audience"] == {"key": "developer", "version": 1}


def test_a_cell_is_found_by_its_id_and_by_its_column_key():
    config = str(ROOT / "config")
    assert video_columns.get(config, "ai-developer-quick_take-short") == video_columns.get(config, "flash")
    assert sorted(video_columns.load(config)) == sorted(["deep_dive", "flash", "point_short", "ai-non_tech-deep_dive-long", "ai-non_tech-one_point-short", "ai-non_tech-quick_take-short", "ai-developer-roundup-long", "ai-developer-comparison-long"])  # one per cell
    assert matrix.provenance(config, "flash")["cell"] == "ai-developer-quick_take-short"
    assert matrix.provenance(config, "no-such-column") is None


def test_the_types_setting_for_a_format_beats_the_format_which_beats_the_type():
    data = _shipped()
    data["formats"]["short"]["target_minutes"] = 2
    data["types"]["quick_take"]["formats"]["short"]["target_minutes"] = 1.25
    data["types"]["one_point"]["formats"]["short"].pop("target_minutes")
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        loaded = video_columns.load(_write(Path(tmp), data))
    assert loaded["flash"].target_minutes == 1.25 and loaded["point_short"].target_minutes == 2


def test_the_audience_limits_the_tones(tmp_path):
    data = _shipped()
    data["audiences"]["developer"]["tones"] = ["concise", "explainer"]
    loaded = video_columns.load(_write(tmp_path, data))
    assert loaded["flash"].allowed_tones == ("concise",)
    assert loaded["deep_dive"].allowed_tones == ("concise", "explainer")
    data["audiences"]["developer"]["tones"] = ["story"]
    with pytest.raises(video_columns.ColumnError, match="没有交集"):
        video_columns.load(_write(tmp_path, data))


def test_a_type_without_a_format_cannot_be_made_into_a_cell(tmp_path):
    data = _shipped()
    data["cells"].append({"domain": "ai", "audience": "developer", "type": "one_point", "format": "long", "status": "experimental"})
    with pytest.raises(video_columns.ColumnError, match="ai-developer-one_point-long.*没有形态 long"):
        video_columns.load(_write(tmp_path, data))


def test_experimental_cells_load_and_blocked_cells_say_why(tmp_path):
    data = _shipped()
    data["cells"][1]["status"] = "experimental"
    data["cells"][2].update(status="blocked", reason="竖屏需要新的画面版本")
    config = _write(tmp_path, data)
    loaded = video_columns.load(config)
    assert "point_short" not in loaded and {"deep_dive", "flash"} <= set(loaded)
    assert matrix.provenance(config, "flash")["status"] == "experimental"
    with pytest.raises(video_columns.ColumnError, match="暂不能用：竖屏需要新的画面版本"):
        video_columns.get(config, "ai-developer-one_point-short")
    data["cells"][2].pop("reason")
    with pytest.raises(video_columns.ColumnError, match="要写 reason"):
        video_columns.load(_write(tmp_path, data))


@pytest.mark.parametrize("edit, message", [
    (lambda d: d["cells"][0].update(audience="nobody"), "没有这个audience"),
    (lambda d: d["cells"].append(copy.deepcopy(d["cells"][0])), "重复"),
    (lambda d: d["audiences"]["developer"].pop("version"), "version"),
    (lambda d: d["cells"][0].update(status="live"), "status"),
    (lambda d: d["cells"][0].update(key="Bad Key"), "key"),
    (lambda d: d["types"]["deep_dive"]["formats"]["long"].update(colour="red"), "colour"),
    (lambda d: d["types"]["deep_dive"]["formats"]["long"].update(target_minutes=99), "target_minutes"),
])
def test_a_bad_definition_names_what_is_wrong(tmp_path, edit, message):
    data = _shipped()
    edit(data)
    with pytest.raises(video_columns.ColumnError, match=message):
        video_columns.load(_write(tmp_path, data))


def test_a_flat_column_may_not_reuse_a_cells_key(tmp_path):
    data = _shipped()
    config = _write(tmp_path, data)
    (tmp_path / "video_columns.yaml").write_text("flash:\n  research_prompt: x\n", encoding="utf-8")
    with pytest.raises(video_columns.ColumnError, match="同时出现"):
        video_columns.load(config)
    (tmp_path / "video_columns.yaml").write_text("custom:\n  research_prompt: x\n", encoding="utf-8")
    assert {"custom", "deep_dive", "flash", "point_short"} <= set(video_columns.load(config))


def test_composition_equals_the_flat_settings_the_columns_used_to_have():
    """The shipped cells, dumped as a flat file and loaded the old way, are the same columns."""
    from tests import golden_video_cells as golden
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "video_columns.yaml").write_text(golden.shipped_flat_yaml(), encoding="utf-8")
        flat = video_columns.load(tmp)
    shipped = video_columns.load(str(ROOT / "config"))
    assert {k: dataclasses.asdict(c) for k, c in flat.items()} == {k: dataclasses.asdict(c) for k, c in shipped.items()}


# --- phase 2: the audience as a dimension, and the video form under the type ---------------------------------------


def test_the_second_audience_fills_its_own_words_into_a_types_text():
    config = str(ROOT / "config")
    developer = video_columns.get(config, "flash")
    plain = video_columns.get(config, "ai-non_tech-quick_take-short")
    assert plain.audience == "普通用户" and developer.audience == "开发者"
    for text in (plain.research_prompt, plain.script_style, plain.description, str(plain.package)):
        assert "开发者" not in text and "{audience" not in text
    assert "对普通用户的影响" in plain.research_prompt and "每月多花或少花多少" in plain.script_style
    assert "不写代码" in plain.script_style and "不写代码" in plain.research_prompt  # its guidance and research lines
    assert plain.package["outro"]["lines"] == ["面向普通用户的 AI 快讯"]
    assert "{audience" not in developer.script_style and "不写代码" not in developer.script_style  # the developer cell adds nothing
    assert plain.default_depth == "intro" and developer.default_depth == "intermediate"


def test_a_slot_on_its_own_line_vanishes_when_the_audience_has_nothing_to_add():
    audience = {"label": "开发者", "impact": "要改什么"}
    text = "先说{audience}。\n{audience_guidance}\n最后。\n{audience_research}"
    assert matrix.fill_slots(text, audience, "x") == "先说开发者。\n最后。"
    assert matrix.fill_slots("a\n{audience_guidance}\nb", {"guidance": "一\n二"}, "x") == "a\n一\n二\nb"
    with pytest.raises(video_columns.ColumnError, match="不认识的受众插槽"):
        matrix.fill_slots("{audience_unknown}", audience, "x")


def test_the_video_form_is_the_types_and_the_format_only_restricts_it(tmp_path):
    data = _shipped()
    assert video_columns.get(str(ROOT / "config"), "deep_dive").allowed_forms == ("auto", "information_motion", "process_diagram")
    assert video_columns.get(str(ROOT / "config"), "flash").allowed_forms == ("information_motion",)
    data["formats"]["long"]["forms"] = ["information_motion", "auto"]  # the format drops process_diagram
    loaded = video_columns.load(_write(tmp_path, data))
    assert loaded["deep_dive"].allowed_forms == ("auto", "information_motion")  # in the type's order
    with pytest.raises(video_columns.ColumnError, match="默认形式 auto"):
        data["formats"]["long"]["forms"] = ["process_diagram"]
        video_columns.load(_write(tmp_path, data))
    data["formats"]["long"].pop("forms")
    data["types"]["deep_dive"].pop("allowed_forms")
    with pytest.raises(video_columns.ColumnError, match="没写 allowed_forms"):
        video_columns.load(_write(tmp_path, data))


def test_what_autovideo_is_told_about_the_video_comes_from_the_cell():
    config = str(ROOT / "config")
    assert video_columns.get(config, "flash").creative_intent == {"purpose": "知识讲解"}  # as it always was
    hint = video_columns.get(config, "ai-non_tech-quick_take-short").creative_intent
    assert hint["purpose"] == "知识讲解" and "不写代码" in hint["audience"]


def test_the_shipped_roundup_and_comparison_cells_are_experimental_and_carry_their_topic_kind():
    cells = matrix.load(str(ROOT / "config"))
    roundup, comparison = cells["ai-developer-roundup-long"], cells["ai-developer-comparison-long"]
    assert roundup.status == comparison.status == "experimental"
    assert (roundup.column.topic_kind, comparison.column.topic_kind) == ("roundup", "comparison")
    # a roundup is written from a discovery report, so it has no research prompt; a comparison researches
    assert roundup.column.research_prompt == "" and "{topic_subjects}" in comparison.column.research_prompt
    assert all(cell.column.topic_kind == "single" for cid, cell in cells.items() if cid.split("-")[2] in
               ("deep_dive", "quick_take", "one_point"))
    # no audience slot is left unfilled
    for cell in (roundup, comparison):
        assert "{audience" not in cell.column.script_style and "{audience" not in cell.column.description


def test_a_column_needs_a_research_prompt_unless_it_is_a_roundup():
    base = {"name": "x", "target_minutes": 3}
    with pytest.raises(video_columns.ColumnError, match="research_prompt"):
        video_columns.build("a", {**base, "topic_kind": "comparison"})
    assert video_columns.build("b", {**base, "topic_kind": "roundup"}).topic_kind == "roundup"
    with pytest.raises(video_columns.ColumnError, match="topic_kind"):
        video_columns.build("c", {**base, "topic_kind": "listicle", "research_prompt": "x"})


def test_the_catalog_lists_every_cell_with_its_status_and_why_a_blocked_one_cannot_be_used(tmp_path):
    rows = matrix.catalog(str(ROOT / "config"))
    assert [r["status"] for r in rows] == sorted((r["status"] for r in rows), key=["enabled", "experimental", "blocked"].index)
    flash = next(r for r in rows if r["id"] == "ai-developer-quick_take-short")
    assert flash["key"] == "flash" and flash["usable"] and flash["label"] == "快讯 · 开发者 · B 站横屏短"
    assert flash["parts"]["audience"] == {"key": "developer", "version": 1, "name": "开发者"}
    assert flash["column"]["topic_kind"] == "single" and flash["column"]["allowed_forms"] == ["information_motion"]

    data = _shipped()
    data["cells"].append({"domain": "ai", "audience": "developer", "type": "quick_take", "format": "long",
                          "status": "blocked", "reason": "竖屏需要新的画面版本"})
    data["types"]["quick_take"]["formats"]["long"] = {"target_minutes": 2}
    blocked = matrix.catalog(_write(tmp_path, data))[-1]
    assert blocked["status"] == "blocked" and not blocked["usable"] and blocked["key"] is None
    assert blocked["reason"] == "竖屏需要新的画面版本" and blocked["column"] is None


def test_a_flat_column_is_listed_as_an_enabled_cell_without_parts(tmp_path):
    (tmp_path / "video_columns.yaml").write_text(
        "mine:\n  name: 我的栏目\n  research_prompt: x\n  target_minutes: 3\n", encoding="utf-8")
    assert matrix.catalog(str(tmp_path)) == [{
        "id": "mine", "key": "mine", "status": "enabled", "reason": "", "usable": True, "label": "我的栏目",
        "parts": None, "column": video_columns.load(str(tmp_path))["mine"].as_dict()}]

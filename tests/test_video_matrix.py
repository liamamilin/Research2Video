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
    assert {c.column.key: c.status for c in cells.values()} == {"deep_dive": "enabled", "flash": "enabled", "point_short": "enabled"}
    assert "ai-developer-quick_take-short" in cells
    assert cells["ai-developer-quick_take-short"].parts["audience"] == {"key": "developer", "version": 1}


def test_a_cell_is_found_by_its_id_and_by_its_column_key():
    config = str(ROOT / "config")
    assert video_columns.get(config, "ai-developer-quick_take-short") == video_columns.get(config, "flash")
    assert sorted(video_columns.load(config)) == ["deep_dive", "flash", "point_short"]  # one entry per cell
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
    assert sorted(loaded) == ["deep_dive", "flash"]
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
    assert sorted(video_columns.load(config)) == ["custom", "deep_dive", "flash", "point_short"]


def test_composition_equals_the_flat_settings_the_columns_used_to_have():
    """The shipped cells, dumped as a flat file and loaded the old way, are the same columns."""
    from tests import golden_video_cells as golden
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "video_columns.yaml").write_text(golden.shipped_flat_yaml(), encoding="utf-8")
        flat = video_columns.load(tmp)
    shipped = video_columns.load(str(ROOT / "config"))
    assert {k: dataclasses.asdict(c) for k, c in flat.items()} == {k: dataclasses.asdict(c) for k, c in shipped.items()}

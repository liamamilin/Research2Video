"""The shipped columns still resolve exactly as the golden snapshot says (spec/video-matrix.md, phase 0)."""

from __future__ import annotations

import json

import pytest

from tests import golden_video_cells as golden


def test_the_snapshot_covers_the_three_shipped_columns():
    assert sorted(json.loads(golden.GOLDEN.read_text(encoding="utf-8"))) == ["deep_dive", "flash", "point_short"]


@pytest.mark.parametrize("key", ["deep_dive", "flash", "point_short"])
def test_a_shipped_column_resolves_as_it_did(key):
    want = json.loads(golden.GOLDEN.read_text(encoding="utf-8"))[key]
    got = golden.snapshot()[key]
    for part in want:
        assert got[part] == want[part], f"{key}: {part} changed; regenerate the snapshot only if the change is deliberate"

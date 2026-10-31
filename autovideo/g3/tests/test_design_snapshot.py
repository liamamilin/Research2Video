"""Historical package validation excludes runtime files while retaining hash enforcement."""
from pathlib import Path
import shutil

import pytest

from g0.design_snapshot import ROOT, build_snapshot


def test_design_snapshot_rejects_changes_to_immutable_contracts(tmp_path):
    source = tmp_path / "source"
    count = build_snapshot(source)
    assert len(list(source.rglob("*.*"))) == count + 1
    assert not (source / "g3/data").exists()
    shutil.copytree(ROOT / "g0/design_baseline", source / "g0/design_baseline")
    contract = source / "schemas/video-ir-v0.2.schema.json"
    contract.write_bytes(contract.read_bytes() + b" ")
    with pytest.raises(ValueError, match="schemas/video-ir-v0.2.schema.json"):
        build_snapshot(tmp_path / "changed", root=source)

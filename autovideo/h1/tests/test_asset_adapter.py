import hashlib
import json
from pathlib import Path

import pytest

from h1.asset_adapter import AssetFault, resolve_scene_backdrops


PNG = b"\x89PNG\r\n\x1a\n" + b"test-pixels"


def _setup(tmp_path: Path, asset_path: str = "h1/assets/plate.png"):
    project = tmp_path / "project"
    asset = project / asset_path
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_bytes(PNG)
    manifest = project / "h1/asset-manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"assets": [{
        "asset_id": "plate", "path": asset_path,
        "sha256": hashlib.sha256(PNG).hexdigest(),
    }]}), encoding="utf-8")
    ir = {"scenes": [{"id": "opening"}]}
    return project, manifest, ir


def test_resolves_manifest_asset_to_cached_static_path(tmp_path):
    project, manifest, ir = _setup(tmp_path)
    result = resolve_scene_backdrops(
        ir, {"opening": "plate"}, manifest, project,
        project / "h1/cache", project / "h1/public/assets",
    )
    assert result == {"opening": "assets/plate.png"}
    cached = next((project / "h1/cache").rglob("*.png"))
    assert hashlib.sha256(cached.read_bytes()).hexdigest() == hashlib.sha256(PNG).hexdigest()


def test_rejects_path_traversal_even_when_manifest_lists_it(tmp_path):
    project, manifest, ir = _setup(tmp_path, "../outside.png")
    with pytest.raises((AssetFault, FileNotFoundError)):
        resolve_scene_backdrops(
            ir, {"opening": "plate"}, manifest, project,
            project / "h1/cache", project / "h1/public/assets",
        )


def test_rejects_digest_mismatch(tmp_path):
    project, manifest, ir = _setup(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["assets"][0]["sha256"] = "0" * 64
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(AssetFault, match="digest mismatch"):
        resolve_scene_backdrops(
            ir, {"opening": "plate"}, manifest, project,
            project / "h1/cache", project / "h1/public/assets",
        )


def test_rejects_unknown_scene_or_asset(tmp_path):
    project, manifest, ir = _setup(tmp_path)
    with pytest.raises(AssetFault, match="existing scenes"):
        resolve_scene_backdrops(
            ir, {"missing": "plate"}, manifest, project,
            project / "h1/cache", project / "h1/public/assets",
        )
    with pytest.raises(AssetFault, match="not in the manifest"):
        resolve_scene_backdrops(
            ir, {"opening": "other"}, manifest, project,
            project / "h1/cache", project / "h1/public/assets",
        )

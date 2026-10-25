"""Local, content-addressed image asset adapter for the H1 experiment."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path


class AssetFault(ValueError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_scene_backdrops(
    ir: dict,
    selection: dict[str, str],
    manifest_path: Path,
    project_root: Path,
    cache_root: Path,
    public_assets: Path,
) -> dict[str, str]:
    """Validate selected image assets and return renderer-relative static paths.

    This adapter has no network access and accepts only manifest-listed PNGs
    whose bytes match their recorded digest. Scene-to-asset mapping is a trusted
    H1 sidecar, never LLM-authored IR.
    """
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = manifest.get("assets")
    if not isinstance(rows, list):
        raise AssetFault("asset manifest must contain an assets list")
    assets = {row.get("asset_id"): row for row in rows if isinstance(row, dict)}
    if len(assets) != len(rows):
        raise AssetFault("asset IDs must be present and unique")

    scene_ids = {scene.get("id") for scene in ir.get("scenes", []) if isinstance(scene, dict)}
    if not selection or not set(selection).issubset(scene_ids):
        raise AssetFault("selection must reference existing scenes")

    resolved: dict[str, str] = {}
    project_root = project_root.resolve()
    for scene_id, asset_id in selection.items():
        if not isinstance(asset_id, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", asset_id):
            raise AssetFault(f"invalid asset ID for scene {scene_id}")
        row = assets.get(asset_id)
        if row is None:
            raise AssetFault(f"asset is not in the manifest: {asset_id}")
        relative = Path(row.get("path", ""))
        if relative.is_absolute() or relative.suffix.lower() != ".png":
            raise AssetFault(f"asset path must be a relative PNG: {asset_id}")
        source = (project_root / relative).resolve(strict=True)
        if not source.is_relative_to(project_root) or not source.is_file():
            raise AssetFault(f"asset path escapes the project or is not a file: {asset_id}")
        expected = row.get("sha256")
        actual = sha256_file(source)
        if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected) or actual != expected:
            raise AssetFault(f"asset digest mismatch: {asset_id}")
        with source.open("rb") as stream:
            if stream.read(8) != b"\x89PNG\r\n\x1a\n":
                raise AssetFault(f"asset is not a PNG image: {asset_id}")

        cached = cache_root / expected[:2] / f"{expected}.png"
        cached.parent.mkdir(parents=True, exist_ok=True)
        if cached.exists() and sha256_file(cached) != expected:
            raise AssetFault(f"content-addressed cache entry is corrupt: {asset_id}")
        if not cached.exists():
            shutil.copy2(source, cached)

        public_assets.mkdir(parents=True, exist_ok=True)
        public_file = public_assets / f"{asset_id}.png"
        if public_file.exists() and sha256_file(public_file) != expected:
            raise AssetFault(f"renderer asset path already contains different bytes: {asset_id}")
        if not public_file.exists():
            shutil.copy2(cached, public_file)
        resolved[scene_id] = f"assets/{asset_id}.png"
    return resolved

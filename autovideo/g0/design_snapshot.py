"""Reconstruct the frozen design package without including runtime data or dependencies."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
# These operational documents continue to evolve; their original bytes remain
# available for the historical package's integrity checks.
HISTORICAL_DOCS = {"AGENTS.md", "README.md", "RELEASE_STATUS.md"}


def build_snapshot(destination: Path, root: Path = ROOT) -> int:
    manifest = json.loads((root / "PACKAGE_MANIFEST.json").read_text())
    for item in manifest["files"]:
        relative = item["path"]
        source = (root / "g0/design_baseline" / (relative + ".snapshot")
                  if relative in HISTORICAL_DOCS else root / relative)
        data = source.read_bytes()
        if len(data) != item["bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError(f"Frozen design package changed: {relative}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    shutil.copy2(root / "PACKAGE_MANIFEST.json", destination / "PACKAGE_MANIFEST.json")
    return len(manifest["files"])

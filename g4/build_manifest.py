"""Build a deterministic SHA-256 manifest for G4 deliverables and changed code."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "g4/G4_MANIFEST.json"
EXPLICIT_FILES = [
    ".env.example",
    "G4_ACCEPTANCE.md",
    "g1/video_ir.py",
    "g1/tests/test_video_ir_v0_2_runtime.py",
    "g2/run_g2.py",
    "g2/README.md",
    "g2/tests/test_g2.py",
    "g3/config.py",
    "g3/worker.py",
    "g3/README.md",
    "g3/tests/test_g3.py",
    "g3/web/src/main.tsx",
    "g3/web/static/app.js",
    "g3/web/static/app.css",
]


def included_files() -> list[Path]:
    paths = {ROOT / rel for rel in EXPLICIT_FILES}
    for path in (ROOT / "g4").rglob("*"):
        if not path.is_file() or path == OUTPUT or path.name == "build_manifest.py":
            continue
        if any(part in {"__pycache__", ".pytest_cache"} for part in path.parts):
            continue
        if path.suffix in {".pyc", ".pyo"}:
            continue
        paths.add(path)
    missing = sorted(str(path.relative_to(ROOT)) for path in paths if not path.is_file())
    if missing:
        raise SystemExit(f"manifest input is missing: {', '.join(missing)}")
    return sorted(paths, key=lambda item: item.relative_to(ROOT).as_posix())


def main() -> None:
    entries = []
    for path in included_files():
        data = path.read_bytes()
        entries.append({
            "path": path.relative_to(ROOT).as_posix(),
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    payload = {
        "manifest_version": 1,
        "scope": "G4 source changes, reports, Golden Case evidence, generated media, logs, and QC",
        "files": entries,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)} ({len(entries)} files)")


if __name__ == "__main__":
    main()

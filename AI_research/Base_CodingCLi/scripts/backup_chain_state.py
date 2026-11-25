"""Back up configuration and live ledgers for a chain rollout; media stays in place."""

from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def snapshot(config: Path, jobs: Path, state: Path, destination: Path) -> dict:
    if destination.exists():
        raise ValueError("使用新的备份目录，保留已有备份")
    destination.mkdir(parents=True)
    receipt = {
        "created_at": time.time(),
        "files": [],
        "databases": [],
        "media": "existing paths retained; no media files moved",
        "status": "incomplete",
    }
    databases = (
        "video.db",
        "chains.db",
        "schedules.db",
        "users.db",
        "reports.db",
        "platforms.db",
        "douyin.db",
        "production_admission.db",
        "materials.db",
        "events.db",
        "assistant.db",
        "themes.db",
        "theme_library.db",
        "experiments.db",
        "tracking.db",
        "telemetry.db",
    )
    candidates = [state / name for name in databases] + list(
        (state / "platforms").glob("*/queue.db")
    )
    for source in candidates:
        if not source.is_file():
            continue
        target = destination / "state" / source.relative_to(state)
        target.parent.mkdir(parents=True, exist_ok=True)
        with closing(
            sqlite3.connect(
                source.resolve().as_uri() + "?mode=ro", uri=True, timeout=30
            )
        ) as live:
            with closing(sqlite3.connect(target)) as copied:
                live.backup(copied)
                copied.execute("PRAGMA journal_mode=DELETE")
                assert copied.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                counts = {
                    name: copied.execute(
                        'SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"'
                    ).fetchone()[0]
                    for (name,) in copied.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    )
                }
        receipt["databases"].append(
            {"path": str(source), "backup": str(target), "rows": counts}
        )
    for label, root, patterns in [
        ("config", config, ["*.yaml", "*.yml"]),
        ("jobs", jobs, ["*.yaml", "*.yml"]),
        (
            "state",
            state,
            ["config_changes/*.json", "report_meta.jsonl", "pipeline_rounds.json"],
        ),
    ]:
        for pattern in patterns:
            paths = (
                root.rglob(pattern)
                if "/" not in pattern and pattern.startswith("*")
                else root.glob(pattern)
            )
            for source in paths:
                target = destination / label / source.relative_to(root)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                receipt["files"].append(
                    {
                        "path": str(source),
                        "backup": str(target),
                        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                    }
                )
    receipt["status"] = "complete"
    (destination / "manifest.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2)
    )
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    from web.settings import get_settings

    paths = get_settings().paths
    result = snapshot(
        Path(paths.config_dir),
        Path(paths.jobs_dir),
        Path(paths.state_dir),
        args.destination,
    )
    print(
        "Complete:",
        len(result["databases"]),
        "databases,",
        len(result["files"]),
        "configuration/receipt files",
    )


if __name__ == "__main__":
    main()

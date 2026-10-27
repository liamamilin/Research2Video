"""Replay automatic video deduplication against a read-only ledger; never archive or render.

Usage: python scripts/eval_video_dedup.py --database state/video.db --script ID --output /tmp/replay.json
The same script can be supplied more than once. No model, platform or network call is made.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import sqlite_util
from core.video import content_guard as guard, store


@contextmanager
def readonly_ledger(database: Path):
    """Also cover topic_store's imported connection helper; an attempted write fails in SQLite."""
    previous = store.db_path()
    store.set_db_path(str(database.resolve()))

    def open_readonly(path, **kwargs):
        conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn

    try:
        with patch.object(sqlite_util, "open_db", open_readonly):
            yield
    finally:
        store.set_db_path(previous)


def replay(database: Path, script_ids: list[str], config_dir: str, jobs_dir: str) -> list[dict]:
    result = []
    with readonly_ledger(database):
        for script_id in script_ids:
            script = store.get_script(script_id)
            if script is None:
                raise ValueError(f"Unknown script: {script_id}")
            guarded = guard.with_policy(script, jobs_dir)
            version = store.get_version(script_id, script["current_version"]) if script["current_version"] else None
            item = {"script_id": script_id, "title": version["title"] if version else script["topic"].get("title", ""),
                    "policy": guarded["topic"].get("matrix_dedup"), "result": "NO_VERSION", "peers": [],
                    "would_duplicate": False, "protected": guard.protected(script_id)}
            if version:
                used = guard.cited_sources(version["sections"], lead=True) or guard.cited_sources(version["sections"])
                peers = guard.duplicate_peers(used, guard.coverage(guarded["topic"], config_dir, jobs_dir, script_id=script_id))
                item.update(would_duplicate=bool(peers), peers=list(dict.fromkeys(p["script_id"] for p in peers)),
                            result="PROTECTED" if item["protected"] else "DUPLICATE" if peers else "ALLOW")
            result.append(item)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--script", action="append", required=True)
    parser.add_argument("--config-dir", default=str(ROOT / "config"))
    parser.add_argument("--jobs-dir", default=str(ROOT / "jobs"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = replay(args.database, args.script, args.config_dir, args.jobs_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"readonly": True, "scripts": result}, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"readonly": True, "results": [{"script_id": r["script_id"], "result": r["result"],
                                                     "would_duplicate": r["would_duplicate"]}
                                                    for r in result]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

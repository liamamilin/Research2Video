"""Replay report event selection in a temporary ledger; production is opened read-only.

--live uses the configured writing model once per distinct report/item. --events-json
uses saved organizer replies instead. Neither mode writes a draft, renders or publishes.
Example: python scripts/replay_video_story_selection.py --database state/video.db \
  --script SCRIPT_ID --live --output state/story-replay.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import report_material, report_meta
from core.video import content_guard as guard, store, story_pool
from scripts.eval_video_dedup import readonly_ledger
from web.services import video_workflow as workflow


def inputs(database: Path, ids: list[str], config_dir: str, jobs_dir: str) -> list[dict]:
    groups = {}
    with readonly_ledger(database):
        remembered = guard.history(config_dir, jobs_dir)
        with store.connect() as conn:
            order = {r["id"]: r["sequence"] for r in conn.execute("SELECT id,rowid AS sequence FROM scripts")}
        for script_id in ids:
            original = store.get_script(script_id)
            if not original:
                raise ValueError(f"Unknown script: {script_id}")
            script = guard.with_policy(original, jobs_dir)
            if not script["topic"].get("matrix_dedup") and not guard.is_sample(script):
                script = {**script, "topic": guard.new_topic(script["topic"], script.get("content_context") or {},
                                                             config_dir, jobs_dir)}
            job = (script["topic"].get("matrix_dedup") or {}).get("job", "")
            scope = guard.scope(job, config_dir)
            report_path = script["report_path"] or script["topic"]["from_report"]
            text = workflow.report_of(script, report_path)
            reference = (report_meta.get(report_path) or {}).get("material") or {}
            full = Path(workflow.workspace_dir()) / report_path
            material = report_material.read(reference, str(full), full.read_text()) if reference else ""
            if script["topic"].get("from_item") and material:
                selected = guard.sources(text)
                material = "\n\n---\n\n".join(p["text"] for p in story_pool.packets(text, material)
                                                         if guard.sources(p["text"]) & selected)
            key = story_pool._hash(scope, text, material)
            group = groups.setdefault(key, {"key": key, "scope": scope, "report": text, "material": material,
                                           "scripts": [], "before": order[script_id]})
            group["scripts"].append({"original_id": script_id, "column": script["column_key"], "job": job,
                                     "sequence": order[script_id]})
            group["before"] = min(group["before"], order[script_id])
        for group in groups.values():
            group["scripts"].sort(key=lambda s: s["sequence"])
            group["peers"] = [h for h in remembered if h["scope"] == group["scope"] and h["sequence"] < group["before"]]
    return list(groups.values())


def replay(groups: list[dict], config=None, saved=None) -> dict:
    original = store.db_path()
    result = []
    try:
        with TemporaryDirectory(prefix="video-story-replay-") as sandbox:
            store.set_db_path(str(Path(sandbox) / "video.db"))
            store.init_db()
            for group in groups:
                def organize(report, packets, previous, llm_config):
                    return story_pool.validate(saved[group["key"]], packets, previous), {}

                # Only the temp ledger receives pools, claims and decision rows.
                with patch.object(story_pool, "organize", organize) if saved is not None else _no_patch():
                    pool = story_pool.load_or_create(group["scope"], group["report"], group["material"], group["peers"], config)
                allocations = []
                for case in group["scripts"]:
                    script = store.create_script(case["column"], {"title": "选题回放",
                                                 "matrix_dedup": {"job": case["job"], "policy": story_pool.POLICY}}, "autopilot")
                    try:
                        story_pool.take(script["id"], pool, group["peers"])
                    except story_pool.NoCandidate:
                        pass
                    allocations.append({"original_id": case["original_id"], **story_pool.selection(script["id"])})
                result.append({"key": group["key"], "scope": group["scope"], "history_count": len(group["peers"]),
                               "usage": pool["usage"], "events": pool["events"], "allocations": allocations})
    finally:
        store.set_db_path(original)
    return {"production_readonly": True, "drafts_written": 0, "groups": result}


def _no_patch():
    from contextlib import nullcontext

    return nullcontext()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--script", action="append", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--events-json", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    groups = inputs(args.database, args.script, str(ROOT / "config"), str(ROOT / "jobs"))
    saved = json.loads(args.events_json.read_text()) if args.events_json else None
    result = replay(groups, workflow._llm_configs(None)[0] if args.live else None, saved)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"production_readonly": True, "drafts_written": 0,
                      "groups": [{"scope": g["scope"], "candidates": len(g["events"]),
                                  "allocations": [{"selected": a["selected"], "skipped": a["skipped"]} for a in g["allocations"]]}
                                 for g in result["groups"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""Review or reversibly archive unpublished duplicate main stories; never delete media or publications.

Run without --apply to review the plan. --apply also saves a SQLite backup and an audit receipt under --output.
Platform upload receipts, including uncertain submissions, protect a script even before its publication is recorded.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.video import autopilot_store, content_guard as guard, outputs, store


def upload_protected(state: Path) -> set[str]:
    """Read every account's outbox without contacting or logging into a platform."""
    return guard.upload_protected(state)


def build_plan(config_dir: str, jobs_dir: str, state_dir: str) -> dict:
    scripts = store.list_scripts(limit=100000) + store.list_scripts(limit=100000, archived=True)
    owners, _ = outputs.script_owners(config_dir, jobs_dir, scripts)
    published, protected = store.published_script_ids(), upload_protected(Path(state_dir))
    versions = store.current_versions([s["id"] for s in scripts])
    renders = store.render_statuses_by_script()
    with store.connect() as conn:
        sequence = {r["id"]: r["sequence"] for r in conn.execute("SELECT id,rowid AS sequence FROM scripts")}
    rows = []
    for script in scripts:
        if script.get("archived") and script["id"] not in published:
            continue
        version = versions.get(script["id"])
        owner = owners[script["id"]]
        rows.append({"script_id": script["id"], "title": version["title"] if version else script["topic"].get("title", ""),
                     "scope": f"matrix:{owner['matrix']}" if owner["matrix"] else f"job:{owner['job']}" if owner["job"] else "",
                     "report": script["report_path"] or script["topic"].get("from_report", ""),
                     "recipe": script["column_key"], "created_at": script["created_at"], "status": script["status"],
                     "sequence": sequence[script["id"]],
                     "published": script["id"] in published, "protected": script["id"] in protected,
                     "sample": guard.is_sample(script),
                     "rendered": any(r["status"] == "COMPLETED" for r in renders.get(script["id"], [])),
                     "sources": guard.cited_sources(version["sections"], lead=True) if version else set(),
                     "covered_sources": guard.cited_sources(version["sections"]) if version else set()})
    # Prefer a published work, then an already completed video, then the earliest draft.
    rows.sort(key=lambda r: (not r["published"], not r["protected"], not r["rendered"], r["sequence"]))
    keep, archive = [], []
    for row in rows:
        reason, peers = "", []
        if not row["published"] and not row["protected"]:
            if row["sample"]:
                reason = "验收样片与正式内容隔离"
            elif row["status"] in ("DRAFT", "APPROVED") and row["scope"]:
                peers = [p for p in keep if p["scope"] == row["scope"] and guard.overlap(row["sources"], p["covered_sources"])]
                covered = set().union(*(p["covered_sources"] for p in peers)) if peers else set()
                if not row["sources"] or len(guard.overlap(row["sources"], covered)) / len(row["sources"]) < guard.MAIN_STORY_SHARE:
                    peers = []
                if peers:
                    reason = "同矩阵重复主选题；保留：" + "、".join(p["title"] for p in peers)
        if reason:
            archive.append({**row, "reason": reason, "duplicate_of": [p["script_id"] for p in peers]})
        else:
            keep.append(row)
    serializable = lambda row: {**row, "sources": sorted(row["sources"]), "covered_sources": sorted(row["covered_sources"])}  # noqa: E731
    return {"created_at": datetime.now().astimezone().isoformat(),
            "archive": [serializable(r) for r in archive], "keep": [serializable(r) for r in keep]}


def published_digest() -> str:
    """Include published scripts, their versions and renders, not only the links."""
    with store.connect() as conn:
        values = {}
        for table in ("publications", "scripts", "script_versions", "renders"):
            condition = "" if table == "publications" else (
                " WHERE id IN (SELECT script_id FROM publications)" if table == "scripts" else
                " WHERE script_id IN (SELECT script_id FROM publications)")
            values[table] = [dict(r) for r in conn.execute(f"SELECT * FROM {table}{condition} ORDER BY rowid")]
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def apply_plan(plan: dict, state_dir: str, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    before = published_digest()
    with sqlite3.connect(f"file:{store.db_path()}?mode=ro", uri=True) as source:
        with sqlite3.connect(str(output / "video-before.db")) as dest:
            source.backup(dest)
    changed, skipped = [], []
    for row in plan["archive"]:
        current = store.get_script(row["script_id"])
        if (current is None or current.get("archived") or current["status"] in ("WRITING", "RESEARCHING")
                or row["script_id"] in upload_protected(Path(state_dir))):
            skipped.append(row["script_id"])
            continue
        if guard.archive_duplicate(row["script_id"], row["reason"], row["duplicate_of"]):
            autopilot_store.cancel_open_items(row["script_id"], row["reason"])
            changed.append(row)
        else:
            skipped.append(row["script_id"])
    after = published_digest()
    result = {"archived": changed, "skipped": skipped, "published_before_sha256": before,
              "published_after_sha256": after, "published_unchanged": before == after}
    (output / "receipt.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    if before != after:
        raise RuntimeError("Published records changed during maintenance; inspect the saved backup and receipt")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    store.use_state_dir(str(ROOT / "state"))
    plan = build_plan(str(ROOT / "config"), str(ROOT / "jobs"), str(ROOT / "state"))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    result = apply_plan(plan, str(ROOT / "state"), args.output) if args.apply else plan
    print(json.dumps({"apply": args.apply, "count": len(result.get("archived", result.get("archive", []))),
                      "titles": [r["title"] for r in result.get("archived", result.get("archive", []))],
                      "published_unchanged": result.get("published_unchanged")}, ensure_ascii=False))


if __name__ == "__main__":
    main()

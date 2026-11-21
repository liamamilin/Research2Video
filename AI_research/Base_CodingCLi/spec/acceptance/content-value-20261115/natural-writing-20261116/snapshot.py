"""Read-only inventory and evidence capture for the natural-writing task.

Run in Base_CodingCLi. Production databases are opened read-only. This records
inputs and current recipes; it does not run providers or claim runtime coverage.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent
EVIDENCE = ROOT / "state/acceptance-content-value-20261115/natural-writing-20261116/baseline"


def read(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) or {}


def save(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    refs: dict[str, list] = defaultdict(list)
    active = {}
    archived = set()
    for path in sorted((ROOT / "config/matrices").glob("*.yaml")):
        value = read(path)
        names = [x.get("job") if isinstance(x, dict) else x for x in value.get("jobs", [])]
        if value.get("archived"):
            archived.update(filter(None, names))
            continue
        active[path.stem] = value
        for name in filter(None, names):
            refs[name].append({"kind": "current_matrix", "matrix": path.stem,
                               "paused": name in value.get("paused", [])})
    with sqlite3.connect(f"file:{ROOT / 'state/schedules.db'}?mode=ro", uri=True) as db:
        for (name,) in db.execute("SELECT job_name FROM schedules WHERE enabled=1 AND deleted=0"):
            if name not in archived and not name.startswith("__"):
                refs[name].append({"kind": "enabled_schedule"})
    # These jobs are still exposed by the current matrix workbench. The YAML
    # often just dispatches to a Python author; audit the factory as well.
    for folder in ("source_discovery", "topic_discovery", "topic_platform", "topic_ideas"):
        for path in sorted((ROOT / "jobs" / folder).glob("*.yaml")):
            value = read(path)
            matrix = value.get("source_discovery") or value.get("topic_discovery") or value.get("topic_ideas")
            if matrix in active:
                refs[f"{folder}/{path.stem}"].append({
                    "kind": "current_matrix_helper", "matrix": matrix,
                    "runtime_note": "Inspect dispatch and live factory; a historical request ID is not a reusable request.",
                })
    configs = {p.stem: read(p) for p in sorted((ROOT / "config/production").glob("*.yaml"))}
    columns = {p.stem: read(p) for p in sorted((ROOT / "config/content_columns").glob("*.yaml"))}
    scripts = defaultdict(list)
    with sqlite3.connect(f"file:{ROOT / 'state/video.db'}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        query = ("SELECT s.*,v.title,v.sections,v.meta,v.created_at AS version_created_at "
                 "FROM scripts s JOIN script_versions v ON v.script_id=s.id AND v.version=s.current_version "
                 "WHERE s.archived=0 ORDER BY s.updated_at DESC")
        for raw in db.execute(query):
            row = dict(raw)
            for field in ("topic", "content_context", "sections", "meta"):
                row[field] = json.loads(row[field] or ("[]" if field == "sections" else "{}"))
            job = (row["content_context"].get("job") or configs.get(row["production"], {}).get("job")
                   or (row["topic"].get("matrix_dedup") or {}).get("job") or row["topic"].get("knowledge_job"))
            if job in refs:
                scripts[job].append(row)
    from core.video import manuscript_workflow

    manuscript_jobs = set(read(ROOT / "config/manuscript.yaml").get("jobs", []))
    # Only inspect the editorial settings, never serialize provider credentials.
    video = read(ROOT / "config/system.yaml").get("video") or {}
    if "manuscript_jobs" in video:
        manuscript_jobs = set(video["manuscript_jobs"] or [])
    result = []
    for name in sorted(refs):
        if name.startswith("archive/") or name in archived:
            continue
        path = ROOT / "jobs" / f"{name}.yaml"
        if not path.exists():
            result.append({"job": name, "scope_evidence": refs[name], "status": "missing_job_file"})
            continue
        job = read(path)
        slug = name.replace("/", "__")
        bindings = {key: value for key, value in configs.items() if value.get("job") == name}
        contexts = {key: value for key, value in columns.items()
                    if set(value.get("production_ids", [])) & bindings.keys()}
        profile = manuscript_workflow.JOB_PROMPTS.get(name)
        from dataclasses import asdict

        config_bundle = {"job": name, "yaml": job, "production": bindings, "columns": contexts,
                         "manuscript_enabled": name in manuscript_jobs,
                         "profile": asdict(profile) if profile else None,
                         "note": "A defined profile is not necessarily enabled; production_id can further narrow it."}
        save(EVIDENCE / "inputs" / f"{slug}.json", config_bundle)
        # Preserve latest per production route, not only one route per job.
        selected = []
        by_binding = defaultdict(int)
        for script in scripts[name]:
            binding = script["production"] or script["column_key"]
            if by_binding[binding] >= 2:
                continue
            by_binding[binding] += 1
            selected.append(script["id"])
            save(EVIDENCE / "scripts" / f"{script['id']}.json", script)
        result.append({"job": name, "name": job.get("name"), "scope_evidence": refs[name],
                       "job_enabled": job.get("enabled", True),
                       "source_collection_only": bool(job.get("source_collection")),
                       "job_path": str(path.relative_to(ROOT)),
                       "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                       "production_ids": list(bindings), "content_columns": list(contexts),
                       "manuscript_enabled": name in manuscript_jobs,
                       "saved_script_count": len(scripts[name]), "baseline_script_ids": selected,
                       "input_snapshot": str((EVIDENCE / 'inputs' / f'{slug}.json').relative_to(ROOT)),
                       "analysis_status": "pending", "runtime_usage_verification": "pending"})
    save(HERE / "inventory.json", {"date": "2026-11-16", "scope": "All jobs in current unarchived matrices, regardless of schedules; active matrix helper entry points; enabled external schedules. Archived excluded.",
                                  "status": "configuration inventory; runtime reachability and prompt analysis remain separate",
                                  "candidate_count": len(result), "active_matrices": list(active), "jobs": result})
    print(json.dumps({"jobs": len(result), "matrix_jobs": sum(any(e['kind']=='current_matrix' for e in r['scope_evidence']) for r in result),
                      "with_saved_scripts": sum(bool(r.get('baseline_script_ids')) for r in result),
                      "saved_scripts": sum(len(r.get('baseline_script_ids',[])) for r in result)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

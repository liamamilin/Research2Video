"""The information sources page: the registry (config/sources.yaml), which Jobs use each source, and how its last fetch went.

Nothing is stored for this. Which Jobs use a source comes from their ``sources`` (ids and tags resolved); how the last
fetch went comes from the runs' history (``usage.fetched`` records, per spec, the item count or why it failed), so a
source that has gone quiet shows up here without anyone adding bookkeeping.
"""

from __future__ import annotations

from core import sources
from core.config import list_jobs, load_job
from web.services.automation_health import _history

#: How many recent runs of a Job are read for a source's history.
RUNS_READ = 30


def _fetched(entry: dict) -> dict:
    return (entry.get("usage") or {}).get("fetched") or {}


def catalog(config_dir: str, jobs_dir: str, state_dir: str, *, job_names: list[str] | None = None) -> dict:
    """``{sources: [...], problems: [...]}``; ``problems`` are Jobs whose source names the registry does not have."""
    registry = sources.load_registry(config_dir)
    by_spec: dict[str, list[dict]] = {}
    for key, entry in registry.items():
        by_spec.setdefault(entry['spec'], []).append({"id": key, **entry, "used_by": [], "last": None})
    problems: list[dict] = []
    for name in list_jobs(jobs_dir) if job_names is None else job_names:
        job = load_job(jobs_dir, name) or {}
        declared = list(job.get("sources") or [])
        if not declared:
            continue
        try:
            specs = sources.resolve(declared, config_dir, registry=registry)
        except sources.RegistryError as exc:
            problems.append({"job": name, "problem": str(exc)})
            continue
        history = _history(state_dir, name) + (_history(state_dir, name.rsplit("/", 1)[-1]) if "/" in name else [])
        history = sorted(history, key=lambda e: e.get("ts") or "")[-RUNS_READ:]
        for spec in specs:
            candidates = by_spec.setdefault(spec, [{"id": "", "name": spec, "spec": spec, "tags": [], "note": "", "used_by": [], "last": None}])
            for row in candidates:
                references = {str(s).strip() for s in declared}
                if row['id'] and not (spec in references or 'source:' + row['id'] in references or
                                      any('tag:' + tag in references for tag in row['tags'])):
                    continue
                row["used_by"].append({"job": name, "name": job.get("name") or name})
                for entry in reversed(history):
                    state = _fetched(entry).get(spec)
                    if state is None:
                        continue
                    seen = {"at": entry.get("ts"), "job": name, "items": state if isinstance(state, int) else 0,
                            "error": state if isinstance(state, str) else ""}
                    if row["last"] is None or (seen["at"] or "") > (row["last"]["at"] or ""):
                        row["last"] = seen
                    break
    rows = sorted([row for group in by_spec.values() for row in group], key=lambda r: (not r["used_by"], r["id"] == "", r["name"].lower()))
    # The automatic pool is data, not an edit to the user's YAML registry.
    # Empty ids keep these rows on the page's existing read-only/direct-source path.
    from core.source_supply import profiles
    from core.source_supply_store import SupplyStore
    for key, profile in profiles(config_dir).items():
        for source in SupplyStore(state_dir, key).pool()["sources"]:
            last = source.get("last")
            if last:
                from datetime import datetime, timezone
                last = {**last, "at": datetime.fromtimestamp(last["at"], timezone.utc).isoformat()}
            stats = source.get("stats", {})
            rows.append({"id": "", "automatic": True, "name": source["name"], "spec": source["spec"],
                         "tags": [key, "自动来源"], "note": source.get("reason", "") +
                         f"；报告引用 {stats.get('used', 0)} 页，变化 {stats.get('changed', 0)} 条（不等于成片数）",
                         "used_by": [{"job": j, "name": profile["jobs"][j]["direction"]}
                                     for j in source["jobs"] if j in profile["jobs"]], "last": last})
    return {"sources": rows, "problems": problems}

"""Where each script came from: matrix › Job › the run's report › the production config (spec/outputs.md).

Nothing is stored for this. A script points at a topic (or a roundup at its items, a cut at its parent script, a
script written from a report at that report); the topic knows the report it was imported from and the source that
imported it; the source is a Job; the Job is in a matrix. Scripts made before production configs existed carry no
config, and each config is one recipe on one Job, so such a script's config is the one named after its recipe on
the same Job.
"""

from __future__ import annotations

from core import matrices
from core.config import load_job

from . import production, store, topic_store


def _topic_rows() -> dict[str, dict]:
    with store.connect() as conn:
        return {r["id"]: dict(r) for r in conn.execute("SELECT id, source, report_date, report_path FROM topics")}


def _report_rows() -> dict[str, dict]:
    with store.connect() as conn:
        return {r["id"]: dict(r) for r in conn.execute("SELECT id, report_path, job, kind, created_at FROM research_reports")}


def _imports() -> list[dict]:
    with store.connect() as conn:
        return [dict(r) for r in conn.execute("SELECT report_path, source, report_date FROM topic_imports")]


def _origin(script: dict, scripts: dict, topics: dict, reports: dict, depth: int = 0) -> dict:
    """The report a script was written from (the run's report, not its own research) and what produced it."""
    topic = script.get("topic") or {}
    topic_id = topic.get("id") or next(iter(topic.get("items") or []), None)
    row = topics.get(topic_id) if topic_id else None
    if row and row["source"] != topic_store.MANUAL:
        return {"source": row["source"], "report_path": row["report_path"] or "", "report_date": row["report_date"] or ""}
    parent = scripts.get(topic.get("parent_script") or "")
    if parent and depth < 5:  # a point cut from another script's research comes from wherever that one did
        return _origin(parent, scripts, topics, reports, depth + 1)
    report = reports.get(topic.get("report_id") or "")
    if report and report["kind"] != "video_research":
        return {"job": report["job"], "report_path": report["report_path"], "report_date": report["created_at"][:10]}
    return {"manual": True}


def _attribute(config_dir: str, jobs_dir: str, configs: dict, sources: dict, owner: dict) -> list[dict]:
    """Every script, archived ones included, with its origin, Job, matrix and production config (stored or inferred)."""
    every = store.list_scripts(limit=100000, archived=False) + store.list_scripts(limit=100000, archived=True)
    scripts = {s["id"]: s for s in every}
    topics, reports = _topic_rows(), _report_rows()
    out = []
    for script in every:
        origin = _origin(script, scripts, topics, reports)
        if origin.get("source"):
            source = sources.get(origin["source"])
            job = source.job if source else ""
        else:
            job = origin.get("job") or ""
        config, guessed = script.get("production") or "", False
        if not config and script["column_key"] in configs and configs[script["column_key"]].job == job:
            config, guessed = script["column_key"], True  # made before configs: the one named after its recipe
        matrix = owner.get(job)
        out.append({"script": script, "origin": origin, "job": job, "matrix": matrix.key if matrix else "",
                    "production": config, "guessed": guessed})
    return out


def count_by_config(config_dir: str, jobs_dir: str) -> dict[str, int]:
    """Production config key -> how many scripts it made, archived ones and those made before configs included.
    Failed ones are left out, as 写过了 leaves them out."""
    configs = production.load_all(config_dir, jobs_dir)
    owner = {j: m for m in matrices.load_all(config_dir).values() for j in m.jobs}
    counts: dict[str, int] = {}
    for a in _attribute(config_dir, jobs_dir, configs, topic_store.sources(jobs_dir), owner):
        if a["production"] and a["script"]["status"] != "FAILED":
            counts[a["production"]] = counts.get(a["production"], 0) + 1
    return counts


def collect(config_dir: str, jobs_dir: str, archived: bool = False) -> dict:
    """Every script (archived ones with ``archived``) with where it came from, and each Job's reports no script was
    written from. A report counts as written from when any script came from it, archived or not."""
    sources = topic_store.sources(jobs_dir)
    every_matrix = matrices.load_all(config_dir)
    owner = {j: m for m in every_matrix.values() for j in m.jobs}
    configs = production.load_all(config_dir, jobs_dir)
    renders = store.latest_render_by_script()
    published = store.published_script_ids()
    attributed = _attribute(config_dir, jobs_dir, configs, sources, owner)
    used = {a["origin"].get("report_path") for a in attributed}

    rows = []
    for a in attributed:
        script, origin, config = a["script"], a["origin"], a["production"]
        if bool(script.get("archived")) != archived:
            continue
        version = store.get_version(script["id"], script["current_version"]) if script["current_version"] else None
        rows.append({
            "id": script["id"], "title": version["title"] if version else (script.get("topic") or {}).get("title", ""),
            "status": script["status"], "column_key": script["column_key"], "created_at": script["created_at"],
            "archived": bool(script.get("archived")), "auto": script.get("created_by") == "autopilot",
            "matrix": a["matrix"], "job": a["job"], "report_path": origin.get("report_path", ""),
            "report_date": origin.get("report_date", ""), "manual": bool(origin.get("manual")),
            "production": config, "production_name": configs[config].name if config in configs else "",
            "production_guessed": a["guessed"], "render": renders.get(script["id"]),
            "published": script["id"] in published,
        })

    jobs: dict[str, dict] = {}
    for item in _imports():
        source = sources.get(item["source"])
        if source is None:
            continue
        entry = jobs.setdefault(source.job, {"job": source.job, "name": source.label, "reports": 0, "unwritten": 0,
                                             "matrix": owner[source.job].key if source.job in owner else ""})
        entry["reports"] += 1
        entry["unwritten"] += item["report_path"] not in used
    for row in rows:  # a Job that only wrote whole reports (no topics:) still gets a name
        if row["job"] and row["job"] not in jobs:
            job = load_job(jobs_dir, row["job"]) or {}
            jobs[row["job"]] = {"job": row["job"], "name": job.get("name") or row["job"], "reports": 0, "unwritten": 0,
                                "matrix": row["matrix"]}
    return {
        "scripts": rows,
        "jobs": list(jobs.values()),
        "matrices": [{"key": m.key, "name": m.name} for m in every_matrix.values()],
    }

"""Where each script came from: matrix › Job › the run's report › the production config (spec/outputs.md).

Nothing is stored for this. A script points at a topic (or a roundup at its items, a cut at its parent script, a
script written from a report at that report); the topic knows the report it was imported from and the source that
imported it; the source is a Job; the Job is in a matrix. Scripts made before production configs existed carry no
config, and each config is one recipe on one Job, so such a script's config is the one named after its recipe on
the same Job.
"""

from __future__ import annotations

import yaml

from core import matrices
from core.config import load_job

from . import credibility, production, progress, store, topic_store


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


def _attribute(config_dir: str, jobs_dir: str, configs: dict, sources: dict, owner: dict,
               scripts: list[dict] | None = None) -> list[dict]:
    """Every script, archived ones included, with its origin, Job, matrix and production config (stored or inferred)."""
    every = scripts if scripts is not None else (
        store.list_scripts(limit=100000, archived=False) + store.list_scripts(limit=100000, archived=True))
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
        job = (script.get("content_context") or {}).get("job") or job
        if not job and config in configs:
            job = configs[config].job
        if not config and script["column_key"] in configs and configs[script["column_key"]].job == job:
            config, guessed = script["column_key"], True  # made before configs: the one named after its recipe
        matrix = owner.get(job)
        source_matrix = matrix.key if matrix else ""
        out.append({"script": script, "origin": origin, "job": job,
                    "source_matrix": script.get("source_matrix_id") or source_matrix,
                    "matrix": script.get("content_matrix_id") or source_matrix,
                    "production": config, "guessed": guessed})
    return out


def script_owners(config_dir: str, jobs_dir: str, scripts: list[dict]) -> tuple[dict, list[dict]]:
    """Saved business ownership for new scripts; source Job attribution for legacy scripts and archived cuts."""
    every_matrix = matrices.load_all(config_dir)
    owner = {job: matrix for matrix in every_matrix.values() for job in matrix.jobs}
    sources = topic_store.sources(jobs_dir)
    configs = production.load_all(config_dir, jobs_dir)
    ids = {s["id"] for s in scripts}
    every = scripts + [s for s in store.list_scripts(limit=100000, archived=True) if s["id"] not in ids]
    names: dict[str, str] = {s.job: s.label for s in sources.values()}
    out = {}
    for attributed in _attribute(config_dir, jobs_dir, configs, sources, owner, scripts=every):
        script = attributed["script"]
        if script["id"] not in ids:
            continue
        job = attributed["job"]
        if not job and script.get("production") in configs:
            job = configs[script["production"]].job
        matrix = every_matrix.get(attributed["matrix"])
        if job and job not in names:
            try:
                names[job] = (load_job(jobs_dir, job) or {}).get("name") or job
            except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError):
                names[job] = job
        context = script.get("content_context") or {}
        out[script["id"]] = {"matrix": attributed["matrix"],
                             "matrix_name": matrix.content.get("name", matrix.name) if matrix else attributed["matrix"] or "独立视频",
                             "matrix_archived": bool(matrix and matrix.archived), "job": job,
                             "source_matrix": attributed["source_matrix"],
                             "content_column_id": script.get("content_column_id", ""),
                             "content_column_name": context.get("column_name", ""),
                             "job_name": names.get(job, "手动选题"),
                             "production_name": configs[attributed["production"]].name if attributed["production"] in configs else ""}
    return out, [{"key": m.key, "name": m.name} for m in every_matrix.values() if not m.archived]


def count_by_config(config_dir: str, jobs_dir: str, *, configs: dict | None = None) -> dict[str, int]:
    """Production config key -> how many scripts it made, archived ones and those made before configs included.
    Failed ones are left out, as 写过了 leaves them out."""
    if configs is None:
        configs = production.load_all(config_dir, jobs_dir)
    owner = {j: m for m in matrices.load_all(config_dir).values() for j in m.jobs}
    counts: dict[str, int] = {}
    for a in _attribute(config_dir, jobs_dir, configs, topic_store.sources(jobs_dir), owner):
        if a["production"] and a["script"]["status"] != "FAILED":
            counts[a["production"]] = counts.get(a["production"], 0) + 1
    return counts


def count_by_report(config_dir: str, jobs_dir: str) -> dict[tuple[str, str], int]:
    """Visible, nonfailed scripts per source report, without building full output cards."""
    from collections import Counter

    every = matrices.load_all(config_dir)
    owner = {j: m for m in every.values() for j in m.jobs}
    configs = production.load_all(config_dir, jobs_dir)
    counts = Counter()
    for item in _attribute(config_dir, jobs_dir, configs, topic_store.sources(jobs_dir), owner):
        script = item["script"]
        matrix = every.get(item["matrix"])
        if script["status"] == "FAILED" or script.get("archived") or (matrix and matrix.archived):
            continue
        counts[(item["job"], item["origin"].get("report_path", ""))] += 1
    return counts


def collect(config_dir: str, jobs_dir: str, archived: bool = False) -> dict:
    """Every script (the put-away ones, and those of a put-away matrix, with ``archived``) with where it came from, and each Job's reports no script was
    written from. A report counts as written from when any script came from it, archived or not."""
    sources = topic_store.sources(jobs_dir)
    every_matrix = matrices.load_all(config_dir)
    owner = {j: m for m in every_matrix.values() for j in m.jobs}
    configs = production.load_all(config_dir, jobs_dir)
    renders = store.render_statuses_by_script()
    published = store.published_versions_by_script()
    attributed = _attribute(config_dir, jobs_dir, configs, sources, owner)
    used = {a["origin"].get("report_path") for a in attributed}

    owner_archived = lambda key: every_matrix[key].archived if key in every_matrix else False  # noqa: E731
    visible = [a for a in attributed if bool(a["script"].get("archived") or (a["matrix"] and owner_archived(a["matrix"]))) == archived]
    versions = store.current_versions([a["script"]["id"] for a in visible])
    rows = []
    for a in visible:
        script, origin, config = a["script"], a["origin"], a["production"]
        version = versions.get(script["id"])
        every_render = renders.get(script["id"], [])
        publications = published.get(script["id"], set())
        rows.append({
            "id": script["id"], "title": version["title"] if version else (script.get("topic") or {}).get("title", ""),
            "content_mode": (script.get("topic") or {}).get("content_mode", "individual"),
            "episode_items": [i["title"] for i in (version or {}).get("meta", {}).get("episode", {}).get("items", [])],
            "status": script["status"], "column_key": script["column_key"], "created_at": script["created_at"],
            "version": script["current_version"], "updated_at": script["updated_at"],
            "credibility": credibility.label(version, auditing=bool(script.get("stage"))) if version else None,
            "archived": bool(script.get("archived")), "auto": script.get("created_by") == "autopilot",
            "matrix": a["matrix"], "job": a["job"], "report_path": origin.get("report_path", ""),
            "source_matrix": a["source_matrix"], "content_column_id": script.get("content_column_id", ""),
            "content_column_name": (script.get("content_context") or {}).get("column_name", ""),
            "report_date": origin.get("report_date", ""), "manual": bool(origin.get("manual")),
            "production": config, "production_name": configs[config].name if config in configs else "",
            "production_guessed": a["guessed"], **progress.render_state(script, every_render),
            "published": progress.is_published(script, publications),
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
            try:
                job = load_job(jobs_dir, row["job"]) or {}
                name = job.get("name") or row["job"]
            except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError):
                name = row["job"]
            jobs[row["job"]] = {"job": row["job"], "name": name, "reports": 0, "unwritten": 0,
                                "matrix": row["matrix"]}
    return {
        "scripts": rows,
        "jobs": list(jobs.values()),
        "matrices": [{"key": m.key, "name": m.name, "archived": m.archived} for m in every_matrix.values()],
    }

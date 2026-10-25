"""Run reviewable video-chain samples without changing production records or schedules.

Examples (reports/scripts/media call the configured external models):
    python scripts/eval_video_chain.py inventory --output state/validation/chain-review
    python scripts/eval_video_chain.py reports --only history/history_daily --output state/validation/chain-review
    python scripts/eval_video_chain.py scripts --only topic_digest --output state/validation/chain-review
    python scripts/eval_video_chain.py media --only topic_digest --output state/validation/chain-review --video-provider research

Attempts are append-only. The index points to the latest attempt, including failures.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import logging
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml

from core import events, matrices, report_meta, state
from core.config import list_jobs, load_job, load_system_config
from core.engine import ResearchEngine
from core.fileio import file_lock
from core.video import production, recipes, reports, store, topic_store
from web.settings import get_settings
from web.services import video_executor, video_render, video_workflow

# Each recipe is exercised with actual material from an active job.
CASES = {
    "report_digest": ("ai_media/daily_opensource_radar", "开发者"),
    "flash": ("monitoring/ai_flash_discovery", "开发者"),
    "point_short": ("science/science_daily", "普通用户"),
    "deep_dive": ("ai_media/weekly_deep_research", "开发者"),
    "topic_digest": ("history/history_daily", "大众观众"),
    "top_finding": ("tech/tech_daily", "普通用户"),
    "action_list": ("consumer/consumer_daily", "普通用户"),
    "ai-developer-roundup-long": ("ai_media/daily_opensource_radar", "开发者"),
    "ai-developer-comparison-long": ("ai_media/weekly_model_arena", "开发者"),
}


def save(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def record(index_path: Path, stage: str, key: str, result: dict) -> dict:
    """Independent report/media processes must not overwrite each other's receipts."""
    with file_lock(str(index_path) + ".lock", timeout=10):
        index = json.loads(index_path.read_text()) if index_path.exists() else {}
        index.setdefault(stage, {})[key] = result
        save(index_path, index)
        return index


def active_jobs() -> dict:
    every_matrix = matrices.load_all(str(ROOT / "config"))
    grouped = {job for matrix in every_matrix.values() for job in matrix.jobs}
    active = {job: matrix for matrix in every_matrix.values()
              if not matrix.archived for job in matrix.jobs
              if (load_job(str(ROOT / "jobs"), job) or {}).get("enabled", True)}
    for job in list_jobs(str(ROOT / "jobs"), enabled_only=True):
        if job not in grouped and not job.startswith("video/"):
            active[job] = None
    return active


def attempt(out: Path, stage: str, key: str) -> Path:
    path = out / stage / key.replace("/", "_") / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    path.mkdir(parents=True, exist_ok=False)
    return path


class SampleEngine(ResearchEngine):
    """Save the actual retrieval input; avoid sending test-run notifications."""

    def _notify(self, *args, **kwargs):
        return None

    def _output_reference(self, path):
        # Each sample has its own workspace; absolute receipts stay unambiguous
        # when the shared validation index later opens its report.
        return str(Path(path).resolve()) if path else None

    def _fetch_sources_strict(self, job):
        result = super()._fetch_sources_strict(job)
        (self.validation_dir / "retrieved-material.md").write_text(result[0], encoding="utf-8")
        return result

    def _fetch_sources_block(self, job):
        result = super()._fetch_sources_block(job)
        (self.validation_dir / "retrieved-material.md").write_text(result, encoding="utf-8")
        return result


def report_case(out: Path, key: str) -> dict:
    work = attempt(out, "reports", key)
    job = dict(load_job(str(ROOT / "jobs"), key) or {})
    if not job:
        raise ValueError(f"Missing job: {key}")
    job.pop("_file", None)
    job.pop("_mtime", None)
    job["output"] = str(work / "output/report.md")
    path = work / "jobs" / (key + ".yaml")
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(job, allow_unicode=True, sort_keys=False), encoding="utf-8")
    engine = SampleEngine(config_dir=str(ROOT / "config"), jobs_dir=str(work / "jobs"),
                          workspace_dir=str(work), user="chain-validation")
    engine.validation_dir = work
    resolved = matrices.for_run(load_job(str(work / "jobs"), key), str(ROOT / "config"))
    (work / "task.md").write_text(engine._build_prompt(resolved), encoding="utf-8")
    started = time.monotonic()
    path = engine.run_job(key)
    full_path = (ROOT / path).resolve() if path else None
    result = {"job": key, "work": str(work), "seconds": round(time.monotonic() - started, 2),
              "status": "COMPLETED" if full_path and full_path.is_file() else "FAILED", "report": str(full_path or "")}
    if result["status"] == "FAILED":
        result["error"] = (state.StateManager.get(key) or {}).get("last_error")
    save(work / "result.json", result)
    return result


def script_case(out: Path, key: str, index: dict) -> dict:
    require_isolated_store(out)
    job, audience = CASES[key]
    report = index.get("reports", {}).get(job, {})
    if report.get("status") != "COMPLETED":
        raise ValueError(f"Generate the report first: {job}")
    work = attempt(out, "scripts", key)
    report_path = Path(report["report"])
    base = recipes.load(str(ROOT / "config"))[key]
    audience_key = {"普通用户": "non_tech", "大众观众": "general", "开发者": "developer"}[audience]
    column = (recipes.variant_key(base, audience_key)
              if base.audiences and audience_key != base.audiences[0] else key)
    recorded = reports.record_file(str(report_path), job=job, created_by="chain-validation")
    topic = {"title": (load_job(str(ROOT / "jobs"), job) or {})["name"], "test_sample": True,
             "fields": {"目标受众": audience}, "from_report": str(report_path), "report_id": recorded["id"]}
    if key == "flash":
        source = topic_store.resolve(str(ROOT / "jobs"), job)
        topics = topic_store.parse(source, report_path.read_text(), datetime.now().strftime("%Y-%m-%d"))
        if not topics:
            raise ValueError("Discovery report did not yield selectable topics")
        chosen = topics[0]
        topic.update(title=chosen.title, from_item=chosen.id)
        topic["fields"].update(chosen.fields)
        topic["fields"]["目标受众"] = audience
    script = store.create_script(column, topic, created_by="chain-validation")
    configs = production.load_all(str(ROOT / "config"), str(ROOT / "jobs"))
    match = next((cfg for cfg in configs.values() if cfg.usable and cfg.job == job and cfg.recipe == column), None)
    if match:
        store.update_script(script["id"], production=match.key)
    started = time.monotonic()
    status = video_workflow.run(script["id"], started_by="chain-validation")
    script = store.get_script(script["id"])
    version = store.get_version(script["id"], script["current_version"])
    result = {"recipe": key, "column": column, "job": job, "status": status, "work": str(work),
              "seconds": round(time.monotonic() - started, 2), "script": script, "version": version}
    save(work / "workflow.json", result)
    if version:
        body = "\n\n".join(p["text"] for section in version["sections"] for p in section["paragraphs"])
        (work / "script.md").write_text("# " + version["title"] + "\n\n" + body, encoding="utf-8")
        result["script_file"] = str(work / "script.md")
        result["characters"] = len(body)
    return result


def media_case(out: Path, key: str, index: dict, provider: str, mode: str | None) -> dict:
    require_isolated_store(out)
    previous = index.get("scripts", {}).get(key, {})
    if previous.get("status") != "DRAFT":
        raise ValueError(f"Generate a script first: {key}")
    script = store.get_script(previous["script"]["id"])
    # Use the same approval and request construction as the unattended workflow.
    video_executor.advance_auto(script, {}, can_start_script=True, can_start_render=False)
    script = store.get_script(script["id"])
    render = video_render.create(script, production_mode=mode)
    request = render["request"]
    work = attempt(out, "media", key)
    save(work / "research-request.json", request)
    auto_root = ROOT.parents[1] / "autovideo"
    sys.path.insert(0, str(auto_root))
    from g3.app import CreateJobRequest, create_app
    from g3.config import Settings
    from g3.store import JobStore
    from g3.worker import G2ProcessRunner
    import threading

    settings = Settings(data_dir=auto_root / "g3/data" / out.name)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if provider == "research":
        cfg = load_system_config(str(ROOT / "config"))["ai"]
        save(settings.llm_settings_file, {"base_url": cfg["base_url"], "model": cfg["model"],
             "key_env": cfg["api_key_env"], "env_file": str(ROOT / ".env"), "max_tokens": cfg.get("max_tokens", 32768)})
    else:
        source = auto_root / "g3/data/llm-settings.json"
        if source.is_file():
            settings.llm_settings_file.write_bytes(source.read_bytes())
    jobs = JobStore(settings.data_dir)
    jobs.initialize()
    # Reuse the real API's submission normalization and preflight. Entering its
    # lifespan would recover unrelated running samples, so only call this route.
    app = create_app(settings, start_worker=False)
    submit = next(route.endpoint for route in app.routes
                  if route.path == "/api/jobs" and "POST" in route.methods)
    submitted = submit(CreateJobRequest(**request))
    job = jobs.get_job(submitted["job_id"])
    save(work / "job.json", {"job_id": job["job_id"], "data_dir": str(settings.data_dir), "provider_source": provider})
    claimed = jobs.claim_next()
    if not claimed or claimed["job_id"] != job["job_id"]:
        raise RuntimeError("Another isolated job is pending; finish it before starting a new sample")
    record(out / "validation-index.json", "media", key, {
        "recipe": key, "work": str(work), "status": "RUNNING", "job": claimed,
        "research_render_id": render["id"], "provider_source": provider,
        "script_id": script["id"], "script_version": script["current_version"],
    })
    G2ProcessRunner(settings, jobs).execute(claimed, threading.Event())
    final = jobs.get_job(job["job_id"])
    result = {"recipe": key, "work": str(work), "status": final["status"], "job": final,
              "research_render_id": render["id"], "provider_source": provider,
              "script_id": script["id"], "script_version": script["current_version"]}
    save(work / "result.json", result)
    return result


def validation_output(path: Path) -> Path:
    """Keep real-generation artifacts outside the production report index."""
    resolved = path.resolve()
    if not resolved.is_relative_to((ROOT / "state/validation").resolve()):
        raise ValueError("Use --output state/validation/<name>; validation artifacts must stay outside production reports")
    return resolved


def require_isolated_store(out: Path) -> None:
    """Programmatic callers must use the same isolated ledger as the CLI; never create samples in production."""
    expected = validation_output(out) / "isolated-state" / "video.db"
    if Path(store.db_path()).resolve() != expected:
        raise ValueError("验收样片必须使用输出目录下的 isolated-state/video.db，不能写入正式视频数据库")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("inventory", "reports", "scripts", "media"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--only", default="")
    parser.add_argument("--skip-completed", action="store_true")
    parser.add_argument("--video-provider", choices=("research", "autovideo"), default="autovideo")
    parser.add_argument("--mode", choices=("standard", "fast"))
    args = parser.parse_args()
    try:
        out = validation_output(args.output)
    except ValueError as exc:
        parser.error(str(exc))
    out.mkdir(parents=True, exist_ok=True)
    get_settings()  # Load the existing .env without exposing it in artifacts.
    isolated = out / "isolated-state"
    state.use_state_dir(str(isolated))
    report_meta.META_DIR, report_meta.META_FILE = str(isolated), str(isolated / "report_meta.jsonl")
    events.use_state_dir(str(isolated))
    store.use_state_dir(str(isolated))
    store.init_db()
    index_path = out / "validation-index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    if args.stage == "inventory":
        data = {"jobs": {key: matrix.key if matrix else None for key, matrix in active_jobs().items()},
                "production": [cfg.as_dict() for cfg in production.load_all(str(ROOT / "config"), str(ROOT / "jobs")).values()
                               if cfg.job in active_jobs()],
                "recipes": {key: {"usable": r.usable, "sha256": r.sha256, "files": r.uses}
                            for key, r in recipes.load(str(ROOT / "config")).items()}, "cases": CASES}
        save(out / "live-inventory.json", data)
        print(json.dumps({"jobs": len(data["jobs"]), "production": len(data["production"]),
                          "recipes": len(data["recipes"])}, ensure_ascii=False), flush=True)
        return 0
    keys = list(active_jobs() if args.stage == "reports" else CASES)
    if args.only:
        if args.only not in keys:
            parser.error("Unknown active job or recipe")
        keys = [args.only]
    if args.skip_completed:
        keys = [key for key in keys if index.get(args.stage, {}).get(key, {}).get("status")
                not in ("COMPLETED", "DRAFT")]
    errors = 0
    for key in keys:
        try:
            if args.stage == "reports":
                result = report_case(out, key)
            elif args.stage == "scripts":
                result = script_case(out, key, index)
            else:
                result = media_case(out, key, index, args.video_provider, args.mode)
        except Exception as exc:
            logging.exception("Validation failed: %s", key)
            result = {"status": "FAILED", "error": f"{type(exc).__name__}: {exc}"}
        index = record(index_path, args.stage, key, result)
        errors += result["status"] not in ("COMPLETED", "DRAFT")
        print(json.dumps({"stage": args.stage, "key": key, "status": result["status"]}, ensure_ascii=False), flush=True)
    return int(bool(errors))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(main())

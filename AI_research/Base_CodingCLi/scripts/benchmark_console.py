"""Measure console read handlers against the current local data; no LLM calls or workflow starts.

Run from the repository root: python scripts/benchmark_console.py --iterations 5
This measures server handler work, not browser, authentication or gateway latency.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=5, choices=range(1, 21))
    args = parser.parse_args()
    from core import health
    from core.video import store
    from fastapi.encoders import jsonable_encoder
    from web.indexer import db as index_db
    from web.routes import config, jobs, logs, matrices, outputs, pipeline, production, reports, scheduler, sources, usage, video, video_recipes
    from web.services import assistant_store, douyin, platform_manager, social_publishing
    from core.social.registry import active_platforms

    # Topic reads reconcile new reports into SQLite. Never let a benchmark
    # modify the running service's database or wait on its writers.
    with tempfile.TemporaryDirectory(prefix="arec-console-benchmark-") as directory:
        previous = [(store, store.db_path()), (index_db, index_db.db_path())]
        assistant_settings = assistant_store.get_settings
        try:
            for module, path in previous:
                target = Path(directory) / Path(path).name
                with sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True) as source, sqlite3.connect(target) as dest:
                    source.backup(dest)
                module.set_db_path(str(target))
            handlers = {
                "outputs": outputs.list_outputs, "production": production.list_configs,
                "matrices": matrices.list_matrices, "columns": video.list_columns,
                "board": video.home_board, "scripts": video.list_scripts, "sources": sources.list_sources,
                "shipments": video.shipments, "topics": video.topic_browser,
                "automation_alerts": video.automation_alerts, "automation_batches": video.autopilot_overview,
                "jobs": jobs.list_all, "rounds": lambda **kw: pipeline.list_rounds(limit=30, **kw),
                "schedules": scheduler.list_scheduled,
                "schedules_managed": lambda **kw: scheduler.list_scheduled(scope="managed", **kw),
                "schedules_system": lambda **kw: scheduler.list_scheduled(scope="system", **kw),
                "recipes": video_recipes.list_recipes,
                "recipe_parts": video_recipes.list_parts, "system_config": config.get_system_config,
                "provider_presets": config.get_providers, "report_stats": reports.stats,
                "ratings": usage.get_ratings, "usage": lambda **kw: usage.get_usage(days=30, month=False, **kw),
                "health": lambda **kw: health.run_checks(),
                "audit": lambda **kw: logs.get_audit_log(limit=500, **kw),
                "runtime_logs": lambda **kw: logs.get_global_logs(lines=500, **kw),
                "platform_bilibili": lambda **kw: platform_manager.overview(),
                "platform_douyin": lambda **kw: douyin.overview(),
            }
            for platform in active_platforms():
                handlers[f"platform_{platform}"] = lambda platform=platform, **kw: social_publishing.overview(platform)
            filters = dict(category=None, job=None, favorite=None, tag=None, unread=None,
                           since=None, until=None, latest_round=False)
            handlers["report_tree"] = lambda **kw: reports.tree(**filters, **kw)
            handlers["report_list"] = lambda **kw: reports.list_reports(page=1, per_page=20, sort="recent", **filters, **kw)
            every = matrices.matrices.load_all("config")
            if every:
                key = next(iter(every))
                handlers["matrix_detail"] = lambda **kw: matrices.get_matrix(key, **kw)
                handlers["matrix_runs"] = lambda **kw: matrices.matrix_runs(key, **kw)
                handlers["matrix_calendar"] = lambda **kw: matrices.matrix_calendar(key, **kw)
            scripts = store.list_scripts(limit=1)
            if scripts:
                script_id = scripts[0]["id"]
                handlers["script_detail"] = lambda **kw: video.get_script(script_id, **kw)
            indexed = index_db.list_reports(page=1, per_page=1)["items"]
            if indexed:
                report_path = indexed[0]["path"]
                handlers["report_content"] = lambda **kw: reports.raw_report(report_path, **kw)
            assistant_db = Path(assistant_settings().paths.state_dir) / "assistant.db"
            if assistant_db.is_file():
                with sqlite3.connect(f"file:{assistant_db.resolve()}?mode=ro", uri=True) as source, sqlite3.connect(Path(directory) / "assistant.db") as dest:
                    source.backup(dest)
                    latest = dest.execute("SELECT user_id FROM conversations ORDER BY updated DESC LIMIT 1").fetchone()
                snapshot = assistant_settings().model_copy(deep=True)
                snapshot.paths.state_dir = directory
                assistant_store.get_settings = lambda: snapshot
                identity = latest[0] if latest else "benchmark"
                handlers["assistant_recovery"] = lambda **kw: assistant_store.AssistantStore().conversation({"id": identity, "username": str(identity)})
                from starlette.requests import Request
                from web.deps import current_user
                from web.server import create_app
                from web.services import assistant_workspace
                app = create_app()
                user = {"id": identity, "username": str(identity), "role": "admin"}
                app.dependency_overrides[current_user] = lambda: user
                request = Request({"type": "http", "app": app, "headers": [],
                                   "client": ("127.0.0.1", 0), "method": "GET", "scheme": "http",
                                   "path": "/api/assistant/tasks", "query_string": b""})
                handlers["assistant_tasks"] = lambda **kw: asyncio.run(assistant_workspace.task_center(request, user, 20))
            results = measure(handlers, args.iterations, jsonable_encoder)
        finally:
            assistant_store.get_settings = assistant_settings
            for module, path in previous:
                module.set_db_path(path)
    print(json.dumps({"measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                      "scope": "local read handlers with SQLite snapshots; excludes browser/auth/gateway", "handlers": results}, indent=2))


def measure(handlers: dict, iterations: int, encode) -> dict:
    """Measure handler work and response size, never print report text, secrets or user records."""
    results = {}
    for name, handler in handlers.items():
        samples = []
        serialization = []
        totals = []
        try:
            for _ in range(iterations):
                start = time.perf_counter()
                data = handler(user={})
                handled = time.perf_counter()
                payload = json.dumps(encode(data), ensure_ascii=False).encode()
                finished = time.perf_counter()
                samples.append(round((handled - start) * 1000, 1))
                serialization.append(round((finished - handled) * 1000, 1))
                totals.append(round((finished - start) * 1000, 1))
            results[name] = {"samples_ms": samples, "median_ms": statistics.median(samples), "max_ms": max(samples),
                             "serialization_median_ms": statistics.median(serialization),
                             "total_samples_ms": totals, "total_median_ms": statistics.median(totals),
                             "response_bytes": len(payload)}
        except Exception as exc:
            results[name] = {"error": type(exc).__name__}
    return results


if __name__ == "__main__":
    main()

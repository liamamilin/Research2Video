"""Normal read endpoints on fixed data; no lifespan, workers, launchd or networking.

The CLI compares the report with a reviewed baseline. This test also runs without
the CLI and asserts that non-empty data survives every measured endpoint.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import statistics
import socket
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.console_workload import MANIFEST, seed

# Multiple primary reads for some Tabs: count and measure them separately.
CASES = [
    ("chains", "链路管理", "/api/chains"),
    ("chain_catalog", "链路管理", "/api/chains/catalog"),
    ("chain_scope", "链路筛选", "/api/chains/chain_000/scope"),
    ("matrices", "矩阵", "/api/matrices?compact=true"),
    ("outputs", "产出", "/api/outputs"),
    ("board", "视频看板", "/api/video/board"),
    ("scripts", "文稿", "/api/video/scripts?compact=true"),
    ("shipments", "成片与发布", "/api/video/shipments?compact=true"),
    ("platforms", "平台管理", "/api/platforms/bilibili"),
    ("assistant", "助手", "/api/assistant/conversation"),
    ("dashboard_usage", "研究总览", "/api/usage?days=0&month=false"),
    ("jobs", "Jobs / 研究总览", "/api/jobs?compact=true"),
    ("rounds", "情报轮次", "/api/pipeline/rounds?limit=30"),
    ("reports", "报告", "/api/reports"),
    ("report_tree", "报告", "/api/reports/tree"),
    ("schedules", "周期调度", "/api/scheduler?scope=managed"),
    ("topics", "选题", "/api/video/topics/browser"),
    ("sources", "信息源", "/api/sources"),
    ("recipes", "文稿配方", "/api/video/recipes"),
    ("analytics", "数据分析", "/api/telemetry/summary?days=0"),
    ("analytics_scripts", "数据分析", "/api/telemetry/scripts?days=0&limit=25"),
    ("settings", "设置", "/api/config/system"),
]


@pytest.mark.console_performance
def test_fixed_console_workload(web_env, monkeypatch):
    from core import report_meta
    from core.video import production
    from web.deps import current_user
    from web.list_compression import ListCompressionMiddleware
    from web.routes import (assistant, chains, config, jobs, matrices, outputs, pipeline,
                            platforms, reports, scheduler, sources, telemetry, video, video_recipes, usage)
    from web.services import bilibili

    root, _ = web_env
    monkeypatch.setattr(report_meta, "META_FILE", str(root / "state/report_meta.jsonl"))
    monkeypatch.setattr(telemetry, "_ensure_synced", lambda: False)  # background import is outside GET timing
    monkeypatch.setattr(bilibili, "biliup_binary", lambda: None)  # external executable discovery
    def deny_network(*args, **kwargs):
        raise AssertionError("performance fixture must not open network connections")
    monkeypatch.setattr(socket.socket, "connect", deny_network)
    spec = seed(root)
    configs = production.load_all(str(root / "config"), str(root / "jobs"))
    assert len(configs) == spec["jobs"] and all(c.usable for c in configs.values()), [c.errors for c in configs.values() if not c.usable]
    app = FastAPI()
    app.add_middleware(ListCompressionMiddleware)
    app.dependency_overrides[current_user] = lambda: {"id": 1, "username": "fixture", "role": "admin"}
    for module in (assistant, chains, config, jobs, matrices, outputs, pipeline, platforms,
                   reports, scheduler, sources, telemetry, video, video_recipes, usage):
        app.include_router(module.router)
    # Counts only, never SQL text or response bodies in benchmark reports.
    counts: Counter = Counter()
    real_connect = sqlite3.connect

    def connect(*args, **kwargs):
        counts["connections"] += 1
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(lambda sql: counts.update([sql.lstrip().split(None, 1)[0].upper()]) if sql.strip() else None)
        return conn

    monkeypatch.setattr(sqlite3, "connect", connect)
    result = {"workload": spec, "fixture_sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(), "cases": {}}
    samples = int(os.environ.get("AREC_PERF_SAMPLES", "5"))
    rounds = int(os.environ.get("AREC_PERF_ROUNDS", "1"))
    with TestClient(app) as client:
        # This app has no startup hooks. Outbound HTTP is forbidden even on localhost.
        import httpx
        original_send = httpx.Client.send

        def offline_send(self, request, *args, **kwargs):
            assert request.url.host == "testserver", f"unexpected outbound request: {request.url.host}"
            return original_send(self, request, *args, **kwargs)

        monkeypatch.setattr(httpx.Client, "send", offline_send)
        for key, tab, path in CASES:
            measured = {"tab": tab, "path": path, "samples_ms": [], "sql_selects": [], "connections": [],
                        "schema_statements": [], "decoded_bytes": [], "wire_bytes": []}
            for i in range(1 + samples * rounds):
                counts.clear()
                started = time.perf_counter()
                response = client.get(path)
                elapsed = (time.perf_counter() - started) * 1000
                assert response.status_code == 200, f"{tab} {path}: HTTP {response.status_code}"
                data = response.json()
                if i == 0:
                    measured["first_request_ms"] = elapsed
                else:
                    measured["samples_ms"].append(elapsed)
                    measured["sql_selects"].append(counts["SELECT"])
                    measured["connections"].append(counts["connections"])
                    measured["schema_statements"].append(counts["CREATE"] + counts["ALTER"])
                    measured["decoded_bytes"].append(len(response.content))
                    measured["wire_bytes"].append(int(response.headers.get("content-length", len(response.content))))
            measured["median_ms"] = statistics.median(measured["samples_ms"])
            measured["mad_ms"] = statistics.median(abs(x - measured["median_ms"]) for x in measured["samples_ms"])
            result["cases"][key] = measured
            # Non-empty data checks prevent 'optimizing' by silently dropping records.
            active = spec["scripts"] - spec["archived_scripts"]
            if key in {"scripts", "jobs", "recipes"}:
                assert len(data) == {"scripts": active, "jobs": spec["jobs"], "recipes": 1}[key]
            elif key in {"board", "outputs"}:
                assert len(data["items" if key == "board" else "scripts"]) == active
            elif key == "matrices":
                assert len(data["matrices"]) == spec["matrices"]
            elif key == "reports":
                assert data["total"] == spec["reports"]
            elif key == "assistant":
                assert len(data["requests"]) == 60
            elif key == "analytics":
                assert data["enabled"] and data["llm"]["calls"] == spec["telemetry_calls"]
            elif key == "topics":
                assert len(data["initial"]["topics"]) == spec["topics"]
            elif key == "platforms":
                assert len(data["candidates"]) > 100
            elif key == "dashboard_usage":
                assert data["totals"]["runs"] == spec["history_runs"]
            print(f"{tab}: {measured['median_ms']:.1f} ms, {max(measured['decoded_bytes'])} B, "
                  f"{max(measured['sql_selects'])} SELECT, {max(measured['connections'])} connections")
    if os.environ.get("AREC_PERF_REPORT"):
        Path(os.environ["AREC_PERF_REPORT"]).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")

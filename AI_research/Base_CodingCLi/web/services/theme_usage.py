"""Read existing production records; inspecting usage never calls a provider."""
from __future__ import annotations

import json

from core.state import StateManager
from core.theme_library import ThemeLibrary
from core.video import store
from web.settings import get_settings


def _totals(attempts: list[dict]) -> dict:
    result = {}
    for key in ("requests", "prompt_tokens", "completion_tokens", "total_tokens", "searches"):
        values = [a.get("usage", {}).get(key) for a in attempts]
        known = [v for v in values if isinstance(v, (int, float))]
        result[key] = sum(known) if known else None
    durations = [a["duration_seconds"] for a in attempts if isinstance(a.get("duration_seconds"), (int, float))]
    result["duration_seconds"] = round(sum(durations), 3) if durations else None
    costs = [a.get("cost_usd") for a in attempts]
    result["cost_usd"] = round(sum(costs), 6) if costs and all(isinstance(c, (int, float)) for c in costs) else None
    result["partial"] = bool(attempts) and any(not a.get("usage") or a.get("timings", {}).get("process_runs", 1) > 1 for a in attempts)
    return result


def usage_for_theme(theme: dict) -> dict:
    stages = []
    generation = ThemeLibrary(get_settings().paths.state_dir).for_theme(
        theme["matrix"], theme["id"], theme["inspiration"].get("generation_id", ""))
    attempts = []
    if generation:
        result = generation["result"]
        attempts = [{"id": generation["id"], "status": generation["status"], "usage": result.get("usage", {}),
                     "duration_seconds": result.get("duration_seconds") or
                         (round(generation["finished_at"] - generation["created_at"], 3) if generation["finished_at"] else None)}]
    stages.append({"key": "ideas", "name": "整批出题", "shared": True,
                   "batch_topics": generation["result"].get("recommended") if generation else None,
                   "attempts": attempts, **_totals(attempts)})
    script = store.get_script(theme["script_id"]) if theme["script_id"] else None
    research = []
    if script and script.get("research_job"):
        research = [{"status": r["status"], "usage": r.get("usage") or {}, "duration_seconds": r.get("duration_seconds")}
                    for r in StateManager.get_history(script["research_job"], limit=0)
                    if r["status"] != "skipped"]
    stages.append({"key": "research", "name": "主题研究", "attempts": research, **_totals(research)})
    versions = [store.get_version(script["id"], v["version"]) for v in store.list_versions(script["id"])
                if v["edit_type"] == "GENERATED"] if script else []
    writing = [{"id": v["version"], "status": "saved", "usage": v.get("meta", {}).get("usage") or {},
                "duration_seconds": v.get("meta", {}).get("duration_seconds")} for v in versions]
    stages.append({"key": "writing", "name": "文稿", "attempts": writing, **_totals(writing)})
    video = []
    if script:
        from web.services import video_render
        for render in store.list_renders(script["id"]):
            artifact = video_render.safe_file(render, "model-cost.json")
            try:
                cost = json.loads(artifact.read_text()) if artifact else {}
            except (OSError, ValueError):
                cost = {}
            calls = cost.get("calls") or []
            measured = [c for c in calls if "prompt_tokens" in c and "completion_tokens" in c]
            usage = {"requests": len(calls), "prompt_tokens": sum(c["prompt_tokens"] for c in measured),
                     "completion_tokens": sum(c["completion_tokens"] for c in measured)} if calls else {}
            if measured:
                usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
            # Zero rates in the existing .env mean no usable price, not a free API.
            priced = bool(calls) and all(
                (c.get("pricing_snapshot", {}).get("input_uncached_peak_usd_per_million", 0) or
                 c.get("pricing_snapshot", {}).get("output_peak_usd_per_million", 0)) > 0
                and isinstance(c.get("cloud_api_cost_estimate_usd"), (int, float)) for c in calls)
            timings = video_render.render_timings(render)
            video.append({"id": render["id"], "status": render["status"], "usage": usage,
                          "duration_seconds": timings.get("elapsed_seconds"),
                          "cost_usd": sum(c["cloud_api_cost_estimate_usd"] for c in calls) if priced else None,
                          "cost_basis": "模型调用估算，不含本地渲染、电费或固定订阅" if priced else "未配置可用单价",
                          "timings": {k: timings[k] for k in ("process_runs", "submit_seconds", "poll_seconds", "download_seconds", "process_seconds") if k in timings}})
    stages.append({"key": "video", "name": "视频制作", "attempts": video, **_totals(video)})
    return {"theme_id": theme["id"], "stages": stages,
            "note": "显示已记录用量，重试中缺失的记录不计入已知合计。出题为整批共享，视频耗时包含排队及重试等待。搜索为逻辑批次，不等同供应商计费请求。未知费用不能视为免费，估算不代表实际账单。"}

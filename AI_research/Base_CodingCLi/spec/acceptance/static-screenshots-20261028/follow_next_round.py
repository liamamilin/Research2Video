"""Inventory the next automatic batch of each AI column without changing production.

Only this monitor's baseline, observations and latest.json are written. A small
summary locates new videos; actual MP4 review is separate and remains necessary.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
WATCH = ROOT / "state/validation/static-screenshots-20261028/next-round-watch"
COLUMNS = {
    "ai_flash": ("AI 快讯", "flash_daily", "monitoring/ai_flash_discovery"),
    "ai_opensource": ("AI 开源发现", "ai_media_opensource_digest", "ai_media/daily_opensource_radar"),
    "ai_model_review": ("AI 模型观察", "ai_media_arena_digest", "ai_media/weekly_model_arena"),
    "ai_business": ("AI 产业观察", "ai_media_business_digest", "ai_media/weekly_business_watch"),
    "ai_deep_research": ("AI 深度解读", "ai_media_deep_dive", "ai_media/weekly_deep_research"),
}


def read_db(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("BEGIN")
    return db


def load(value):
    return json.loads(value) if isinstance(value, str) else value or {}


def local_time(value: float) -> str:
    return datetime.fromtimestamp(value, ZoneInfo("Asia/Shanghai")).isoformat()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--init", action="store_true")
    args = parser.parse_args()
    with read_db(ROOT / "state/video.db") as db:
        runs = [dict(row) for row in db.execute(
            "SELECT id, spec, status, started_by, started_at, created_at, stage, note FROM autopilot_runs ORDER BY started_at, id")]
        if args.init:
            WATCH.mkdir(parents=True, exist_ok=True)
            baseline = {"schema_version": 1, "created_at": time.time(),
                "scope": {key: {"name": name, "production": production, "job": job}
                          for key, (name, production, job) in COLUMNS.items()},
                "known_autopilot_run_ids": [r["id"] for r in runs],
                "known_render_ids": [r[0] for r in db.execute("SELECT id FROM renders")],
                "known_script_ids": [r[0] for r in db.execute("SELECT id FROM scripts")],
                "active_renders": [dict(r) for r in db.execute(
                    "SELECT id, autovideo_job_id, status, active_stage FROM renders "
                    "WHERE status IN ('SUBMITTING','QUEUED','RUNNING','DOWNLOADING')")],
                "rule": "First newly triggered automatic batch per production after this baseline; preserve every existing task and attempt"}
            with (WATCH / "baseline.json").open("x", encoding="utf-8") as stream:
                json.dump(baseline, stream, ensure_ascii=False, indent=2)
        baseline = json.loads((WATCH / "baseline.json").read_text())
        known_runs = set(baseline["known_autopilot_run_ids"])
        known_renders = set(baseline["known_render_ids"])
        new_runs = [r for r in runs if r["id"] not in known_runs and r["started_by"] == "autopilot"]
        with read_db(ROOT / "state/schedules.db") as scheduler:
            schedules = {row["job_name"]: dict(row) for row in scheduler.execute(
                "SELECT job_name, enabled, next_run_at, timezone FROM schedules WHERE deleted=0")}
        snapshot = {"baseline_at": local_time(baseline["created_at"]), "columns": []}
        for key, (name, production, job) in COLUMNS.items():
            batch = next((r for r in new_runs if any(p.get("production") == production
                         for p in load(r["spec"]).get("picks", []))), None)
            schedule = schedules.get(job, {})
            row = {"key": key, "name": name, "production": production, "job": job,
                "schedule_enabled": bool(schedule.get("enabled")),
                "next_report_at": local_time(schedule["next_run_at"]) if schedule.get("next_run_at") else None,
                "state": "waiting_next_batch", "batch": None, "items": [], "renders": []}
            if batch:
                row.update(state="following_batch", batch={k: batch[k] for k in (
                    "id", "status", "created_at", "stage", "note")})
                row["items"] = [dict(r) for r in db.execute(
                    "SELECT id, script_id, render_id, state, note FROM autopilot_items "
                    "WHERE run_id=? AND production=? ORDER BY slot", (batch["id"], production))]
                for item in row["items"]:
                    if not item["script_id"]:
                        continue
                    for render in db.execute(
                        "SELECT id, status, autovideo_job_id, active_stage, error_code, error_message, visuals "
                        "FROM renders WHERE script_id=? ORDER BY created_at", (item["script_id"],)):
                        if render["id"] in known_renders:
                            continue
                        visuals = load(render["visuals"])
                        row["renders"].append({**{k: render[k] for k in render.keys() if k != "visuals"},
                            "visual_state": visuals.get("state"),
                            "selected_images": len(visuals.get("selection", {}).get("materials", [])),
                            "review_record": str(WATCH / "reviews" / (render["id"] + ".json")),
                            "review_record_exists": (WATCH / "reviews" / (render["id"] + ".json")).is_file()})
            snapshot["columns"].append(row)
    serialized = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    fingerprint = hashlib.sha256(serialized.encode()).hexdigest()
    latest = WATCH / "latest.json"
    previous = json.loads(latest.read_text()) if latest.exists() else {}
    changed = previous.get("fingerprint") != fingerprint
    if changed:
        observed = {"checked_at": local_time(time.time()), "fingerprint": fingerprint, **snapshot}
        (WATCH / "observations").mkdir(exist_ok=True)
        target = WATCH / "observations" / f"{time.time_ns()}-{fingerprint[:10]}.json"
        target.write_text(json.dumps(observed, ensure_ascii=False, indent=2))
        temporary = latest.with_suffix(".tmp")
        temporary.write_text(json.dumps(observed, ensure_ascii=False, indent=2))
        temporary.replace(latest)
    print(json.dumps({"changed": changed, "evidence": str(WATCH), "columns": [
        {k: row[k] for k in ("name", "state", "next_report_at", "batch", "renders")}
        for row in snapshot["columns"]]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

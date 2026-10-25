"""Verify and compare the two real service runs, without starting generation."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
RESEARCH = ROOT.parent / "AI_research/Base_CodingCLi"


def parse(value):
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")


def seconds(start, end):
    return round((parse(end) - parse(start)).total_seconds(), 3)


def read(path):
    return json.loads(path.read_text())


def connection(path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


research = connection(RESEARCH / "state/video.db")
auto = connection(ROOT / "g3/data/jobs.sqlite3")
baseline = read(ROOT / "g3/acceptance/production-plan-fast-cards-20261023/live-run.json")
records = {"baseline": {**baseline, "tts_step_seconds": 243.79}}
for name, config in read(OUT / "runs.json").items():
    render = dict(research.execute("SELECT * FROM renders WHERE id=?", (config["id"],)).fetchone())
    if render["status"] != "COMPLETED":
        print(name, render["status"])
        continue
    job = render["autovideo_job_id"]
    attempt = dict(auto.execute("SELECT * FROM attempts WHERE job_id=? ORDER BY attempt_no DESC LIMIT 1", (job,)).fetchone())
    root = ROOT / "g3/data" / attempt["workspace_rel_path"] / "g2-run"
    downloads = RESEARCH / "state/video_renders" / render["id"]
    registered = {r["name"]: dict(r) for r in auto.execute("SELECT * FROM artifacts WHERE job_id=? AND attempt_no=?", (job, attempt["attempt_no"]))}
    items = json.loads(render["artifacts"])
    for item in items:
        path = downloads / item["name"]
        assert path.stat().st_size == item["bytes"] == registered[path.name]["bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"] == registered[path.name]["sha256"]
    qa, package, tts, voice = [read(downloads / name) for name in ("qa.json", "package-qa.json", "tts-metrics.json", "voice-report.json")]
    assert qa["status"] == package["status"] == "PASS"
    assert read(downloads / "narration-check.json")["status"] == "PASS"
    assert render["source_sha256"] == baseline["source_sha256"]
    old_tts = read(ROOT / "g3/data/jobs" / baseline["autovideo_job_id"] / "attempts/1/g2-run/artifacts/tts-metrics.json")
    assert tts["audio_sha256"] == old_tts["audio_sha256"]
    log = (root.parent / "worker.log").read_text()
    stages = {key: float(value) for key, value in re.findall(r"STEP ([a-z0-9-]+): PASS in ([0-9.]+)s", log)}
    timings = json.loads(render["timings"])
    records[name] = {
        "render_id": render["id"], "autovideo_job_id": job, "status": render["status"],
        "execution_seconds": seconds(attempt["started_at"], attempt["completed_at"]),
        "research_end_to_end_seconds": seconds(render["created_at"], render["completed_at"]),
        "tts_step_seconds": stages["tts-and-align"], "stages": stages, "transfer": timings,
        "tts": tts, "voice_performance": voice.get("performance"), "cache": voice.get("cache"),
        "source_sha256": render["source_sha256"], "identical_pcm_to_previous": True,
        "verified_files": len(items), "content_qa": qa["status"], "package_qa": package["status"],
        "caption_cues_checked": qa["fast_cards"]["caption_cues_checked"],
        "content_seconds": qa["video"]["duration_seconds"],
        "packaged_seconds": package["checks"]["duration"]["seconds"],
    }
    folder = OUT / name
    folder.mkdir(exist_ok=True)
    for source in ("qa.json", "package-qa.json", "tts-metrics.json", "voice-report.json", "narration-check.json", "execution-log.txt"):
        shutil.copy2(downloads / source, folder / source)
    (folder / "artifacts.json").write_text(json.dumps(items, indent=2) + "\n")
    print(name, records[name]["execution_seconds"], records[name]["research_end_to_end_seconds"], stages["tts-and-align"], timings.get("download_seconds"))
for name in ("cold", "warm"):
    if name in records:
        records[name]["end_to_end_reduction_percent"] = round(100 * (1 - records[name]["research_end_to_end_seconds"] / baseline["research_end_to_end_seconds"]), 2)
        records[name]["tts_reduction_percent"] = round(100 * (1 - records[name]["tts_step_seconds"] / records["baseline"]["tts_step_seconds"]), 2)
(OUT / "comparison.json").write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n")

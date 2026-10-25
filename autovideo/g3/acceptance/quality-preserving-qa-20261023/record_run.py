"""Verify the normal Research -> AutoVideo -> Research run and immutable baseline."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
RESEARCH = ROOT.parent / "AI_research/Base_CodingCLi"


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def seconds(start, end):
    def parse(value):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
    return round((parse(end) - parse(start)).total_seconds(), 3)


def connection(path):
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


research = connection(RESEARCH / "state/video.db")
auto = connection(ROOT / "g3/data/jobs.sqlite3")
results = {}
roots = {}
run = read(OUT / "run.json")
for name, render_id in (("before", "7c13c419137b49449130b83d25f41030"), ("after", run.get("render_id", run.get("id")))):
    render = dict(research.execute("SELECT * FROM renders WHERE id=?", (render_id,)).fetchone())
    assert render["status"] == "COMPLETED", (name, render["status"], render["error_message"])
    attempt = dict(auto.execute("SELECT * FROM attempts WHERE job_id=? ORDER BY attempt_no DESC LIMIT 1",
                                (render["autovideo_job_id"],)).fetchone())
    assert attempt["status"] == "COMPLETED"
    root = ROOT / "g3/data" / attempt["workspace_rel_path"] / "g2-run"
    roots[name] = root
    downloads = RESEARCH / "state/video_renders" / render_id
    registered = {item["name"]: dict(item) for item in auto.execute(
        "SELECT * FROM artifacts WHERE job_id=? AND attempt_no=?", (render["autovideo_job_id"], attempt["attempt_no"]))}
    items = json.loads(render["artifacts"])
    for item in items:
        path = downloads / item["name"]
        assert path.stat().st_size == item["bytes"] == registered[path.name]["bytes"]
        assert digest(path) == item["sha256"] == registered[path.name]["sha256"]
    qa, package, tts, voice = [read(downloads / filename) for filename in
                              ("qa.json", "package-qa.json", "tts-metrics.json", "voice-report.json")]
    assert qa["status"] == package["status"] == read(downloads / "narration-check.json")["status"] == "PASS"
    stages = {key: float(value) for key, value in re.findall(r"STEP ([a-z0-9-]+): PASS in ([0-9.]+)s",
                                                           (root.parent / "worker.log").read_text())}
    results[name] = {"render_id": render_id, "autovideo_job_id": render["autovideo_job_id"],
                     "source_sha256": render["source_sha256"], "status": render["status"],
                     "execution_seconds": seconds(attempt["started_at"], attempt["completed_at"]),
                     "end_to_end_seconds": seconds(render["created_at"], render["completed_at"]),
                     "stages": stages, "transfer": json.loads(render["timings"]),
                     "cache": voice.get("cache"), "pcm_sha256": tts["audio_sha256"],
                     "packaged_video_sha256": digest(downloads / "video.mp4"),
                     "content_qa": qa, "package_qa": package, "verified_downloads": len(items)}
    folder = OUT / name
    folder.mkdir(exist_ok=True)
    for filename in ("qa.json", "package-qa.json", "tts-metrics.json", "voice-report.json", "narration-check.json", "execution-log.txt"):
        shutil.copy2(downloads / filename, folder / filename)
    (folder / "artifacts.json").write_text(json.dumps(items, indent=2) + "\n")
    (folder / "request.json").write_text(json.dumps(json.loads(render["request"]), ensure_ascii=False, indent=2) + "\n")
for field in ("source_sha256", "pcm_sha256", "packaged_video_sha256"):
    assert results["before"][field] == results["after"][field], field
equal_files = ("artifacts/package/captions.srt", "artifacts/package/chapters.json",
               "artifacts/package/contact-sheet.jpg", "fixtures/assets/communications_noise.wav")
results["identical_artifacts"] = {}
for filename in equal_files:
    value = digest(roots["before"] / filename)
    assert value == digest(roots["after"] / filename), filename
    results["identical_artifacts"][filename] = value
assert results["before"]["package_qa"] == results["after"]["package_qa"]
for field in ("execution_seconds", "end_to_end_seconds"):
    results[f"{field}_reduction_percent"] = round(100 * (1 - results["after"][field] / results["before"][field]), 2)
results["interpretation"] = "Same approved v1 and complete chapter cache in both runs; no fresh-TTS or batch throughput claim. Packaged MP4 is byte-identical, including every video/audio frame."
(OUT / "run-comparison.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
(OUT / "run.json").write_text(json.dumps({key: results["after"][key] for key in
                                        ("render_id", "autovideo_job_id", "status", "source_sha256")}, indent=2) + "\n")
print(json.dumps({name: {key: result[key] for key in ("execution_seconds", "end_to_end_seconds", "stages", "verified_downloads")}
                  for name, result in results.items() if name in ("before", "after")}, indent=2))

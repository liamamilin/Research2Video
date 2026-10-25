"""Read-only verification of the real two-service fast-card handoff.

Run from the repository root. Runtime media stays under the ignored data dirs;
only small QA reports and hashes are copied into this evidence directory.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from g2.production_plan import verify

EVIDENCE = Path(__file__).resolve().parent
RESEARCH = ROOT.parent / "AI_research/Base_CodingCLi"
JOB_ID = "2e6d0b88-3a2e-42c0-b67c-9b30af4e7c4d"
RENDER_ID = "768de6dbf8b6465eaf38a946cffbceee"


def read(path: Path) -> dict:
    return json.loads(path.read_text())


def connection(path: Path) -> sqlite3.Connection:
    result = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    result.row_factory = sqlite3.Row
    return result


def seconds(start: str, end: str) -> float:
    def parse(value: str) -> datetime:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
    return round((parse(end) - parse(start)).total_seconds(), 3)


def main() -> None:
    auto = connection(ROOT / "g3/data/jobs.sqlite3")
    research = connection(RESEARCH / "state/video.db")
    # Table identity is fixed by the application schema, never caller input.
    render = dict(research.execute("SELECT * FROM renders WHERE id=?", (RENDER_ID,)).fetchone())
    attempt = dict(auto.execute("SELECT * FROM attempts WHERE job_id=? AND attempt_no=1", (JOB_ID,)).fetchone())
    assert render["status"] == attempt["status"] == "COMPLETED"
    request = read(ROOT / f"g3/data/jobs/{JOB_ID}/input/input.json")
    plan = verify(request)
    assert plan["source_sha256"] == render["source_sha256"]
    assert plan["executor"]["id"] == "ffmpeg_cards"
    downloads = RESEARCH / f"state/video_renders/{RENDER_ID}"
    assert read(downloads / "production-plan.json") == plan
    source = hashlib.sha256(render["source_text"].encode()).hexdigest()
    assert source == plan["source_sha256"]
    registered = {r["name"]: dict(r) for r in auto.execute(
        "SELECT * FROM artifacts WHERE job_id=? AND attempt_no=1", (JOB_ID,))}
    hashes = []
    for artifact in json.loads(render["artifacts"]):
        path = downloads / artifact["name"]
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        assert path.stat().st_size == artifact["bytes"] == registered[path.name]["bytes"]
        assert sha == artifact["sha256"] == registered[path.name]["sha256"]
        hashes.append({"name": path.name, "bytes": path.stat().st_size, "sha256": sha})
    qa = read(downloads / "qa.json")
    package = read(downloads / "package-qa.json")
    narration = read(downloads / "narration-check.json")
    execution = read(downloads / "production-execution.json")
    assert qa["status"] == package["status"] == narration["status"] == "PASS"
    assert qa["video"]["full_decode"] == qa["audio"]["full_decode"] == "PASS"
    assert qa["llm_call_count"] == 0 and qa["real_tts"] is True
    assert qa["fast_cards"]["caption_cues_checked"] == narration["segments"] == 31
    baseline = read(ROOT / "g3/acceptance/generation-speed-20261023/topic-digest-rerun/comparison.json")
    assert baseline["new"]["source_sha256"] == source
    execution_seconds = seconds(attempt["started_at"], attempt["completed_at"])
    end_to_end = seconds(render["created_at"], render["completed_at"])
    report = {
        "status": "PASS", "script_id": render["script_id"], "version": render["version"],
        "render_id": RENDER_ID, "autovideo_job_id": JOB_ID, "source_sha256": source,
        "production_plan_sha256": plan["sha256"], "verified_downloads": len(hashes),
        "execution_seconds": execution_seconds, "research_end_to_end_seconds": end_to_end,
        "engine_to_research_complete_gap_seconds": round(end_to_end - execution_seconds, 3),
        "previous_standard_execution_seconds": baseline["new"]["execution_seconds"],
        "execution_reduction_percent": round(100 * (1 - execution_seconds / baseline["new"]["execution_seconds"]), 2),
        "previous_research_end_to_end_seconds": baseline["research_end_to_end_seconds"]["new"],
        "end_to_end_reduction_percent": round(100 * (1 - end_to_end / baseline["research_end_to_end_seconds"]["new"]), 2),
        "content_seconds": qa["video"]["duration_seconds"],
        "package_seconds": package["checks"]["duration"]["seconds"],
        "pages": execution["page_count"], "caption_cues_checked": qa["fast_cards"]["caption_cues_checked"],
        "tts_correlation": qa["audio"]["decoded_tts_correlation"],
        "llm_calls": qa["llm_call_count"], "encoder": execution["encoder"],
        "interpretation": "Same approved narration and voice, fresh TTS. Different renderer and packaging; not an isolated renderer A/B. End-to-end gap includes polling/recovery/download; no finer bridge timing was recorded. Batch throughput was not measured.",
    }
    for name in ("qa.json", "package-qa.json", "narration-check.json", "production-plan.json",
                 "production-execution.json", "execution-log.txt"):
        shutil.copy2(downloads / name, EVIDENCE / f"live-{name}")
    (EVIDENCE / "live-artifacts.json").write_text(json.dumps(hashes, indent=2) + "\n")
    (EVIDENCE / "live-run.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

"""Read-only evidence for the final normal Research -> AutoVideo run."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
sys.path.insert(0, str(ROOT))
from g2.caption_alignment import verify_plan, srt_text
from g2.card_editorial import validate, constraints
from g2.fast_cards import font, lines

RESEARCH = ROOT.parent / "AI_research/Base_CodingCLi"
RUN = "778449710a1541ec898093f85ec530e3"
BASELINE = ROOT / "g3/data/jobs/d2189a00-ac9b-46a6-a1a1-b5a377b8c25f/attempts/1/g2-run"


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def db(path):
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def parse(value):
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")


rdb = db(RESEARCH / "state/video.db")
adb = db(ROOT / "g3/data/jobs.sqlite3")
render = dict(rdb.execute("SELECT * FROM renders WHERE id=?", (RUN,)).fetchone())
assert render["status"] == "COMPLETED"
job = render["autovideo_job_id"]
attempt = dict(adb.execute("SELECT * FROM attempts WHERE job_id=? ORDER BY attempt_no DESC LIMIT 1", (job,)).fetchone())
root = ROOT / "g3/data" / attempt["workspace_rel_path"] / "g2-run"
downloads = RESEARCH / "state/video_renders" / RUN
registered = {a["name"]: dict(a) for a in adb.execute("SELECT * FROM artifacts WHERE job_id=? AND attempt_no=?", (job, attempt["attempt_no"]))}
items = json.loads(render["artifacts"])
for item in items:
    path = downloads / item["name"]
    assert path.stat().st_size == item["bytes"] == registered[item["name"]]["bytes"]
    assert sha(path) == item["sha256"] == registered[item["name"]]["sha256"]
ir = read(root / "fixtures/video-ir-v0.2.json")
captions = read(downloads / "caption-alignment.json")
verify_plan(ir, captions)
assert (downloads / "content-captions.srt").read_text() == srt_text(captions["cues"])
editorial = read(downloads / "card-editorial.json")
validate({"pages": [{"id": p["id"], "points": p["points"]} for p in editorial["pages"]]}, {"groups": editorial["pages"]})
segments = {s["id"]: s for s in ir["narration"]["segments"]}
for page in editorial["pages"]:
    assert page["text"] == "".join(segments[sid]["text"] for sid in page["segment_ids"])
    assert page["constraints"] == constraints(page["text"])
pcm = root / "fixtures/assets/communications_noise.wav"
assert sha(pcm) == sha(BASELINE / "fixtures/assets/communications_noise.wav")
assert render["source_sha256"] == "14f3287f32a03dabdb1d02d67bf9c9163423864c49b94c002daf2801e966aa29"
qa, package = read(downloads / "qa.json"), read(downloads / "package-qa.json")
assert qa["status"] == package["status"] == read(downloads / "narration-check.json")["status"] == "PASS"


def audio_hash(path):
    return subprocess.run([str(ROOT / "g0/darwin_arm64/ffmpeg"), "-nostdin", "-v", "error", "-i", str(path),
        "-map", "0:a:0", "-f", "hash", "-hash", "sha256", "-"], capture_output=True, text=True, check=True).stdout.strip()


old_audio, new_audio = audio_hash(BASELINE / "artifacts/package/video.mp4"), audio_hash(downloads / "video.mp4")
assert old_audio == new_audio
stages = {key: float(value) for key, value in re.findall(r"STEP ([a-z0-9-]+): PASS in ([0-9.]+)s", (root.parent / "worker.log").read_text())}
fast = read(downloads / "fast-card-plan.json")
split_words = lambda values: any(re.search(r"[A-Za-z]$", a) and re.match(r"[a-z]", b) for a, b in zip(values, values[1:]))
assert not any(split_words(p["body_lines"]) for p in fast["pages"])
assert not any(split_words(lines(c["text"].strip(), font(44), 1540)) for c in captions["cues"])
assert [i + 1 for i, p in enumerate(fast["pages"]) if p["notices"]] == [4, 5]
result = {"status": "PASS", "script_version": 1, "render_id": RUN, "job_id": job,
    "source_sha256": render["source_sha256"], "unchanged_pcm_sha256": sha(pcm), "same_packaged_decoded_audio": new_audio,
    "before_cards": 31, "after_cards": len(fast["pages"]), "before_caption_cues": 31, "after_caption_cues": len(captions["cues"]),
    "point_source_quotes_verified": True, "original_qualifiers_verified": True, "complete_short_caption_coverage": True,
    "no_split_english_words": True, "review_notice_pages": [4, 5], "verified_downloads": len(items),
    "duration_seconds": package["checks"]["duration"]["seconds"],
    "engine_seconds": round((parse(attempt["completed_at"]) - parse(attempt["started_at"])).total_seconds(), 3),
    "end_to_end_seconds": round((parse(render["completed_at"]) - parse(render["created_at"])).total_seconds(), 3),
    "stages": stages, "transfer": json.loads(render["timings"]), "caption_qa": qa["fast_cards"],
    "caption_alignment_seconds": captions["generation_seconds"], "model": captions["model"],
    "packaging": read(downloads / "package.json")["config"],
    "interpretation": "Quality pass over unchanged authored speech. Added screen planning and forced alignment; not an isolated speed benchmark or batch throughput test.",
    "development_failures_preserved": ["5ff70841-7a88-4438-9e2d-b5b59ca7afd6", "8ef8da4e-1bd7-4251-b2a4-003322ce39cd", "2bd645c6-6595-474c-8431-9bccabadce0f"]}
for filename in ("qa.json", "package-qa.json", "narration-check.json", "tts-metrics.json", "voice-report.json", "card-editorial.json", "execution-log.txt"):
    shutil.copy2(downloads / filename, OUT / filename)
(OUT / "artifacts.json").write_text(json.dumps(items, indent=2) + "\n")
(OUT / "verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({k: v for k, v in result.items() if k not in ("transfer", "packaging")}, ensure_ascii=False, indent=2))

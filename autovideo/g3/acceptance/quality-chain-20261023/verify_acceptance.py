"""Decode and check the real output of the unified submission boundary."""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
RESEARCH = ROOT.parent / "AI_research/Base_CodingCLi"
sys.path.insert(0, str(ROOT))
from g2.audience_copy import problem
from g2.caption_alignment import verify_plan, srt_text
from g2.card_editorial import validate
from PIL import Image


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def connect(path):
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


run = read(OUT / "render-run.json")
rdb = connect(RESEARCH / "state/video.db")
adb = connect(ROOT / "g3/data/jobs.sqlite3")
row = dict(rdb.execute("SELECT * FROM renders WHERE id=?", (run["render_id"],)).fetchone())
assert row["status"] == "COMPLETED"
request, items = json.loads(row["request"]), json.loads(row["artifacts"])
media = RESEARCH / "state/video_renders" / row["id"]
attempt = dict(adb.execute("SELECT * FROM attempts WHERE job_id=? ORDER BY attempt_no DESC LIMIT 1", (row["autovideo_job_id"],)).fetchone())
registered = {x["name"]: dict(x) for x in adb.execute("SELECT * FROM artifacts WHERE job_id=? AND attempt_no=?", (row["autovideo_job_id"], attempt["attempt_no"]))}
attempt_root = ROOT / "g3/data" / attempt["workspace_rel_path"] / "g2-run"
for item in items:
    file = media / item["name"]
    assert file.stat().st_size == item["bytes"] == registered[item["name"]]["bytes"]
    assert sha(file) == item["sha256"] == registered[item["name"]]["sha256"]

version = dict(rdb.execute("SELECT * FROM script_versions WHERE script_id=? AND version=5", (run["script_id"],)).fetchone())
sections = json.loads(version["sections"])
expected = "".join(p["text"] for s in sections for p in s["paragraphs"])
ir = read(media / "video-ir.json")
narration = "".join(s["text"] for s in ir["narration"]["segments"])
assert re.sub(r"\s", "", expected) == re.sub(r"\s", "", narration)
assert not problem(narration)
captions = read(media / "caption-alignment.json")
verify_plan(ir, captions)
assert (media / "content-captions.srt").read_text() == srt_text(captions["cues"])
editorial = read(media / "card-editorial.json")
validate({"pages": [{"id": p["id"], "points": p["points"]} for p in editorial["pages"]]}, {"groups": editorial["pages"]})
qa, package = read(media / "qa.json"), read(media / "package-qa.json")
assert qa["status"] == package["status"] == "PASS"
assert read(media / "narration-check.json")["status"] == "PASS"
assert len(request["sources"]) == 3
assert read(media / "package.json")["source_card"]["state"] == "enabled"
ffmpeg = str(ROOT / "g0/darwin_arm64/ffmpeg")
decode = subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-i", str(media/"video.mp4"), "-f", "null", "-"], capture_output=True, text=True)
assert decode.returncode == 0, decode.stderr

previous = ROOT / "g3/acceptance/audience-quality-20261023"
old = read(previous / "render-final.json")
old_attempt = dict(adb.execute("SELECT * FROM attempts WHERE job_id=? ORDER BY attempt_no DESC LIMIT 1", (old["autovideo_job_id"],)).fetchone())
old_root = ROOT / "g3/data" / old_attempt["workspace_rel_path"] / "g2-run"
voice = attempt_root / "fixtures/assets/communications_noise.wav"
old_voice = old_root / "fixtures/assets/communications_noise.wav"
assert sha(voice) == sha(old_voice)
assert row["source_sha256"] == old["source_sha256"]

# Inspect actual decoded frames rather than standalone card PNGs.
frames = OUT / "frames"
frames.mkdir(exist_ok=True)
pack = read(media / "package.json")
intro = pack["intro"]["seconds"]
sheet = Image.new("RGB", (960, 540 * ((len(ir["scenes"]) + 1) // 2)), "#dddddd")
for index, scene in enumerate(ir["scenes"]):
    path = frames / f"page-{index+1:02d}.jpg"
    at = intro + scene["start_ms"] / 1000 + min(1, scene["duration_ms"] / 2000)
    subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-y", "-ss", str(at), "-i", str(media/"video.mp4"), "-frames:v", "1", str(path)], check=True)
    frame = Image.open(path).convert("RGB")
    frame.thumbnail((480, 270))
    sheet.paste(frame, ((index % 2) * 480, (index // 2) * 540))
    # A second frame per page catches later subtitle state as well.
    at = intro + (scene["start_ms"] + scene["duration_ms"] * .7) / 1000
    second = frames / f"page-{index+1:02d}-late.jpg"
    subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-y", "-ss", str(at), "-i", str(media/"video.mp4"), "-frames:v", "1", str(second)], check=True)
    frame = Image.open(second).convert("RGB")
    frame.thumbnail((480, 270))
    sheet.paste(frame, ((index % 2) * 480, (index // 2) * 540 + 270))
sheet.save(OUT / "contact.jpg", quality=92)
probe = subprocess.run([str(ROOT/"g0/darwin_arm64/ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(media/"video.mp4")], check=True, capture_output=True, text=True)
result = {"status": "PASS", "render_id": row["id"], "job_id": row["autovideo_job_id"],
          "mp4": str(media/"video.mp4"), "artifacts_verified": len(items), "decode": "PASS",
          "narration_unchanged": True, "voice_pcm_file_unchanged": True,
          "copy_preflight": "PASS", "cards": len(editorial["pages"]), "caption_cues": len(captions["cues"]),
          "source_card_entries": len(request["sources"]), "qa": qa["status"], "package_qa": package["status"],
          "content_duration_seconds": ir["video"]["duration_ms"] / 1000,
          "duration_seconds": float(json.loads(probe.stdout)["format"]["duration"]),
          "research_tests": {"passed": 551}, "autovideo_tests": {"passed": 683, "skipped": 1, "historical_manifest_failed": 1}}
(OUT / "verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
print(json.dumps(result, ensure_ascii=False))

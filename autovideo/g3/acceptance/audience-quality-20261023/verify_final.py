"""Read-only verification of the final normal Research -> AutoVideo run."""
from datetime import datetime
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
from g2.caption_alignment import verify_plan, srt_text
from g2.card_editorial import validate
from PIL import Image, ImageDraw


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def connect(path):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


run = read(OUT / "render-run.json")
rdb = connect(RESEARCH / "state/video.db")
adb = connect(ROOT / "g3/data/jobs.sqlite3")
render = dict(rdb.execute("SELECT * FROM renders WHERE id=?", (run["render_id"],)).fetchone())
assert render["status"] == "COMPLETED" and render["version"] == 5
request = json.loads(render["request"])
assert len(request["sources"]) == 3
assert any("oppo_enco_x4_unboxing_and_handson" in source["url"] for source in request["sources"])
job = render["autovideo_job_id"]
attempt = dict(adb.execute("SELECT * FROM attempts WHERE job_id=? ORDER BY attempt_no DESC LIMIT 1", (job,)).fetchone())
root = ROOT / "g3/data" / attempt["workspace_rel_path"] / "g2-run"
media = RESEARCH / "state/video_renders" / render["id"]
registered = {row["name"]: dict(row) for row in adb.execute("SELECT * FROM artifacts WHERE job_id=? AND attempt_no=?", (job, attempt["attempt_no"]))}
items = json.loads(render["artifacts"])
for item in items:
    file = media / item["name"]
    assert file.stat().st_size == item["bytes"] == registered[item["name"]]["bytes"]
    assert sha(file) == item["sha256"] == registered[item["name"]]["sha256"]

version = dict(rdb.execute("SELECT * FROM script_versions WHERE script_id=? AND version=5", (run["script_id"],)).fetchone())
sections, meta = json.loads(version["sections"]), json.loads(version["meta"])
assert meta["checks"]["summary"]["passed"] and meta["checks"]["audits_complete"]
assert not any(i["audit"] == "fact" for i in meta["checks"]["issues"])
assert all(p["evidence"] == "CITED" and p["fact_verification"]["status"] == "verified" for s in sections for p in s["paragraphs"])
assert sha(RESEARCH / meta["report_path"]) == meta["editorial_review"]["source_packet_sha256"]
old = dict(rdb.execute("SELECT * FROM script_versions WHERE script_id=? AND version=1", (run["script_id"],)).fetchone())
original = read(OUT / "original-v1.json")
for field in ("sections", "meta", "length_check"):
    assert json.loads(old[field]) == original[field]
assert sum(p["evidence"] == "NEEDS_REVIEW" for s in original["sections"] for p in s["paragraphs"]) == 2

ir = read(media / "video-ir.json")
narration = "".join(row["text"] for row in ir["narration"]["segments"])
expected = "".join(p["text"] for s in sections for p in s["paragraphs"])
assert re.sub(r"\s+", "", narration) == re.sub(r"\s+", "", expected)
assert not re.search(r"材料|资料未|摘要没|缺少缺点|评测标题", narration)
captions = read(media / "caption-alignment.json")
verify_plan(ir, captions)
assert (media / "content-captions.srt").read_text() == srt_text(captions["cues"])
editorial = read(media / "card-editorial.json")
validate({"pages": [{"id": p["id"], "points": p["points"]} for p in editorial["pages"]]}, {"groups": editorial["pages"]})
fast = read(media / "fast-card-plan.json")
for page, drawn in zip(editorial["pages"], fast["pages"], strict=True):
    labels = [{"point": i+1, "text": p["qualifiers"]} for i, p in enumerate(page["points"]) if p["qualifiers"]]
    assert drawn["qualifiers"] == labels and not drawn["notices"]
    assert all(q in page["text"] for p in page["points"] for q in p["qualifiers"])
    assert not any(re.search(r"肯定|口径与限制|材料", q) for p in page["points"] for q in p["qualifiers"])
    if "28" in drawn["text"]:
        assert "Wi-Fi 网页浏览测试中" in drawn["text"] + "".join(q for p in page["points"] for q in p["qualifiers"])
qa, package, narration_check = read(media / "qa.json"), read(media / "package-qa.json"), read(media / "narration-check.json")
assert qa["status"] == package["status"] == narration_check["status"] == "PASS"
pack = read(media / "package.json")
assert pack["config"]["outro"]["lines"] == ["主要来源见简介"]
assert pack["source_card"]["state"] == "enabled" and pack["source_card"]["seconds"] == 4.5
assert len(pack["packaging"]["source_card"]["sources"]) == 3
ffmpeg = ROOT / "g0/darwin_arm64/ffmpeg"
decode = subprocess.run([str(ffmpeg), "-nostdin", "-v", "error", "-i", str(media / "video.mp4"), "-f", "null", "-"], capture_output=True, text=True)
(OUT / "final-decode.log").write_text(decode.stderr)
assert decode.returncode == 0
first = read(OUT / "render-first.json")
first_attempt = dict(adb.execute("SELECT * FROM attempts WHERE job_id=? ORDER BY attempt_no DESC LIMIT 1", (first["autovideo_job_id"],)).fetchone())
first_root = ROOT / "g3/data" / first_attempt["workspace_rel_path"] / "g2-run"
assert sha(root / "fixtures/assets/communications_noise.wav") == sha(first_root / "fixtures/assets/communications_noise.wav")

# Inspect decoded final MP4 frames, including the source/condition labels.
frames = OUT / "frames"
frames.mkdir(exist_ok=True)
offset = pack["content"]["offset_ms"] / 1000
at = 0
contact = Image.new("RGB", (1280, 380*((len(fast["pages"])+1)//2)), "#dfe5e0")
for i, page in enumerate(fast["pages"]):
    duration = page["frames"] / 30
    position = offset + at + duration / 2
    target = frames / f"page-{i+1:02}.jpg"
    subprocess.run([str(ffmpeg), "-nostdin", "-v", "error", "-y", "-ss", str(position), "-i", str(media / "video.mp4"), "-frames:v", "1", "-q:v", "2", str(target)], check=True)
    contact.paste(Image.open(target).resize((640,360)), ((i%2)*640, (i//2)*380+20))
    ImageDraw.Draw(contact).text(((i%2)*640+10,(i//2)*380+4),f"{page['scene_id']} @ {position:.1f}s",fill="#14251c")
    at += duration
contact.save(OUT / "final-contact.jpg", quality=92)
for name in ("source_card", "outro"):
    region = pack[name]
    position = region["offset_ms"] / 1000 + region["seconds"] / 2
    subprocess.run([str(ffmpeg), "-nostdin", "-v", "error", "-y", "-ss", str(position), "-i", str(media / "video.mp4"), "-frames:v", "1", "-q:v", "2", str(OUT / f"{name}.jpg")], check=True)
stages = {k: float(v) for k,v in re.findall(r"STEP ([a-z0-9-]+): PASS in ([0-9.]+)s", (root.parent/"worker.log").read_text())}
result = {"status":"PASS", "script_version":5,"render_id":render["id"],"job_id":job,
    "video_path":str(media/"video.mp4"),"source_sha256":render["source_sha256"],
    "cards":len(fast["pages"]),"caption_cues":len(captions["cues"]),"narration_segments":len(ir["narration"]["segments"]),
    "duration_seconds":pack["total_seconds"],"stages":stages,"verified_downloads":len(items),
    "qa":qa,"package_qa":package,"all_current_paragraphs_explicitly_verified":True,
    "old_v1_and_review_marks_unchanged":True,"same_pcm_as_first_v5_render":True,
    "no_research_notes_in_narration":True,"point_qualifiers_exact_and_scoped":True,
    "no_repeated_metric_heading_when_already_on_card":True,"full_decode":"PASS", "editorial_advisories_retained":len(meta["checks"]["issues"]),
    "packaging_verified":{"intro_seconds":pack["intro"]["seconds"],"outro_seconds":pack["outro"]["seconds"],
        "source_card_seconds":pack["source_card"]["seconds"],"source_card_entries":len(request["sources"])},
    "transfer":json.loads(render.get("timings") or "{}")}
(OUT/"verification.json").write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps({k:v for k,v in result.items() if k not in ('qa','package_qa','transfer')},ensure_ascii=False))

"""Render one reviewed acceptance manuscript through the normal executor."""
import json
import os
from pathlib import Path
import sys
import time

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
RESEARCH = ROOT.parent / "AI_research/Base_CodingCLi"
os.chdir(RESEARCH)
sys.path.insert(0, str(RESEARCH))
from core.video import store
from core.video.policy import is_core_issue
from web.services import video_executor, video_render, video_workflow

key = sys.argv[1]
case = next(x for x in json.loads((OUT / "batch.json").read_text())["cases"] if x["key"] == key)
script = store.get_script(case["script_id"])
assert not script.get("stage"), "writing/audits are still active"
version = store.get_version(script["id"], script["current_version"])
checks = video_workflow.rule_checks(version, video_executor.column_of(script))
assert checks.get("model_ran") and checks["summary"]["passed"], checks["summary"]
assert not any(is_core_issue(i) for i in checks["issues"]), "unresolved factual or copy issue"
assert not any(p.get("evidence") == "NEEDS_REVIEW" for s in version["sections"] for p in s["paragraphs"])
runs = OUT / "runs"
runs.mkdir(exist_ok=True)
record = runs / (key + "-start.json")
started = time.monotonic()
if record.exists():
    existing = json.loads(record.read_text())
    assert existing["version"] == version["version"]
    render = store.get_render(existing["render_id"])
    assert render["status"] not in ("FAILED", "CANCELLED"), render.get("error_message")
    video_render.start(render["id"])
else:
    if script.get("approved_version") != version["version"] or script["status"] != "APPROVED":
        video_executor.approve(script["id"], version["version"], "用户授权·矩阵配方质量验收")
    render = video_executor.render(script["id"], "用户授权·矩阵配方质量验收", production_mode=case["mode"])
    record.write_text(json.dumps({"case":key,"script_id":script["id"],"version":version["version"],"render_id":render["id"]},ensure_ascii=False,indent=2))
print(json.dumps({"case":key,"render_id":render["id"],"mode":case["mode"]}),flush=True)
last = None
while True:
    row = store.get_render(render["id"])
    state = (row["status"],row.get("active_stage"))
    if state != last:
        print(json.dumps({"case":key,"status":state[0],"stage":state[1],"seconds":round(time.monotonic()-started)},ensure_ascii=False),flush=True)
        last = state
    if row["status"] in ("COMPLETED","FAILED","CANCELLED"):
        break
    time.sleep(2)
(runs/(key+"-final.json")).write_text(json.dumps(row,ensure_ascii=False,indent=2))
assert row["status"] == "COMPLETED",row.get("error_message")
print(json.dumps({"case":key,"complete":True,"job_id":row["autovideo_job_id"],"seconds":round(time.monotonic()-started,2)},ensure_ascii=False),flush=True)

"""Exercise the normal Research submission with the already authorized v5."""
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
from web.services import video_executor, video_workflow

script = store.get_script("e118dc7db5f34c3ba90af0285b4688f5")
assert script["current_version"] == script["approved_version"] == 5 and not script.get("stage")
version = store.get_version(script["id"], 5)
checks = video_workflow.rule_checks(version, video_executor.column_of(script))
assert checks["summary"]["passed"] and checks["audits_complete"]
assert not any(p.get("evidence") == "NEEDS_REVIEW" for s in version["sections"] for p in s["paragraphs"])
started = time.monotonic()
render = video_executor.render(script["id"], "质量链路统一验收", voice_id="voxcpm_anchor", production_mode="fast")
(OUT / "render-run.json").write_text(json.dumps({"render_id": render["id"], "script_id": script["id"], "version": 5}, indent=2))
print("Render " + render["id"], flush=True)
previous = None
while True:
    row = store.get_render(render["id"])
    state = (row["status"], row.get("active_stage"))
    if state != previous:
        print(json.dumps({"status": state[0], "stage": state[1], "elapsed_seconds": round(time.monotonic() - started, 1)}), flush=True)
        previous = state
    if row["status"] in ("COMPLETED", "FAILED", "CANCELLED"):
        break
    time.sleep(2)
(OUT / "render-final.json").write_text(json.dumps(row, ensure_ascii=False, indent=2))
assert row["status"] == "COMPLETED", row.get("error_message")
print(json.dumps({"render_id": row["id"], "job_id": row["autovideo_job_id"],
                  "seconds": round(time.monotonic() - started, 3)}), flush=True)

"""Build an illustrated-explainer render request with the real AREC workflow builder and submit it
to the isolated AutoVideo trial instance. Writes nothing to AREC's database (same idea as the
A/B preview's build_request): the approved version is supplied in memory.

Usage (from AREC root): PYTHONPATH=. python3 workflow_render.py <script.json> <matrix> <title> <out_dir>
"""
import copy
import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(".env")

from core.video.autovideo import AutoVideoClient
from web.services import video_render, video_visuals, opening_card

sections_path, matrix, title, out_dir = sys.argv[1:5]
out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
if (out / "request.json").exists():
    sys.exit(f"{out} already used")
sections = json.loads(Path(sections_path).read_text(encoding="utf-8"))
version = {"version": 1, "title": title, "sections": sections, "meta": {}, "length_check": {}}
import uuid
script = {"id": uuid.uuid4().hex, "column_key": "illustrated_explainer", "approved_version": 1,
          "topic": {"title": title, "fields": {"核心角度": title}}, "production": "", "source_matrix_id": matrix,
          "content_context": {}, "status": "APPROVED"}

real_get_version = video_render.store.get_version
video_render.store.get_version = lambda sid, no: copy.deepcopy(version) if sid == script["id"] else real_get_version(sid, no)

client = AutoVideoClient("http://127.0.0.1:8013", timeout=120)
video_render.get_client = lambda: client
built = video_render.create(script, record=lambda script_id, version, **kw: {"script_id": script_id, "version": version, **kw})
shadow = {"id": "trial-" + out.name, "script_id": built["script_id"], "request": built["request"],
          "visuals": built.get("visuals") or {}, "source_text": built["source_text"]}
payload, _ = video_visuals.prepare(shadow, client)
shadow.update(version=built.get("version"), title=built.get("title") or "")
payload, _ = opening_card.apply(payload, shadow, built.get("context"), out / "opening-copy.json")
(out / "request.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
if len(sys.argv) > 5 and sys.argv[5] == "dry":
    (out / "request.json").rename(out / "request-dry.json"); sys.exit(0)
job = client.create_job(payload)
print("job", job["job_id"], flush=True)
(out / "job.json").write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
while True:
    status = client.get_job(job["job_id"])
    if status["status"] in ("COMPLETED", "FAILED", "CANCELLED"):
        break
    time.sleep(10)
print(status["status"], status.get("error_code"), status.get("error_message"), flush=True)
(out / "final-status.json").write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")

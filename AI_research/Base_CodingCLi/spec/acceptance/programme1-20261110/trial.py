"""One trial video of programme 1「同一笔钱，怎么选」(11-10): the real T05 item, writer and render builder, nothing
written to AREC's database (so it cannot be approved or auto-published).

Usage (from AREC root): PYTHONPATH=. python3 spec/acceptance/programme1-20261110/trial.py <out_dir>
Writes script.json (title, sections, next episode), request.json, job.json and final-status.json into <out_dir>.
"""
import copy
import json
import sys
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(".env")

from core.video import content_columns, excerpt, production, store, topic_store, writer, polish
from core.video import columns as video_columns
from core.video.autovideo import AutoVideoClient
from web.services import opening_card, video_render, video_visuals
from web.services.video_workflow import _llm_configs
from web.settings import get_settings

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
settings = get_settings()
config = production.get(settings.paths.config_dir, settings.paths.jobs_dir, "chatgpt_t05_comparison")
source = topic_store.resolve(settings.paths.jobs_dir, config.source)
day, path = topic_store.report_files(source, ".")[0]
_, topics = topic_store.read_report(source, path, ".")
item, following = topics[0].as_dict(), topics[1].as_dict()
report = open(path, encoding="utf-8").read()
material = excerpt.item_excerpt(report, item["title"]) or report

context = content_columns.snapshot(settings.paths.config_dir, config.key, config.job)
column = content_columns.apply_script_context(video_columns.get(settings.paths.config_dir, config.recipe), context)
# as a series batch would give it (selection.next_episode): the next item of the same report
topic = {**item, "next_episode": following["title"]}
llm, _ = _llm_configs(None, column)
written = writer.write_for_column(column=column, content_context=context, topic=topic, report=material,
                                  citation_check=None, llm_config=llm)
polish.apply(written, llm, greeting=None, voice=column.voice_id or None)
(out / "script.json").write_text(json.dumps({"report": path, "item": item["title"], "next_episode": following["title"],
                                             "title": written.title, "sections": written.sections,
                                             "length_check": written.length_check}, ensure_ascii=False, indent=1),
                                 encoding="utf-8")
print("written", written.title, written.length_check.get("estimated_seconds"), flush=True)

version = {"version": 1, "title": written.title, "sections": written.sections, "meta": written.meta,
           "length_check": written.length_check}
script = {"id": uuid.uuid4().hex, "column_key": column.key, "approved_version": 1, "topic": topic,
          "production": config.key, "source_matrix_id": "chatgpt_acquisition", "content_context": context,
          "status": "APPROVED"}
real_get_version = store.get_version
video_render.store.get_version = lambda sid, no: copy.deepcopy(version) if sid == script["id"] else real_get_version(sid, no)
client = AutoVideoClient("http://127.0.0.1:8011", timeout=120)
video_render.get_client = lambda: client
built = video_render.create(script, record=lambda script_id, version, **kw: {"script_id": script_id, "version": version, **kw})
shadow = {"id": "trial-" + out.name, "script_id": built["script_id"], "request": built["request"],
          "visuals": built.get("visuals") or {}, "source_text": built["source_text"]}
payload, _ = video_visuals.prepare(shadow, client)
shadow.update(version=built.get("version"), title=built.get("title") or "")
payload, _ = opening_card.apply(payload, shadow, built.get("context"), out / "opening-copy.json")
(out / "request.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
job = client.create_job(payload)
print("job", job["job_id"], flush=True)
(out / "job.json").write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
while True:
    status = client.get_job(job["job_id"])
    if status["status"] in ("COMPLETED", "FAILED", "CANCELLED"):
        break
    time.sleep(15)
print(status["status"], status.get("error_code"), status.get("error_message"), flush=True)
(out / "final-status.json").write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")

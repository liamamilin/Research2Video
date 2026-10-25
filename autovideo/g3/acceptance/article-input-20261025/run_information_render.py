import json,os,sys,time
from pathlib import Path
OUT=Path(__file__).resolve().parent
RESEARCH=OUT.parents[3]/"AI_research/Base_CodingCLi"
os.chdir(RESEARCH);sys.path.insert(0,str(RESEARCH))
from core.video import store
from web.services import video_executor
sid=json.loads((OUT/"information-script.json").read_text())["id"]
s=store.get_script(sid)
video_executor.approve(sid,s["current_version"],"admin")
r=video_executor.render(sid,"admin")
(OUT/"information-render-start.json").write_text(json.dumps(r,ensure_ascii=False,indent=2)+"\n")
print(json.dumps({k:r.get(k) for k in ("id","status","autovideo_job_id")}),flush=True)
last=None
while True:
 r=store.get_render(r["id"]);state=(r["status"],r.get("active_stage"))
 if state!=last:print(json.dumps({k:r.get(k) for k in ("id","status","active_stage","autovideo_job_id","error_message")},ensure_ascii=False),flush=True);last=state
 if r["status"] in ("COMPLETED","FAILED"):
  (OUT/"information-render-final.json").write_text(json.dumps(r,ensure_ascii=False,indent=2)+"\n");break
 time.sleep(4)
assert r["status"]=="COMPLETED",r.get("error_message")

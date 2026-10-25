import os,sys,json,signal,time,subprocess,urllib.request
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
from core.video import store
from web.settings import get_settings
OUT=Path(__file__).resolve().parent
state=Path(get_settings().paths.state_dir)
with store.connect() as c:
 active=[dict(r) for r in c.execute("SELECT id,status,stage FROM scripts WHERE status IN ('WRITING','RESEARCHING')").fetchall()]
 assert not active,active
for p in state.glob("*.json"):
 try:d=json.loads(p.read_text())
 except (OSError,ValueError):continue
 assert not (isinstance(d,dict) and d.get("status")=="running"),p.name
meta=json.loads((state/"app_runtime.json").read_text());pid=meta["pid"]
cmd=subprocess.check_output(["ps","-p",str(pid),"-o","command="],text=True)
assert "run_web.py" in cmd and "serve" in cmd,cmd
print(json.dumps({"restarting_pid":pid,"script_work":0,"research_work":0}),flush=True)
os.kill(pid,signal.SIGTERM)
started=time.monotonic();health=0;last=None
while time.monotonic()-started<180:
 try:
  with urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8775/",headers={"Accept":"text/html"}),timeout=3) as r:health=r.status
  current=json.loads((state/"app_runtime.json").read_text())
  with urllib.request.urlopen("http://127.0.0.1:8766/api/live",timeout=3) as r:live=r.status
  if health==200 and live==200 and current["pid"]!=pid:
   record={"before_pid":pid,"after_pid":current["pid"],"health":live,"started":current["started"],"instance_id":current["instance_id"],"no_authoring_work_interrupted":True,"autovideo_worker_restarted":False}
   assert current["started"]>max(Path(f).stat().st_mtime for f in ["core/video/articles.py","core/video/article_writer.py","web/services/video_render.py"])
   (OUT/"backend-activation-final.json").write_text(json.dumps(record,indent=2)+"\n");print(json.dumps(record),flush=True);break
 except Exception as e:
  if type(e).__name__!=last:print(type(e).__name__,flush=True);last=type(e).__name__
 time.sleep(1)
else:raise RuntimeError("Backend did not become healthy in startup window")

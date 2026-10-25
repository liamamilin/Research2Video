import json,os,sys
from pathlib import Path
OUT=Path(__file__).resolve().parent
RESEARCH=OUT.parents[3]/"AI_research/Base_CodingCLi"
os.chdir(RESEARCH);sys.path.insert(0,str(RESEARCH))
from core.video import store
from web.services import video_workflow
sid=json.loads((OUT/"information-script.json").read_text())["id"]
print("Rewriting against unchanged Word source with shared article guidance",flush=True)
result=video_workflow.run_rewrite(sid,started_by="admin")
s=store.get_script(sid);v=store.get_version(sid,s["current_version"])
(OUT/f"information-version-v{v['version']}.json").write_text(json.dumps(v,ensure_ascii=False,indent=2)+"\n")
print(json.dumps({"result":result,"status":s["status"],"error":s.get("error"),"version":v["version"],"checks":v["meta"]["checks"]},ensure_ascii=False),flush=True)

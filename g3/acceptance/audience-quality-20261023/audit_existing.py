"""Rerun the normal audit after sandbox connectivity prevented the first run."""
import json
from pathlib import Path
import sys

OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(OUT.parents[3] / "AI_research/Base_CodingCLi"))
from core.video import store, columns, steps
from web.settings import get_settings
from web.services import video_workflow

script = store.get_script("e118dc7db5f34c3ba90af0285b4688f5")
assert script["current_version"] == 2 and not script.get("stage")
column = columns.get(get_settings().paths.config_dir, script["column_key"])
store.update_script(script["id"], stage="观众视角修订后重新审计")
steps.log(script["id"], "audit", "rerun", "首轮网络沙箱连接失败后重新审计 v2，保留失败记录", by="用户授权·质量打磨")
status = video_workflow._resume_audit(script["id"], store.get_script(script["id"]), video_workflow.report_of(script), script["report_path"], column)
version = store.get_version(script["id"], 2)
(OUT / "audited-v2-rerun.json").write_text(json.dumps(version, ensure_ascii=False, indent=2))
print(json.dumps({"status": status, "version": 2, "summary": version["meta"].get("checks", {}).get("summary"),
                  "problems": version["meta"].get("problems")}, ensure_ascii=False))

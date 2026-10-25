"""Normal quality audit of the source-verified revision, with progress-only logs."""
import json
from pathlib import Path
import sys
import time

OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(OUT.parents[3] / "AI_research/Base_CodingCLi"))
from core.llm import LLMClient
from core.video import store, columns, steps
from web.settings import get_settings
from web.services import video_workflow

original_chat = LLMClient.chat


def traced_chat(self, messages, **kwargs):
    label = messages[0]["content"][:22]
    started = time.monotonic()
    print(f"START {label}", flush=True)
    try:
        result = original_chat(self, messages, **kwargs)
    except Exception as exc:
        print(f"FAILED {label} ({time.monotonic()-started:.1f}s): {type(exc).__name__}", flush=True)
        raise
    print(f"DONE {label} ({time.monotonic()-started:.1f}s)", flush=True)
    return result


LLMClient.chat = traced_chat
script = store.get_script("e118dc7db5f34c3ba90af0285b4688f5")
number = int(sys.argv[1]) if len(sys.argv) > 1 else 3
assert script["current_version"] == number and not script.get("stage")
column = columns.get(get_settings().paths.config_dir, script["column_key"])
steps.log(script["id"], "audit", "rerun", f"来源核验修订 v{number} 后正常审校，保留全部历史", by="用户授权·质量打磨")
with store.beating(store.touch_script, script["id"]):
    status = video_workflow._resume_audit(script["id"], script, video_workflow.report_of(script), script["report_path"], column)
version = store.get_version(script["id"], number)
(OUT / f"audited-v{number}.json").write_text(json.dumps(version, ensure_ascii=False, indent=2))
print(json.dumps({"status": status, "version": number, "summary": version["meta"].get("checks", {}).get("summary"),
                  "problems": version["meta"].get("problems")}, ensure_ascii=False), flush=True)

"""Create independent acceptance drafts through the existing shared workflow."""
from copy import deepcopy
import hashlib
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
from web.services import video_executor

BATCH = "matrix-recipe-quality-20261023"
CASES = [
    ("ai-flash", "07c8442c5baa4807bf1d32b21e8a543f", "standard", "快讯：硬件支持、官方数字与适用条件"),
    ("industry", "a8ba608f47204a0182ad0f52bf7de28d", "fast", "产业：承诺和落地、研究和产品的区别"),
    ("ai-deep", "c076368cd731433b94f0dacde7ce36a2", "standard", "深度改编：论文自报、泛化和未证实解释"),
    ("ai-report", "4c4e99e98a3449fb9c5f4978b712c534", "fast", "报告速览：任务类型、热度、许可证"),
    ("company", "4c63a878b25b4c96b7f8fa67f0824da0", "fast", "商业：报道预计、计划数量与实际结果"),
    ("science", "f30f9b94c3a345da82069fac22b24b4f", "fast", "科学：研究归属、受试人群和结论强度"),
    ("history", "41fa2d69fc044c188c8e86bfd0aab336", "fast", "历史：传说、学者解释与已证实史实"),
    ("consumer", "e118dc7db5f34c3ba90af0285b4688f5", "fast", "已核验 v5 对照：测试场景、有限体验与未来计划"),
]


def save(data):
    tmp = OUT / "batch.tmp.json"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(OUT / "batch.json")


if (OUT / "batch.json").exists():
    raise SystemExit("Batch already exists; inspect its records instead of making duplicate scripts")
data = {"batch": BATCH, "selection": "8 manuscripts / 6 matrices / 4 existing recipe families",
        "uncovered_recipes": ["point_short", "action_list", "ai-developer-comparison-long", "ai-developer-roundup-long", "top_finding"],
        "cases": []}
snapshots = OUT / "originals"
snapshots.mkdir(exist_ok=True)
for key, original_id, mode, focus in CASES:
    source = store.get_script(original_id)
    assert source and not source.get("stage"), (key, "selected manuscript is busy")
    version = store.get_version(original_id, source["current_version"])
    snapshot = {"script": source, "version": version}
    (snapshots / (key + ".json")).write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))
    data["cases"].append({"key": key, "original_script_id": original_id, "original_version": version["version"],
                          "original_title": version["title"], "recipe": source["column_key"], "production": source.get("production"),
                          "mode": mode, "focus": focus, "source_report": source["report_path"],
                          "state": "CONTROL" if key == "consumer" else "PENDING",
                          **({"script_id": original_id, "version": version["version"]} if key == "consumer" else {})})
save(data)
pending = [item for item in data["cases"] if item["state"] == "PENDING"]
active = []
started = time.monotonic()
while pending or active:
    while pending and len(active) < 2:
        item = pending.pop(0)
        source = store.get_script(item["original_script_id"])
        topic = deepcopy(source["topic"])
        topic.update(from_report=source["report_path"], report_id=source.get("report_id"),
                     parent_script=source["id"], acceptance_batch=BATCH, acceptance_case=item["key"])
        made = video_executor.create(source["column_key"], topic, "多矩阵质量验收")
        if source.get("production"):
            store.update_script(made["id"], production=source["production"])
        item.update(script_id=made["id"], state="WRITING")
        active.append(item)
        save(data)
        print(json.dumps({"case":item["key"],"script_id":made["id"],"event":"create", "report":source["report_path"]},ensure_ascii=False),flush=True)
    for item in list(active):
        script = store.get_script(item["script_id"])
        state = (script["status"], script.get("stage"), script["current_version"])
        if item.get("last_state") != list(state):
            item["last_state"] = list(state)
            print(json.dumps({"case":item["key"],"status":state[0],"stage":state[1],"version":state[2],"elapsed":round(time.monotonic()-started)},ensure_ascii=False),flush=True)
        if script["status"] in ("FAILED", "PLAN_READY") or (script["status"] == "DRAFT" and not script.get("stage")):
            item.update(state=script["status"], version=script["current_version"], error=script.get("error"))
            active.remove(item)
            if script.get("current_version"):
                (OUT / (item["key"] + "-draft.json")).write_text(json.dumps(store.get_version(script["id"], script["current_version"]),ensure_ascii=False,indent=2))
        save(data)
    if pending or active:
        time.sleep(2)
print("Draft stage finished; no acceptance copies were approved or rendered automatically",flush=True)

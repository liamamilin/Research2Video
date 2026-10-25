"""Approve the audited revision under user authorization and use the common render service."""
import hashlib
import json
from pathlib import Path
import sys

OUT = Path(__file__).resolve().parent
RESEARCH = OUT.parents[3] / "AI_research/Base_CodingCLi"
sys.path.insert(0, str(RESEARCH))
from core.video import store, credibility
from web.services import video_executor, video_render

script = store.get_script("e118dc7db5f34c3ba90af0285b4688f5")
assert script["current_version"] == 5 and not script.get("stage")
version = store.get_version(script["id"], 5)
checks = version["meta"]["checks"]
assert checks["summary"]["passed"] and checks["audits_complete"]
assert not any(i["audit"] == "fact" for i in checks["issues"])
assert not any(p["evidence"] == "NEEDS_REVIEW" for s in version["sections"] for p in s["paragraphs"])
assert credibility.label(version)["tier"] == "trusted"

# v4/v5 inherited the v3 packet hash. Correct the metadata transparently, keeping
# that previous hash and every saved audit JSON; narration and findings are unchanged.
for number in (4, 5):
    row = store.get_version(script["id"], number)
    meta = row["meta"]
    review = meta["editorial_review"]
    packet = RESEARCH / meta["report_path"]
    if review.get("metadata_correction"):
        continue
    review.update(base_source_packet_sha256=review["source_packet_sha256"],
                  source_packet_sha256=hashlib.sha256(packet.read_bytes()).hexdigest(),
                  metadata_correction="来源快照修订后更新当前哈希，原哈希保留；正文与审校结论未改动")
    store.update_version_audit(script["id"], number, sections=row["sections"], meta=meta)
version = store.get_version(script["id"], 5)
(OUT / "audited-v5-final.json").write_text(json.dumps(version, ensure_ascii=False, indent=2))
(OUT / "final-script.txt").write_text("\n\n".join([version["title"]] + ["\n".join(["## " + s["title"]] + [p["text"] for p in s["paragraphs"]]) for s in version["sections"]]))
video_executor.approve(script["id"], 5, "用户授权·质量打磨重跑")
render = video_render.create(store.get_script(script["id"]), voice_id="voxcpm_anchor", production_mode="fast", recover_download=True)
from core.video import steps
steps.log(script["id"], "render", "start", "v5：原网页核验与正常审校通过后快速卡片重跑", by="用户授权·质量打磨")
(OUT / "render-run.json").write_text(json.dumps({"render_id": render["id"], "script_id": script["id"], "version": 5}, indent=2))
print("Render started " + render["id"], flush=True)
status = video_render.process(render["id"])
row = store.get_render(render["id"])
(OUT / "render-final.json").write_text(json.dumps(row, ensure_ascii=False, indent=2))
print(json.dumps({"status": status, "render_id": render["id"], "job_id": row["autovideo_job_id"], "error": row.get("error_message")}, ensure_ascii=False), flush=True)
assert status == "COMPLETED"

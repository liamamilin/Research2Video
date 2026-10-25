"""Record local original quality evidence without changing scripts or contacting providers."""
import hashlib
import json
import os
from pathlib import Path
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
RESEARCH = ROOT.parent / "AI_research/Base_CodingCLi"
os.chdir(RESEARCH)
sys.path.insert(0, str(RESEARCH))
from core.video import store
from core.video.audience_copy import findings

data = json.loads((OUT/"batch.json").read_text())
rows = []
consumer_start = OUT/"runs/consumer-start.json"
excluded = {json.loads(consumer_start.read_text())["render_id"]} if consumer_start.exists() else set()
for case in data["cases"]:
    source = json.loads((OUT/"originals"/(case["key"]+".json")).read_text())
    version = source["version"]
    text = "\n".join(p["text"] for s in version["sections"] for p in s["paragraphs"])
    original = store.get_version(case["original_script_id"],case["original_version"])
    assert original == version, "original manuscript changed during acceptance"
    old = next((r for r in store.list_renders(case["original_script_id"])
                if r["status"]=="COMPLETED" and r["version"]==case["original_version"] and r["id"] not in excluded), None)
    row = {"case":case["key"],"title":version["title"],"recipe":case["recipe"],"production":case["production"],
           "original_script_id":case["original_script_id"],"version":case["original_version"],
           "version_sha256":hashlib.sha256(json.dumps(version,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
           "characters":len(text),"pending_review":sum(p.get("evidence")=="NEEDS_REVIEW" for s in version["sections"] for p in s["paragraphs"]),
           "internal_note_findings":findings(text),"render_id":old["id"] if old else None}
    if old:
        folder = RESEARCH/"state/video_renders"/old["id"]
        row["video"] = str(folder/"video.mp4")
        for name in ("video-ir.json","caption-alignment.json","card-editorial.json"):
            path = folder/name
            if not path.exists(): continue
            saved = json.loads(path.read_text())
            if name=="video-ir.json":
                row.update(presentation=saved.get("presentation"), scenes=len(saved["scenes"]),
                           narration_segments=len(saved["narration"]["segments"]),
                           content_seconds=saved["video"]["duration_ms"]/1000)
            elif name=="caption-alignment.json":row["short_caption_cues"]=len(saved["cues"])
            else:row["editorial_pages"]=len(saved["pages"])
    rows.append(row)
(OUT/"baseline.json").write_text(json.dumps(rows,ensure_ascii=False,indent=2))
print(json.dumps([{k:r.get(k) for k in ("case","pending_review","scenes","short_caption_cues")} for r in rows],ensure_ascii=False))

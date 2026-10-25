"""Supply the existing bibliography contract; no narrated fact is added or changed."""
import hashlib
import json
from pathlib import Path
import shutil
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[3] / "AI_research/Base_CodingCLi"
sys.path.insert(0, str(ROOT))
from core import provenance, report_meta
from core.video import store, reports, sources, steps

s = store.get_script("e118dc7db5f34c3ba90af0285b4688f5")
v = store.get_version(s["id"], 5)
assert s["approved_version"] == 5 and v["meta"]["checks"]["summary"]["passed"]
assert not sources.source_cards(v, (ROOT / s["report_path"]).read_text())
urls = v["meta"]["editorial_review"]["verified_source_urls"]
rows = [
    ("Notebookcheck", "Motorola Edge 70 Max 评测", "2026-10-21", "一手评测"),
    ("GSMArena", "Oppo Enco X4 开箱与试用", "2026-10-22", "一手开箱试用"),
    ("GSMArena", "Bose 收购 Skullcandy", "2026-10-22", "媒体报道"),
    ("Notebookcheck", "Acer Swift Spin 14 AI 评测", "2026-10-21", "一手评测"),
    ("Notebookcheck", "Xiaomi 18 Fold 评测", "2026-10-20", "一手评测"),
]
original = (ROOT / s["report_path"]).read_bytes()
table = "\n## 来源清单\n\n| 来源 | 标题 | 日期 | 性质 | 链接 |\n|---|---|---|---|---|\n"
for (publisher,title,date,kind),url in zip(rows,urls,strict=True):
    table += f"| {publisher} | {title} | {date} | {kind} | {url} |\n"
rel = f"state/video_editorial_sources/{s['id']}/verified-v5-with-source-index.txt"
path = ROOT / rel
assert not path.exists()
path.write_bytes(original + table.encode())
assert path.read_bytes().startswith(original)
cards = sources.source_cards(v,path.read_text())
assert len(cards)==3 and len(sources.report_sources(path.read_text()))==5
report_meta.append_record(rel,"consumer/consumer_daily",extra={"citation_check":provenance.check(path.read_text(),urls),
    "verification_method":"same audited facts, added checked bibliography titles and dates",
    "original_report_path":s["report_path"]})
meta=v["meta"]
meta["editorial_review"].update(audited_source_packet_path=s["report_path"],
    audited_source_packet_sha256=hashlib.sha256(original).hexdigest(),
    source_packet_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    source_index_added="补齐已有来源卡需要的来源清单，正文与已审计事实不变；标题和日期逐页核对")
meta["report_path"]=rel
store.update_version_audit(s["id"],5,sections=v["sections"],meta=meta)
reports.attach(s["id"],rel,created_by="用户授权·来源卡验收补齐")
store.update_script(s["id"],report_path=rel)
steps.log(s["id"],"render","quality_review","来源快照缺少来源清单致来源卡未显示；补齐已核对标题与发布日期后正常重跑，原片保留",by="用户授权·质量打磨")
for src,dst in [("render-final.json","render-before-source-card.json"),("render-run.json","render-run-before-source-card.json"),("verification.json","verification-before-source-card.json"),("render.log","render-before-source-card.log")]:
    assert not (OUT/dst).exists()
    shutil.copy2(OUT/src,OUT/dst)
(OUT/"source-index-verification.json").write_text(json.dumps({"same_audited_facts_prefix":True,
    "audited_packet_sha256":hashlib.sha256(original).hexdigest(),"current_packet_sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
    "bibliography":sources.report_sources(path.read_text()),"closing_card_sources":cards},ensure_ascii=False,indent=2))
print(json.dumps({"source_card_entries":len(cards),"bibliography_entries":5,"same_narration":True},ensure_ascii=False))

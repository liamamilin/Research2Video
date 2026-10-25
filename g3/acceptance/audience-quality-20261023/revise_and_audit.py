"""Save a separate audience edit via the normal workflow, then run all audits.

Run from the Research repository; no historical draft/output is overwritten.
"""
import copy
import json
from pathlib import Path
import sys

OUT = Path(__file__).resolve().parent
WORKSPACE = OUT.parents[3]
sys.path.insert(0, str(WORKSPACE / "AI_research/Base_CodingCLi"))
from core.video import store, columns, steps
from web.settings import get_settings
from web.services import video_workflow

SCRIPT = "e118dc7db5f34c3ba90af0285b4688f5"
s = store.get_script(SCRIPT)
assert s["current_version"] == 1 and not s.get("stage"), "Keep concurrent edits; inspect before rerunning this script"
v1 = store.get_version(SCRIPT, 1)
(OUT / "original-v1.json").write_text(json.dumps(v1, ensure_ascii=False, indent=2))
sections = copy.deepcopy(v1["sections"])
copy_by_section = [
    ["这期看五件新品和行业变化。一款大电池手机，一款降噪耳机，以及音频公司 Bose 收购音频品牌 Skullcandy。还有一台用 ARM 架构处理器的可翻转笔记本，和一款宽幅折叠手机。"],
    ["Motorola Edge 70 Max 是一款围绕大电池设计、主打旗舰功能和续航的手机。Notebookcheck 的评测肯定了它的续航表现，也认为大电池没有让机身显得笨重。评测还给出了价格较低的评价。"],
    ["Oppo Enco X4 是 Oppo 推出的旗舰降噪耳机，已在国际市场首发。官方标称采用 Dynaudio 调音，Dynaudio 是音频品牌。GSMArena 开箱时看到，包装里有耳机，以及 S、M、L 三种硅胶耳套。",
     "开箱能展示包装内容和功能定位，不能据此判断实际降噪、音质和续航。"],
    ["据媒体 GSMArena 报道，音频公司 Bose Corporation 已收购生活方式音频品牌 Skullcandy。双方在联合新闻稿里说，未来产品会结合 Bose 的音频专长，以及 Skullcandy 的设计和社群连接。",
     "新闻稿称，Skullcandy 将保留在 Bose 的高端消费音频产品线中，并继续以 Park City, Utah 为基地。未来产品的说法属于双方的规划，不能据此认定新品已经推出或价格已经改变。"],
    ["Acer Swift Spin 14 AI 是可翻转笔记本。它采用 Snapdragon for Windows 方案，也就是 Windows 上的 ARM 架构处理器。Notebookcheck 的评测对这一方案的性能给出积极评价，同时提醒价格较高。"],
    ["Xiaomi 18 Fold 是 Xiaomi 推出的宽幅折叠手机，配两块 OLED 显示屏。Notebookcheck 的评测认为，宽幅形态、明亮的屏幕和强大硬件是它的看点。这里讲的是形态和屏幕的评价，不能据此认定相机或续航也更好。"],
    ["五件变化里，最值得继续关注 Motorola Edge 70 Max，重点是续航和体积能否兼顾。Notebookcheck 的评价提供了一个线索，具体选购仍应结合实际续航测试和当地售价。"],
]
changes = []
for si, (section, texts) in enumerate(zip(sections, copy_by_section, strict=True)):
    original = section["paragraphs"]
    section["paragraphs"] = []
    for pi, text in enumerate(texts):
        para = copy.deepcopy(original[min(pi, len(original)-1)])
        para.update(text=text, note="观众视角修订：研究缺口留在审校记录，保留具体来源与事实边界")
        section["paragraphs"].append(para)
    changes.append({"section": section["title"], "before": [p["text"] for p in original], "after": texts})
notes = {
    "base_version": 1,
    "reason": "用户授权：消除内部研究备注与模糊指代；不凭摘要缺项断言来源缺项；保留事实限定",
    "research_gaps": [
        "手机摘要未收录量化续航、售价地区和同条件比较基线；不再泛指击败 Samsung/Apple 顶级机型。",
        "耳机来源在旧检索核验中未匹配；文字修订不解除待核实标记，不把缺乏实测说成产品没有性能。",
        "并购摘要未收录金额、产品日期与计划细节；只保留新闻稿中的规划，不推断新品上市或价格变动。",
        "笔记本摘要的一年周期、性能对手与价格基线不足；删除整条定量/对比及购买推荐，不移除条件后保留结论。",
        "折叠机缺点仅见标题描述；删除标题解读和无来源的缺点/测试缺失主张。",
    ], "changes": changes,
}
new = video_workflow.save_edit(SCRIPT, title=v1["title"], sections=sections,
                               edited_by="用户授权·观众视角打磨", base_version=1)
assert new == 2
v2 = store.get_version(SCRIPT, new)
store.update_version_audit(SCRIPT, new, sections=v2["sections"], meta={**v2["meta"], "editorial_review": notes})
store.update_script(SCRIPT, status="DRAFT", stage="观众视角修订后审计", error=None)
steps.log(SCRIPT, "write", "edit", "v2：内部备注移至审校记录，收回无证据的比较，保留具体来源与事实限定", by="用户授权·质量打磨")
(OUT / "editorial-changes.json").write_text(json.dumps(notes, ensure_ascii=False, indent=2))
print("Saved v2; running fact, style and newcomer audits", flush=True)
column = columns.get(get_settings().paths.config_dir, s["column_key"])
status = video_workflow._resume_audit(SCRIPT, store.get_script(SCRIPT), video_workflow.report_of(s), s["report_path"], column)
v2 = store.get_version(SCRIPT, 2)
(OUT / "audited-v2.json").write_text(json.dumps(v2, ensure_ascii=False, indent=2))
print(json.dumps({"status": status, "version": 2, "checks": v2["meta"].get("checks", {}).get("summary"),
                  "problems": v2["meta"].get("problems")}, ensure_ascii=False), flush=True)

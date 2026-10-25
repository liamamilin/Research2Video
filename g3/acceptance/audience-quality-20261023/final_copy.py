"""Keep factual scope inside the claim; remove redundant disclaimer paragraphs."""
import json
from pathlib import Path
import sys

OUT = Path(__file__).resolve().parent
RESEARCH = OUT.parents[3] / "AI_research/Base_CodingCLi"
sys.path.insert(0, str(RESEARCH))
from core import provenance, report_meta
from core.video import store, reports, steps
from web.services import video_workflow

s = store.get_script("e118dc7db5f34c3ba90af0285b4688f5")
assert s["current_version"] == 4 and not s.get("stage")
v = store.get_version(s["id"], 4)
assert v["meta"]["checks"]["summary"]["passed"]
urls = v["meta"]["editorial_review"]["verified_source_urls"]
packet = (RESEARCH / s["report_path"]).read_text() + """
## 第五版精确范围补充
Motorola 软件段具体记录：官方网页当时仍写三年安全补丁；公司向媒体回复五年安全更新，并另有三次大版本升级。口播只讲安全更新的三年/五年口径差异，不把三次系统升级混为三年安全支持，不声称其中一个已最终落实。
对 GSMArena 飞行体验，来源和几次飞行场景已限定主观评价，不再附加量化对比免责声明。并购段明确正在收购与未来产品计划，不推断交割完成、新品上市或降价。允许结尾归纳各条目涉及的真实使用取舍，不增加无来源购买推荐。
"""
rel = f"state/video_editorial_sources/{s['id']}/verified-v5.txt"
path = RESEARCH / rel
assert not path.exists()
path.write_text(packet)
report_meta.append_record(rel, "consumer/consumer_daily", extra={"citation_check": provenance.check(packet, urls),
    "verification_method": "publisher pages checked; claim scope inline; historical snapshots preserved",
    "original_report_path": "output/consumer/2026-10-22_consumer_daily.md"})
texts = [
    ["这期看五件新品和行业变化：手机的长续航、耳机的降噪体验，还有音频品牌收购、可翻转笔记本和宽幅折叠手机。亮点各不相同，落到实际使用，也各有要看的取舍。"],
    ["Motorola Edge 70 Max 是一款主打大电池和续航的手机。在科技评测网站 Notebookcheck 的 Wi-Fi 网页浏览测试中，它的续航接近28小时。评测同时认为，这款大电池手机没有让机身显得笨重。",
     "不过，它的存储不能扩展。安全更新支持还存在口径差异：Notebookcheck 评测记录，厂商官网写三年，公司向媒体回复五年。两种说法之间的差别，会影响长期使用预期。"],
    ["Oppo Enco X4 是 Oppo 的旗舰降噪耳机，已经国际首发。科技媒体 GSMArena 的开箱上手介绍，它采用 Dynaudio 调音，Dynaudio 是音频品牌。GSMArena 作者在几次飞行中试用后，认为佩戴舒适、降噪有效。"],
    ["另一条变化发生在品牌层面。GSMArena 报道，音频公司 Bose 正在收购 Skullcandy，后者是生活方式音频品牌。双方的联合新闻稿说，Skullcandy 会保留自己的品牌，未来产品计划结合 Bose 的音频专长和 Skullcandy 的设计。"],
    ["Acer Swift Spin 14 AI 是一台屏幕能翻转的 Windows 笔记本。它采用高通的 ARM 架构处理器，与常见 AMD、Intel Windows 机型的芯片架构不同，部分软件需要适配。Notebookcheck 对它运行原生适配软件时的性能进步给出积极评价。选这类机器，先确认自己常用的软件能否正常使用。"],
    ["Xiaomi 18 Fold 走的是宽幅折叠路线，展开后提供较宽的操作区域，配两块 OLED 显示屏。Notebookcheck 肯定了它的形态和屏幕亮度，也点出具体取舍：系统更新安排不清晰，扬声器的低音偏弱。屏幕是亮点，软件支持和听感也会影响日常体验。"],
    ["这期最抢眼的是手机续航，但判断新品，亮点和代价要放在一起看：手机的更新支持、耳机的试用场景、电脑的软件适配，以及折叠屏的日常取舍。品牌收购，则要看后续产品如何落地。"],
]
sections = [{"title": old["title"], "paragraphs": [
    {"text": text, "citations": urls if si in (0, 6) else [urls[si-1]],
     "evidence": "NEEDS_REVIEW", "review_reason": "最终观众文案及事实范围复审",
     "note": "事实限定放在相关主句中，不另念研究缺口和重复免责声明"} for text in paragraphs]}
    for si, (old, paragraphs) in enumerate(zip(v["sections"], texts, strict=True))]
number = video_workflow.save_edit(s["id"], title=v["title"], sections=sections,
                                 edited_by="用户授权·最终观众文案", base_version=4)
assert number == 5
row = store.get_version(s["id"], number)
store.update_version_audit(s["id"], number, sections=row["sections"], meta={**row["meta"], "report_path": rel,
    "editorial_review": {**v["meta"]["editorial_review"], "base_version": 4,
    "advisory_disposition": "删除飞行体验与未来计划之后重复的防御性免责声明；更新口径给出三年/五年对象；删除耳套旁支和抽象社群词；结尾归纳五类具体取舍。"}})
reports.attach(s["id"], rel, created_by="用户授权·最终观众文案")
store.update_script(s["id"], report_path=rel, status="DRAFT", stage=None)
steps.log(s["id"], "write", "edit", "v5：限定随主句，删除多余免责声明，明确安全更新口径", by="用户授权·质量打磨")
(OUT / "script-v5.txt").write_text("\n\n".join([row["title"]] + ["\n".join(["## " + x["title"]] + [p["text"] for p in x["paragraphs"]]) for x in sections]))
print("Saved v5; no previous version or source overwritten", flush=True)

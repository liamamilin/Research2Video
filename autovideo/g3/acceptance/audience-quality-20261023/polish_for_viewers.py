"""Address concrete viewer gaps; do not turn every advisory question into narration."""
import copy
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
assert s["current_version"] == 3 and not s.get("stage")
v3 = store.get_version(s["id"], 3)
assert v3["meta"]["checks"]["summary"]["passed"]
urls = v3["meta"]["editorial_review"]["verified_source_urls"]
packet = (RESEARCH / s["report_path"]).read_text() + """
## 第四版观众视角补充核验
Motorola 原评测 Battery life 表为 WiFi Websurfing 27h 57min，正文概括 nearly 28 hours；可准确说在 Notebookcheck 的 Wi-Fi 网页浏览测试中接近28小时，不是所有日常场景都能持续28小时，也不作跨品牌比较。软件段说明官网与公司向媒体回复的安全更新年限不同；可说安全更新支持年限存在口径冲突，不猜哪一个最终生效。不再使用价格较低的比较。
Acer 原评测将 Windows ARM 与传统 AMD/Intel 方案区分，并强调 ARM 原生应用优势及一般 x64 Windows 使用差异。可以解释为处理器架构与常见 AMD/Intel Windows 笔记本不同，部分软件需要适配；不把部分软件的效果泛化到所有软件，不编兼容软件清单。
Xiaomi 原文解释 unusual wide case、folded open generously sized workspace。可说宽幅机身展开后有较宽操作区域。缺点包括系统更新安排不清晰、扬声器缺少低音。只讲这两项具体问题，不笼统说未知的软件限制。
Skullcandy 作为独立品牌保留，只表示品牌与名字保留，不推断独立运营或交割已经完成。
"""
rel = f"state/video_editorial_sources/{s['id']}/verified-v4.txt"
path = RESEARCH / rel
assert not path.exists()
path.write_text(packet)
report_meta.append_record(rel, "consumer/consumer_daily", extra={"citation_check": provenance.check(packet, urls),
    "verification_method": "publisher pages read; audience gaps checked; original snapshots retained",
    "original_report_path": "output/consumer/2026-10-22_consumer_daily.md"})
texts = [
    ["电池做大，手机就一定笨重吗？这期从一款主打续航的手机说起，再看降噪耳机、音频品牌收购、可翻转笔记本和宽幅折叠手机。五件变化，重点看它们带来了什么，也看要接受哪些取舍。"],
    ["Motorola Edge 70 Max 是一款主打大电池和续航的手机。科技评测网站 Notebookcheck 测到，它在 Wi-Fi 网页浏览测试中接近28小时。这个结果对应的是网页浏览测试，不能直接当作所有日常场景的使用时长。评测也认为，大电池没有让机身显得笨重。",
     "Notebookcheck 同时提醒，这款手机的存储不能扩展。安全更新支持年限也有口径冲突：厂商官网和公司向媒体回复的说法不同。长续航有吸引力，长期使用的支持也要看清楚。"],
    ["Oppo Enco X4 是 Oppo 推出的旗舰降噪耳机，已在国际市场首发。科技媒体 GSMArena 的开箱上手介绍，它采用音频品牌 Dynaudio 的调音。",
     "GSMArena 作者在几次飞行中试用后，认为佩戴舒适、降噪有效。这是飞行场景下的试用感受，不能直接当作不同耳机在统一条件下的量化对比。"],
    ["另一条音频消息来自 Bose。GSMArena 报道，这家音频公司正在收购生活方式音频品牌 Skullcandy。联合新闻稿称，Skullcandy 仍将保留自己的品牌。",
     "双方计划把 Bose 的音频专长，与 Skullcandy 的设计和社群连接结合起来。这说的是未来产品规划，不能据此认定新品已经推出，或价格已经改变。"],
    ["Acer Swift Spin 14 AI 是一台屏幕能翻转的 Windows 笔记本。它采用高通的 ARM 架构处理器，与常见 AMD、Intel Windows 机型的芯片架构不同，部分软件需要适配。Notebookcheck 对它运行原生适配软件时的性能进步给出积极评价。选这类机器，先确认自己常用的软件能否正常使用。"],
    ["Xiaomi 18 Fold 走的是宽幅折叠路线，展开后提供较宽的操作区域，配两块 OLED 显示屏。Notebookcheck 肯定了它的形态和屏幕亮度，也点出具体取舍：系统更新安排不清晰，扬声器的低音偏弱。屏幕是亮点，软件支持和听感也会影响日常体验。"],
    ["这期最有看点的，还是 Motorola Edge 70 Max 的续航和体积。继续关注它，也别只盯着电池：安全更新支持能否说清楚，同样影响长期使用。"],
]
sections = []
for si, (old, paragraphs) in enumerate(zip(v3["sections"], texts, strict=True)):
    sections.append({"title": old["title"], "paragraphs": [
        {"text": t, "citations": urls if si == 0 else [urls[0 if si == 6 else si-1]],
         "evidence": "NEEDS_REVIEW", "review_reason": "观众视角再打磨，需要核验当前表述与条件",
         "note": "删去耳套尺寸和模糊价格；解释相关术语与具体限制"} for t in paragraphs]})
version = video_workflow.save_edit(s["id"], title=v3["title"], sections=sections,
                                  edited_by="用户授权·观众视角再打磨", base_version=3)
assert version == 4
row = store.get_version(s["id"], version)
store.update_version_audit(s["id"], version, sections=row["sections"], meta={**row["meta"], "report_path": rel,
    "editorial_review": {**v3["meta"]["editorial_review"], "base_version": 3,
    "advisory_disposition": "修复模糊比较、机构/架构指代、更新支持对象和实际产品取舍；不扩成兼容软件清单或完整测评。结尾按配方聚焦一件，不机械重复五件。"}})
reports.attach(s["id"], rel, created_by="用户授权·观众视角再打磨")
store.update_script(s["id"], report_path=rel, status="DRAFT", stage=None)
steps.log(s["id"], "write", "edit", "v4：具体测试有条件，资料备注不上屏，删除旁支并补必要解释", by="用户授权·质量打磨")
(OUT / "script-v4.txt").write_text("\n\n".join([row["title"]] + ["\n".join(["## " + x["title"]] + [p["text"] for p in x["paragraphs"]]) for x in sections]))
print("Saved v4 and independent source snapshot", flush=True)

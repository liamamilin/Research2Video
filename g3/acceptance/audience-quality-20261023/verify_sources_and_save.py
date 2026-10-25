"""Store the manually checked source packet and v3; preserve v1/v2 and the old report.

Source claims below were checked against the five publisher pages using the web
tool, 2026-10-23. This is a small, paraphrased evidence packet, not full articles.
"""
import copy
import hashlib
import json
from pathlib import Path
import sys

OUT = Path(__file__).resolve().parent
RESEARCH = OUT.parents[3] / "AI_research/Base_CodingCLi"
sys.path.insert(0, str(RESEARCH))
from core import provenance, report_meta
from core.video import store, reports, steps
from web.services import video_workflow

script = store.get_script("e118dc7db5f34c3ba90af0285b4688f5")
assert script["current_version"] == 2
v2 = store.get_version(script["id"], 2)
(OUT / "v2-before-source-correction.json").write_text(json.dumps(v2, ensure_ascii=False, indent=2))
v1 = store.get_version(script["id"], 1)
urls = [v1["sections"][1]["paragraphs"][0]["citations"][0],
        "https://www.gsmarena.com/oppo_enco_x4_unboxing_and_handson-news-75155.php",
        v1["sections"][3]["paragraphs"][0]["citations"][0],
        v1["sections"][4]["paragraphs"][0]["citations"][0],
        v1["sections"][5]["paragraphs"][0]["citations"][0]]
facts = [
    ("Motorola Edge 70 Max", "Notebookcheck 第三方评测，2026-10-21。大电池、旗舰功能和续航是主要卖点。评测肯定续航与机身手感，认为当前价格较低；同时指出存储不能扩展，更新支持有限且相关信息有冲突。原网页包含续航测试、价格和缺点，不能从旧摘要未收录推断原网页没有。这里只保留定性评价，不跨测试条件比较其他品牌。"),
    ("Oppo Enco X4", "GSMArena 作者 Ivan 的开箱上手，2026-10-22。产品为 Oppo 旗舰降噪耳机，刚国际首发；Dynaudio 调音，Dynaudio 为音频品牌。盒内有耳机及 S、M、L 硅胶耳套。作者在几次飞行试用后认为佩戴舒适、降噪有效，这是有限使用场景中的主观体验，不能当作统一条件的量化降噪排名。原网页确实有试用体验，并非只有包装信息。旧 URL 把 handson 错写为 hands-on。"),
    ("Bose 与 Skullcandy", "GSMArena 媒体报道，2026-10-22，引述双方联合新闻稿：Bose 正在收购 Skullcandy；报道没有证明交易交割已完成。未来产品拟结合 Bose 音频专长及 Skullcandy 设计和社群；Skullcandy 将作为独立品牌留在 Bose 高端消费音频产品线，继续以 Park City, Utah 为基地。这是未来规划，不能推断新品已上市或价格已变化。"),
    ("Acer Swift Spin 14 AI", "Notebookcheck 第三方可翻转笔记本评测，2026-10-21。采用高通 Snapdragon for Windows ARM 方案。评测对性能和效率进步给出积极评价，特别提及 ARM 原生应用表现；也提醒一般 Windows x64 软件的可靠性/使用场景差异及价格取舍。不能泛化成所有软件都更快。原网页有具体测试和价格，不使用旧摘要的没说明表述。"),
    ("Xiaomi 18 Fold", "Notebookcheck 第三方评测，2026-10-20。宽幅折叠手机，两块 OLED 显示屏。评测肯定宽幅形态、屏幕亮度与硬件，同时指出部分软件功能限制、系统更新安排不清晰等问题。原网页有续航、相机评价与具体缺点，不能说标题有缺点而全文没列。这里只保留形态/屏幕及软件取舍，不生成跨条件性能排名。"),
]
packet = "# 五件新品变化速览：原网页核验快照\n\n核验日期：2026-10-23。用于本篇修订审校；旧报告与旧版本保留。\n\n"
for (title, content), url in zip(facts, urls, strict=True):
    packet += f"## {title}\n{content}\n来源：{url}\n\n"
packet += "## 编辑重点\n本期聚焦五件变化。Motorola 的看点是续航与机身体积的取舍，价格评价应归属于评测；实际选购需结合具体测试与当地售价。内部缺口只供审校，不进入口播。\n"
relative = f"state/video_editorial_sources/{script['id']}/verified-v3.txt"
path = RESEARCH / relative
path.parent.mkdir(parents=True, exist_ok=True)
assert not path.exists()
path.write_text(packet)
check = provenance.check(packet, urls)
assert check["matched"] == 5 and not check["unmatched"]
report_meta.append_record(relative, "consumer/consumer_daily", extra={
    "citation_check": check, "verification_method": "publisher pages opened and read; paraphrased claim packet",
    "original_report_path": script["report_path"], "verified_source_urls": urls})

texts = [
    ["这期看五件新品和行业变化。一款大电池手机，一款降噪耳机，以及音频公司 Bose 收购音频品牌 Skullcandy 的消息。还有一台可翻转笔记本，和一款宽幅折叠手机。"],
    ["Motorola Edge 70 Max 是一款围绕大电池设计、主打旗舰功能和续航的手机。Notebookcheck 的评测肯定了它的续航表现，也认为大电池没有让机身显得笨重。价格较低同样是这篇评测给出的评价。",
     "Notebookcheck 也提醒，这款手机的存储不能扩展，更新支持还存在不确定性。续航和手感是看点，但这些取舍也要一起看。"],
    ["Oppo Enco X4 是 Oppo 推出的旗舰降噪耳机，已在国际市场首发。GSMArena 的开箱上手介绍，它采用音频品牌 Dynaudio 的调音，包装里有耳机及 S、M、L 三种硅胶耳套。",
     "GSMArena 作者在几次飞行中试用后，认为佩戴舒适、降噪有效。这是飞行场景下的试用感受，不能直接当作不同耳机在统一条件下的量化对比。"],
    ["据媒体 GSMArena 报道，音频公司 Bose Corporation 正在收购生活方式音频品牌 Skullcandy。双方在联合新闻稿里说，未来产品会结合 Bose 的音频专长，以及 Skullcandy 的设计和社群连接。",
     "新闻稿称，Skullcandy 将作为独立品牌保留在 Bose 的高端消费音频产品线中，并继续以 Park City, Utah 为基地。这是双方的未来规划，不能据此认定新品已经推出或价格已经改变。"],
    ["Acer Swift Spin 14 AI 是可翻转笔记本，采用高通 Snapdragon 的 Windows ARM 处理器方案。Notebookcheck 的评测对它的性能进步给出积极评价，尤其是针对这类芯片适配的软件。部分 Windows 软件的兼容性，以及价格，仍是需要考虑的取舍。"],
    ["Xiaomi 18 Fold 是 Xiaomi 推出的宽幅折叠手机，配两块 OLED 显示屏。Notebookcheck 的评测肯定了宽幅形态和明亮的屏幕，也指出系统更新安排不清晰、部分软件功能存在限制。选它不能只看屏幕和硬件，还要看这些限制是否影响自己的使用。"],
    ["五件变化里，最值得继续关注 Motorola Edge 70 Max，重点是续航和体积能否兼顾。Notebookcheck 的评价提供了一个线索，具体选购仍应结合实际续航测试和当地售价。"],
]
sections = []
for si, (old, paragraphs) in enumerate(zip(v2["sections"], texts, strict=True)):
    citation = urls if si == 0 else [urls[0 if si == 6 else si-1]]
    sections.append({"title": old["title"], "paragraphs": [
        {"text": t, "citations": citation, "evidence": "NEEDS_REVIEW",
         "review_reason": "原网页核验后修订，等待对当前整段及其事实限定的明确审计",
         "note": "原网页核验修订；旧报告、v1/v2 和原待核实记录保留"} for t in paragraphs]})
version = video_workflow.save_edit(script["id"], title=v2["title"], sections=sections,
                                  edited_by="用户授权·原网页核验修订", base_version=2)
assert version == 3
row = store.get_version(script["id"], version)
store.update_version_audit(script["id"], version, sections=row["sections"], meta={**row["meta"],
    "report_path": relative, "editorial_review": {"base_version": 2,
    "original_report_path": script["report_path"], "source_packet_sha256": hashlib.sha256(packet.encode()).hexdigest(),
    "source_corrections": ["耳机 canonical URL handson", "并购状态：正在收购，未声称完成交割",
                           "摘要缺项不等于来源缺项", "原文支持的真实产品取舍替代研究免责声明"],
    "verified_source_urls": urls}})
reports.attach(script["id"], relative, created_by="用户授权·原网页核验修订")
store.update_script(script["id"], report_path=relative, status="DRAFT", stage=None)
steps.log(script["id"], "write", "edit", "v3：核对原网页，修正耳机来源及并购状态，保留真实使用边界", by="用户授权·质量打磨")
(OUT / "source-verification.json").write_text(json.dumps({"report_path": relative,
    "original_report_path": script["report_path"], "citation_check": check, "sources": [
        {"title": t, "url": u, "supported_claims": c} for (t, c), u in zip(facts, urls)],
    "version": version}, ensure_ascii=False, indent=2))
(OUT / "script-v3.txt").write_text("\n\n".join([row["title"]] + ["\n".join(["## " + s["title"]] + [p["text"] for p in s["paragraphs"]]) for s in sections]))
print(json.dumps({"version": version, "report_path": relative, "citation_check": check}, ensure_ascii=False))

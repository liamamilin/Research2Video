"""Model-directed visual cards over the unchanged narration and captions."""
from __future__ import annotations

import json
from pathlib import Path
import re

from g2.audience_copy import INTERNAL_GAP

SCHEMA = Path(__file__).parent / "schemas/card-editorial.schema.json"
LIMITS = re.compile(r"尚未实测|未经独立验证|未独立验证|不能据此|不等于|仅限|标称|条件下|测试中")
ATTRIBUTION = re.compile(
    r"[A-Za-z][A-Za-z0-9 .&'-]{0,35}的评测(?=肯定|对|提醒|给出)"
    r"|[A-Za-z][A-Za-z0-9 .&'-]{0,35}作者(?=在|认为|试用)"
    r"|[A-Za-z][A-Za-z0-9 .&'-]{0,35}(?:也)?提醒"
    r"|[A-Za-z][A-Za-z0-9 .&'-]{0,35}(?:的)?开箱(?=时|中|看到|上手|介绍)"
    r"|[A-Za-z][A-Za-z0-9 .&'-]{0,35}\s*(?:最近|此次|本次)?转述"
    r"|(?:据媒体\s*)?[A-Za-z][A-Za-z0-9 .&'-]{0,35}(?:的)?(?:评测)?(?:认为|报道|测到|测得|肯定|记录)"
    r"|(?:论文作者|研究者|作者|团队|研究)(?:报告|估计|认为)|提出的解释(?=是|：)"
    r"|官方(?:标称|称|说)|厂商(?:自述|称)|评测(?:称|认为)|新闻稿称"
)
SCOPE = r"在[^，。；;]{1,60}(?:条件下|测试中|环境下|场景下|试用后)"
CONDITION = re.compile(SCOPE + r"|仅限[^，。；;]{1,20}|尚未实测|未经独立验证|未独立验证|最多|最高|至少|不等于[^，。；;]{1,20}")
CLAIM_BOUND = re.compile(r"并非|不是|不代表|不等于|没有|(?<!能)不能|不支持|未能|尚未|可能|预计|计划|部分|某些|最多|最高|至少|接近(?=\d)|约(?=\d)")
MODAL = re.compile(r"双方计划|未来产品规划|预计|可能|计划")
RESTRICTED_ABSENCE = re.compile(
    r"(?:没有|尚无|并无)[^，。；;]{0,30}(?:工厂|冶炼厂|设备|系统|模型|版本|产品|工具|服务|方案)"
    r"，(?:能够|能|可以|用于)[^，。；;]{2,40}"
)


def lost_claim_bounds(text: str, quote: str) -> bool:
    """Never extract the positive half of a negated or tentative clause."""
    at = quote.index(text)
    for bound in RESTRICTED_ABSENCE.finditer(quote):
        if at < bound.end() and at + len(text) > bound.start() and bound.group() not in text:
            return True
    clause = next((c for c in re.split(r"[，。；;：]", quote) if text in c), quote)
    if re.match(r"(?:或|以及)", text):
        before = quote[:quote.index(text)]
        if re.search(r"不能|并非|不是", before) and not re.search(r"不能|并非|不是", text):
            return True
    return any(m.group() not in text for m in CLAIM_BOUND.finditer(clause))


def candidates(group: dict) -> list[dict]:
    if group["overview"]:
        return [{"text": text, "quote": text} for text in group["overview"]]
    text = group["text"]
    if INTERNAL_GAP.search(text):
        raise ValueError("原稿含内部资料缺口，请先修订文稿，不能将研究备注自动变成画面重点")
    choices = []
    for sentence in re.split(r"(?<=[。！？；;])", text):
        if not sentence.strip():
            continue
        phrases = [p.strip(" ，。！？；;、：") for p in re.split(r"[，：]", sentence)]
        phrases += [m.group() for m in RESTRICTED_ABSENCE.finditer(sentence)]
        phrases += [m.group() for m in re.finditer(r"[A-Z][A-Za-z0-9]*(?:[ .'-]+[A-Za-z0-9]+){0,5}", sentence)]
        for phrase in list(phrases):
            parts = re.split(r"(?:是一[款台个]|主打|采用|也就是)", phrase, maxsplit=1)
            if len(parts) == 2:
                phrases.append(parts[1])
        for phrase in phrases:
            # A detached "则要看后续产品" loses who/what it is about. Keep the
            # original antecedent (or the list after a colon) in the candidate.
            if re.match(r"^(?:则|以及|因此|所以|但是|但|同时|后者|前者)", phrase):
                at = sentence.index(phrase)
                begin = sentence.rfind("：", 0, at) + 1
                phrase = sentence[begin:at + len(phrase)].strip(" ，。！？；;、：")
            if (3 <= len(phrase) <= 60 and phrase in sentence and phrase not in [c["text"] for c in choices]
                    and not lost_claim_bounds(phrase, sentence)
                    and not re.match(r"这套评价值得|这对关注|双方在联合|接下来只看一个信号", phrase)):
                choices.append({"text": phrase, "quote": sentence.strip()})
    return choices


def expand_selection(value: dict, context: dict) -> dict:
    if [p["id"] for p in value["pages"]] != [g["id"] for g in context["groups"]]:
        raise ValueError("必须按顺序返回所有组的 id")
    pages = []
    for selected, group in zip(value["pages"], context["groups"]):
        if "points" in selected:
            pages.append({"id": group["id"],
                          **{key: selected[key] for key in ("heading", "layout") if key in selected},
                          "points": [dict(p) for p in selected["points"]]})
            continue
        options = candidates(group)
        ids = selected["candidate_ids"]
        if len(ids) != len(set(ids)) or any(type(i) is not int or i < 1 or i > len(options) for i in ids):
            raise ValueError(f"{group['id']} 只能选择本组已有候选编号")
        points = [options[i - 1] for i in ids]
        points = [p for p in points if not any(p["text"] != q["text"] and p["text"] in q["text"] for q in points)]
        pages.append({"id": group["id"], "points": points})
    return {"pages": pages}


def constraints(text: str) -> list[str]:
    """Keep original factual bounds as evidence, not as a paragraph to display."""
    result = [s.strip() for s in re.split(r"(?<=[。！？；;])", text)
              if LIMITS.search(s) and not INTERNAL_GAP.search(s)]
    for match in ATTRIBUTION.finditer(text):
        if not any(match.group() in sentence for sentence in result):
            result.insert(0, match.group().strip())
    return list(dict.fromkeys(result))


def point_qualifiers(point: dict, group: dict) -> list[str]:
    """Attach short, verbatim conditions only to the point whose sentence they qualify.

    No attribution carries across a full stop. Semicolons can continue a source's
    claim; clauses after a different named source use the nearest attribution.
    Full original sentences remain in evidence and narration, never in a footer.
    """
    quote, text = point["quote"], point["text"]
    at = group["text"].index(quote)
    begin = max(group["text"].rfind(stop, 0, at) for stop in "。！？\n") + 1
    context = group["text"][begin:at] + quote
    point_at = context.index(text, len(context) - len(quote))
    sources = list(ATTRIBUTION.finditer(context[:point_at + len(text)]))
    attribution = sources[-1].group().strip() if sources else ""
    if attribution and not re.match(r"官方|厂商", attribution):
        # A provenance label names the source, never its evaluative polarity.
        # "Notebookcheck 肯定" under a drawback would misleadingly read as praise.
        attribution = re.sub(r"(?:(?:最近|此次|本次)?转述|(?:也)?(?:认为|肯定|提醒|记录|测到|测得|称))$", "", attribution).strip()
    qualifiers = [attribution] if attribution else []
    local_at = quote.index(text)
    modes = list(MODAL.finditer(quote[:local_at]))
    if modes:
        qualifiers.append(modes[-1].group())
    if "：" in quote[:local_at] and re.search(r"\d|[一二三四五六七八九十]+年", text):
        heading = quote.split("：", 1)[0].strip()
        if len(heading) <= 32:
            qualifiers.append(heading)
    clause_begin = max(quote.rfind(stop, 0, local_at) for stop in "，；;：") + 1
    ends = [quote.find(stop, local_at + len(text)) for stop in "，。；;："]
    clause_end = min((end for end in ends if end >= 0), default=len(quote))
    qualifiers += [m.group().strip() for m in CONDITION.finditer(quote[clause_begin:clause_end])]
    # A leading test condition can introduce a following comma clause. Bounds
    # such as 最高 attached to a different metric must never carry with it.
    prefix = ATTRIBUTION.sub("", quote[:clause_begin]).strip(" ，：")
    if re.fullmatch(SCOPE, prefix):
        qualifiers.append(prefix)
    return list(dict.fromkeys(q for q in qualifiers if q not in text))


def display_qualifiers(point: dict, group: dict, points: list[dict]) -> list[str]:
    """A shared metric heading already on this card does not need repeating."""
    return [q for q in point_qualifiers(point, group)
            if not (point["quote"].startswith(q + "：") and any(q in p["text"] for p in points))]


def groups(narration: dict, paragraph_starts: set[str]) -> list[dict]:
    chapters = narration.get("chapters") or [{"id": "c1", "title": narration["title"],
        "narration_segment_ids": [s["id"] for s in narration["segments"]]}]
    owner = {sid: chapter for chapter in chapters for sid in chapter["narration_segment_ids"]}
    result = []
    for segment in narration["segments"]:
        chapter = owner[segment["id"]]
        if not result or result[-1]["chapter_id"] != chapter["id"] or segment["id"] in paragraph_starts:
            result.append({"id": f"card_{len(result) + 1}", "chapter_id": chapter["id"],
                "title": chapter["title"], "segment_ids": [], "text": "", "overview": []})
        result[-1]["segment_ids"].append(segment["id"])
        result[-1]["text"] += segment["text"]
    if len(chapters) > 2 and re.search(r"地图|目录|本期看点|本期概览|本期速览", chapters[0]["title"]):
        topics = [c["title"] for c in chapters[1:] if not re.search(r"总结|结语|接下来|关注什么", c["title"])]
        if 2 <= len(topics) <= 6:
            result[0]["overview"] = topics
    return result


def validate(plan: dict, context: dict) -> None:
    expected = context["groups"]
    if [p["id"] for p in plan["pages"]] != [g["id"] for g in expected]:
        raise ValueError("画面页必须按输入顺序完整覆盖所有分组")
    for page in plan["pages"]:
        group = next(g for g in expected if g["id"] == page["id"])
        text = "\n".join(p.get("label", "") + p["text"] for p in page["points"])
        if not text.strip():
            raise ValueError("画面文字为空")
        if page.get("layout") == "comparison" and len(page["points"]) != 2:
            raise ValueError("双栏对比组件需要左右两项；其他数量可用 notes 或 steps 排版")
        for point in page["points"]:
            if point.get("segment_id") and point["segment_id"] not in group["segment_ids"]:
                raise ValueError(f"{page['id']} 的展开时刻必须引用本页的旁白段编号")
        from PIL import Image, ImageDraw
        from g2.fast_cards import draw_editorial
        from g2.production_plan import STYLES
        draw_editorial(ImageDraw.Draw(Image.new("RGB", (1920, 1080))), page,
                       STYLES["editorial_light"], STYLES["editorial_light"]["accent"])


def messages(context: dict, correction: str | None = None) -> list[dict]:
    system = '根据素材，产出目标观众愿意看、听得懂、想继续看的视频。\n当前步骤：为旁白设计帮助理解的画面。用恰当的视觉层次呈现这一段最值得看的东西，让画面随讲述展开。接口输出 JSON，每组返回对应的页面 id 和 points，结构见 required_schema。'
    payload = {"title": context.get("title", ""),
               "layouts": {"focus": "一个重点的大字画面", "comparison": "左右两项的对照", "steps": "步骤或关系的展开", "notes": "多个要点"},
               "groups": [{"id": g["id"], "title": g["title"], "original_text": g["text"],
                           "narration": g.get("narration", [])}
                           for g in context["groups"]], "required_schema": json.loads(SCHEMA.read_text())}
    if context.get("instruction"):
        payload["visual_edit_preference"] = context["instruction"]
    if correction:
        payload["validation_feedback"] = correction
    return [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]


def plan_storyboard(pipeline, narration: dict) -> dict:
    starts = pipeline.ARTIFACTS / "paragraph-starts.json"
    paragraphs = set(json.loads(starts.read_text())["segment_ids"]) if starts.exists() else set()
    input_groups = groups(narration, paragraphs)
    texts = {s["id"]: s["text"] for s in narration["segments"]}
    for group in input_groups:
        group["narration"] = [{"id": sid, "text": texts[sid]} for sid in group["segment_ids"]]
    context = {"title": narration["title"], "groups": input_groups}

    def check(value, ctx):
        try:
            validate(expand_selection(value, ctx), ctx)
        except ValueError as exc:
            raise pipeline.G2Fault("CARD_EDITORIAL_INVALID", str(exc)) from exc

    plan = pipeline.generate_validated_plan("card-editorial", messages, SCHEMA, check, context)
    board, evidence = compile_pages(expand_selection(plan, context), context["groups"])
    pipeline.write_json(pipeline.ARTIFACTS / "card-editorial.json", {"version": 3,
        "method": "goal-first screen copy", "pages": evidence})
    return board


def compile_pages(plan: dict, input_groups: list[dict]) -> tuple[dict, list[dict]]:
    scenes, evidence = [], []
    for page, group in zip(plan["pages"], input_groups):
        text = "\n".join(p["text"] for p in page["points"])
        heading = page.get("heading") or group["title"]
        layout = page.get("layout") or ("focus" if len(page["points"]) == 1 else "notes")
        points = [{k: v for k, v in point.items() if k in {"text", "label", "segment_id"}}
                  for point in page["points"]]
        scenes.append({"id": group["id"], "component": "kinetic_text", "props": {
            "title": heading, "text": text, "layout": layout, "points": points},
            "narration_segment_ids": group["segment_ids"], "purpose": group["title"],
            "visual_intent": {"viewer_understands": group["title"], "before": "重点尚未展开", "after": text},
            "beats": [{"id": group["id"] + "_enter", "action": "reveal", "target_id": "scene",
                       "trigger": {"segment_id": group["segment_ids"][0], "boundary": "start", "offset_ms": 0}}]})
        evidence.append({**group, "layout": layout,
            "heading": heading, "qualifier_layout_version": 2, "points": [
            {**point, "qualifiers": []}
            for point in page["points"]], "constraints": []})
    return {"scenes": scenes}, evidence


def replan_scene(pipeline, old: dict, narration: dict, instruction: str) -> dict:
    path = pipeline.ARTIFACTS / "card-editorial.json"
    metadata = json.loads(path.read_text()) if path.exists() else {"version": 1, "pages": []}
    local = [s for s in narration["segments"] if s["id"] in old["narration_segment_ids"]]
    group = next((p for p in metadata["pages"] if p["id"] == old["id"]), None)
    if group is None:
        group = {"id": old["id"], "chapter_id": local[0].get("chapter_id", ""), "title": old["props"]["title"],
                 "segment_ids": old["narration_segment_ids"], "text": "".join(s["text"] for s in local), "overview": []}
    context = {"groups": [group], "instruction": instruction}
    def check(value, ctx):
        try:
            validate(expand_selection(value, ctx), ctx)
        except ValueError as exc:
            raise pipeline.G2Fault("CARD_EDITORIAL_INVALID", str(exc)) from exc

    result = pipeline.generate_validated_plan("card-editorial-revision", messages, SCHEMA, check, context)
    board, evidence = compile_pages(expand_selection(result, context), [group])
    if any(p["id"] == old["id"] for p in metadata["pages"]):
        metadata["pages"] = [evidence[0] if p["id"] == old["id"] else p for p in metadata["pages"]]
    else:
        metadata["pages"].append(evidence[0])
    pipeline.write_json(path, metadata)
    return board["scenes"][0]

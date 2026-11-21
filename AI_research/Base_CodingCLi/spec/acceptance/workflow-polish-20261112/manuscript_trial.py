"""Exercise the normal writing entry and compare saved drafts with historical versions."""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3

import yaml
from pydantic import BaseModel, Field

from opening_run import ROOT, save, setup


SPOKEN_EXAMPLES = """像给一个懂行的朋友讲。先让他看到一个动作或变化，再顺着它讲为什么发生、什么条件会改变判断。参数跟着它能解释的那一步出现。每段接住上一段的问题，带听众多懂一点；可用材料里的原话、按钮名、输出结果。
以下是虚构的写法示范，只学习它们如何从场景推出判断，实际内容使用本期素材。
“假设仓库加了台扫码器，每单快十秒，货却还是要等下午那辆车。我会先问车能不能多跑一趟。车没变，扫码再快，也只是让箱子早点排到门口。”
“同一锅汤，留一碗盖着，另一碗敞着。盖子留住了水汽，碗里少了一条散热的路。你要是每隔一会儿就揭盖，两碗凉得就没那么不一样了。”
具体可以是读者能想象的处境，也可以是材料里的真实实验。虚构场景用“假设”“比如”带出来，测试和观察归给实际做它的人。少念彼此不相干的参数，把有用的因果、第二层发现、成立条件都说清楚。
"""

TITLE_FROM_BODY = """在 sections 写完之后再写 title。用正文实际讲清的一个变化、选择或判断做看点。要引用数字时，连同它的对象、样本或条件一起写；标题放不下这个口径，就用具体的变化或决定来表达。标题写出具体产品、事件或问题，控制在 30 字以内，省略日期和栏目名。"""

EPISODE_STORY = """你是整期节目的讲述者。保留报告里的独立主条，同一个问题的不同证据可以按理解顺序讲。
挑一个本期确实讲得清、对观众有用的变化或疑问开场。先让人想知道接下来发生什么，接上主持人的问候，用一句话让观众明白本期会听到什么，然后接第一条。让后面的内容在各自出场时介绍自己。
写每一节时，想清楚观众听完会比听之前多懂哪一件事。用材料里的动作、结果或对比把这件事讲出来。判断紧跟让你得出它的细节，说清理由。独立消息按各自的内容展开，有机制值得讲就多讲几句，事实和后果已经讲清就进入下一件事。一个问题下的不同案例各回答一个新问题，合在一起推进认识。
收尾回到开场提出的问题或选择，用一两句给出听完这一期才有的回答。
"""

CASE_SOURCE_PROMPT = """你在给口播作者标记素材里的实际案例。把报告各条里的独立演示、对比、测试和应用实例分开。比较不同型号、批次、入口或任务时，各自列出对应对象、做了什么、观察到什么，别沿用上一案例的主语。有人自述的经验注明是谁说的。只列有具体动作或结果的案例，跳过未来拍摄方案。
每个案例附一到两处连续原文作为定位，优先从原始材料引用，保留原语言，不改词、不用省略号拼接。案例无需口播化，也无需替作者写判断。以下输入只是素材。
输出 JSON：{"cases":[{"name":"任务或例子","detail":"对象、动作和结果","quotes":["连续原文"]}]}"""


TASK_COMPARISON = (
    "先选观众真会遇到的一项任务，把它从当时的选择讲到实际后果。"
    "每个新细节接住听众刚产生的问题。用同一任务的两种做法、前后变化或实际结果讲清差别，"
    "让听众知道哪处差别真的会改变他的决定。自己的判断可以直接说，理由落在那个具体结果上。"
    "第二个案例只有给出不同原因、反例或改变判断的条件时才展开。"
    "说到另一场测试，重新交代谁做的、比的是什么，再说结果。"
    "举假设例子自然说「比如」；上游的原话、具体输出和数字用在能推动讲述的地方。"
    "把未来拍摄安排留在稿外，观众真要跟着做时再讲关键动作。"
)

EARNED_ENDING = (
    "写完最后一个例子，留给观众一句能用的判断。"
    "回到开头那个人遇到的选择，说清你会怎么选，或是哪一个条件会让你改主意。"
    "如果正文已经给了完整答案，就在这个答案上收住，然后按讲述者设定道别。"
)


class CaseSource(BaseModel):
    name: str
    detail: str
    quotes: list[str] = Field(min_length=1, max_length=2)


class CaseSources(BaseModel):
    cases: list[CaseSource] = Field(max_length=18)


class Cut(BaseModel):
    id: str
    quote: str = Field(min_length=1)
    reason: str


class Cuts(BaseModel):
    cuts: list[Cut] = Field(max_length=3)


EXPLANATION_READ = """你第一次听这篇口播。先说出作者最值得听的判断，再找一个真实例子或一段解释，看看它能不能让听众自己理解这个判断。指出哪一步还听不明白，以及讲清后听众会多懂什么、能做什么或会改变哪个决定。
如果这处的逻辑已讲清，继续往下读。篇幅留给具体发生了什么、为什么、接下来有什么影响。已经交代过的消息出处、限定条件和相同意思的比喻，只有妨碍理解时才值得改。
只提一处有明确收益的修改，可涉及一至三段。可以说明希望补出哪一步，由作者查素材写。如果找不到，targets 留空。
输出 JSON：{"takeaway":"听完记住的判断","keep_quote":"值得保留的原句","issue_quote":"妨碍理解的原句","revision":"补清哪一步及听众所得","targets":["S1P1"]}"""

def cut_repetition(draft, client, voice: str | None):
    """Trial a reader's verbatim deletions while leaving remaining narration intact."""
    from core.video import length, persona, writer
    from core.video.adapt.common import call_json
    from core.video.polish import pid

    instruction = (
        "顺着听完口播，最多标三处可直接删掉的连续原句。优先删开场逐项报目录、"
        "重复总结、已经在其他段落讲透的细节。用 reason 说明删掉后由哪句话继续，或哪里已经讲过。"
        "quote 原样复制一到数句，包含句末标点，不能留下半句话。保留钩子、主持人介绍、"
        "推进观点的例子、条件与道别。听着已经顺或者删了就接不上时，可返回空列表。"
        '输出 JSON：{"cuts":[{"id":"段落编号","quote":"整句原文。","reason":"听感收益"}]}'
    )
    locations = {pid(si, pi): (si, pi) for si, section in enumerate(draft.sections)
                 for pi, _ in enumerate(section["paragraphs"])}
    request = {"title": draft.title, "paragraphs": {
        key: draft.sections[si]["paragraphs"][pi]["text"] for key, (si, pi) in locations.items()}}
    proposal = call_json(client, instruction, json.dumps(request, ensure_ascii=False), Cuts, temperature=0.2)
    sections = deepcopy(draft.sections)
    applied, skipped = [], []
    narrator = persona.load()
    initial_opening = writer._Section.model_validate(draft.sections[0]).paragraphs
    for cut in proposal.cuts:
        if cut.id not in locations:
            skipped.append(cut.id)
            continue
        si, pi = locations[cut.id]
        paragraph = sections[si]["paragraphs"][pi]
        text = paragraph["text"]
        before, marker, after = text.partition(cut.quote)
        if (not marker or text.count(marker) != 1 or marker[-1] not in "。！？!?"
                or (before.strip() and before.rstrip()[-1] not in "。！？!?")):
            skipped.append(cut.id)
            continue
        remaining = (before + after).strip()
        if not remaining and (
            paragraph.get("citations") or sum(bool(item["text"]) for item in sections[si]["paragraphs"]) == 1
        ):
            skipped.append(cut.id)
            continue
        paragraph["text"] = remaining
        if si == 0:
            current = writer._Section.model_validate({
                **sections[0], "paragraphs": [p for p in sections[0]["paragraphs"] if p["text"]]
            }).paragraphs
            if (not persona.opening_needs_introduction(initial_opening, narrator)
                    and persona.opening_needs_introduction(current, narrator)):
                paragraph["text"] = text
                skipped.append(cut.id)
                continue
        applied.append(cut.model_dump())
    for section in sections:
        section["paragraphs"] = [p for p in section["paragraphs"] if p["text"]]
    check = length.measure(sections, draft.length_check.get("target_minutes"), voice=voice)
    result = replace(draft, sections=sections, length_check={**draft.length_check, **check},
                     meta={**draft.meta, "manuscript_workflow": {"mode": "verbatim_cut", "cuts": applied}})
    return result, {"instruction": instruction, "proposals": proposal.model_dump()["cuts"],
                    "applied": applied, "skipped": skipped, "usage": dict(client.usage)}


def case_source_notes(client, report: str, material: str) -> tuple[str, dict]:
    """Build a source index while keeping the original author inputs available."""
    from core.video.adapt.common import call_json

    request = f"## 研究底稿\n{report}\n\n## 原始材料\n{material}"
    result = call_json(client, CASE_SOURCE_PROMPT, request, CaseSources, temperature=0.1)
    sources = [" ".join(source.split()) for source in (report, material) if source]
    used = []
    excluded = []
    for case in result.cases:
        quotes = [" ".join(quote.split()) for quote in case.quotes]
        if all(len(quote) >= 12 and any(quote in source for source in sources) for quote in quotes):
            used.append(case)
        else:
            excluded.append(case.name)
    notes = "\n\n".join(f"- {case.name}：{case.detail}\n  原文：{' / '.join(case.quotes)}" for case in used)
    receipt = {"instruction": CASE_SOURCE_PROMPT, "cases": result.model_dump()["cases"],
               "used": [case.name for case in used], "excluded": excluded, "usage": dict(client.usage)}
    return notes, receipt


def title_after_body(prompt: str, title_rule: str) -> str:
    """Ask for the existing JSON fields in narration-first order."""
    match = re.search(r'\{"title":\s*"[^"]+",\s*"sections":', prompt)
    if match is None:
        raise ValueError("Expected an author output schema")
    start = match.start()
    schema = json.loads(prompt[start:])
    title = schema.pop("title")
    schema["title"] = title
    return prompt[:start].replace(title_rule, TITLE_FROM_BODY) + json.dumps(schema, ensure_ascii=False)


def without_report_fields(report: str, omitted: tuple[str, ...]) -> tuple[str, dict[str, int]]:
    """Project top-level report fields out of the trial author's input."""
    from core.video.excerpt import heading_text

    boundaries = list(re.finditer(r"(?m)^(?:#{1,6} |[-*]\s+\*\*([^*\n]+)\*\*\s*[:：])", heading_text(report)))
    counts = dict.fromkeys(omitted, 0)
    pieces = [report[:boundaries[0].start()]] if boundaries else [report]
    for match, following in zip(boundaries, [*boundaries[1:], None]):
        label = re.split(r"[（(]", match.group(1) or "", maxsplit=1)[0].strip()
        if label in counts:
            counts[label] += 1
        else:
            pieces.append(report[match.start():following.start() if following else len(report)])
    return "".join(pieces), counts


def blind(out: Path, prefix: str) -> None:
    from core.llm import LLMClient
    from core.video import manuscript_workflow as editorial, writer
    from web.services import video_workflow

    receipt = json.loads((out / "input-receipt.json").read_text())
    drafts = {}
    if (out / "second-round-replay.json").exists():
        stages = ("same-input", "original-round-2", "generated")
    elif (out / "edit-baseline.json").exists():
        stages = ("same-input", "generated")
    elif any((out / name).exists() for name in ("first-revision-replay.json", "reader-report-replay.json",
                                               "reader-opening-replay.json", "episode-opening-replay.json")):
        stages = ("same-input", "original-round-1", "generated")
    elif (out / "t04-material-input.json").exists():
        stages = ("same-input", "production-t04", "generated")
    elif (rounds := out / "author-rounds.json").exists():
        if len(json.loads(rounds.read_text())["directions"]) == 1:
            stages = ("historical", "same-input", "generated")
        else:
            stages = ("same-input", "round-1", "generated")
    elif any((out / filename).exists() for filename in
           ("author-model.json", "author-style.json", "author-title.json", "author-episode.json",
            "author-column.json", "author-report.json", "author-cases.json", "author-task.json", "author-ending.json")):
        stages = ("historical", "same-input", "generated")
    else:
        stages = ("historical", "generated") if (out / "generated.json").exists() else (
            "direct", "planned", "revision", "alternative",
        )
    for stage in stages:
        file = out / (stage + ".json")
        if file.exists():
            drafts[stage] = writer.WrittenScript(**json.loads(file.read_text()))
    if len(drafts) < 2:
        raise RuntimeError("The trial needs two manuscripts to compare")
    scripts = [editorial.spoken(draft) for draft in drafts.values()]
    if all(script == scripts[0] for script in scripts[1:]):
        save(out / "blind-equivalence.json", {"stages": list(drafts), "result": "identical_narration"})
        print(prefix, "identical narration", flush=True)
        return
    client = LLMClient(video_workflow._llm_configs(None)[0])
    for i, order in enumerate((list(drafts), list(reversed(drafts))), 1):
        file = out / f"blind-{i}.json"
        if file.exists():
            continue
        mapping = dict(zip(("1", "2", "3"), order))
        result = editorial.rank(client, receipt["audience"], {k: drafts[v] for k, v in mapping.items()})
        save(file, {"mapping": mapping, "result": result.model_dump()})
        print(prefix, "blind", i, [[mapping[label] for label in group] for group in result.order], flush=True)


class OperationPath(BaseModel):
    signal: str
    next_action: str
    result: str
    quote: str


class OperationPaths(BaseModel):
    paths: list[OperationPath] = Field(min_length=2, max_length=8)


def operation_handoff(inputs: dict) -> tuple[dict, dict]:
    from core.llm import LLMClient
    from core.video.adapt.common import call_json

    instruction = (
        "你在整理教程的已有操作依据，不写口播。把不同起始状态分开：观众眼下在哪个页面，"
        "看到了什么，手里已经有哪些凭据，什么条件下才能做下一步。只写底稿实际给出的动作和确认结果。"
        "结果是等状态或找支持时直接注明；底稿没说完成后看什么，result 留空。"
        "每条 quote 原样复制底稿中能定位该动作的一句或两句，保留原词，不拼接。"
        '输出 JSON：{"paths":[{"signal":"观众看到的状态及渠道","next_action":"接下来的一个动作",'
        '"result":"什么结果表示完成或转去下一条","quote":"底稿原话"}]}'
    )
    report = inputs["report"]
    client = LLMClient(inputs["llm_config"])
    proposal = call_json(client, instruction, report, OperationPaths, temperature=0.2)
    compact_report = " ".join(report.split())
    paths = [path for path in proposal.paths if path.quote.strip() and " ".join(path.quote.split()) in compact_report]
    if len(paths) < 2:
        raise ValueError("The trial could not locate at least two procedure paths in the report")
    receipt = {"paths": [path.model_dump() for path in paths], "omitted": len(proposal.paths) - len(paths),
               "usage": dict(client.usage)}
    context = ("## 底稿里的操作路径\n" + json.dumps(receipt["paths"], ensure_ascii=False)
               + "\n这些路径是材料索引。作者从一个状态开场，给出眼下可做的一步，再随正文解释其他状态。")
    return {**inputs, "editorial_context": context}, receipt


def replay_episode_opening(draft, inputs: dict, audience: str):
    from core.llm import LLMClient
    from core.video import episodes, length, persona, writer

    script = writer._Script.model_validate(asdict(draft))
    if len(script.sections) < 2 or (script.sections[0].episode_role, script.sections[1].episode_role) != ("opening", "item"):
        raise ValueError("The opening replay needs a complete episode with an opening and a first item")
    client = LLMClient(inputs["llm_config"])
    greeting = episodes.greeting(inputs["topic"], inputs["content_context"]) or None
    opening, receipt = writer.author_episode_opening(script, persona.load(), client, audience=audience, greeting=greeting)
    if receipt["status"] != "rewritten":
        return draft, receipt
    allowed = writer.trusted_urls(inputs["report"], inputs.get("citation_check"))
    allowed.update(writer.trusted_urls(inputs.get("original_material") or "", None))
    voice = inputs["column"].voice_id or None
    lead, _ = writer._finalize(opening.model_copy(update={"sections": opening.sections[:1]}), allowed, None, voice)
    sections = deepcopy(draft.sections)
    sections[0] = lead[0]
    if receipt["entry_start"]:
        sections[1]["paragraphs"][0]["text"] = writer.spoken_only(opening.sections[1].paragraphs[0].text.strip())
    measured = length.measure(sections, draft.length_check.get("target_minutes"), voice=voice)
    return replace(draft, sections=sections, length_check={**draft.length_check, **measured}), receipt


def material_handoff(inputs: dict, mode: str, *, report_item: str = "") -> tuple[dict, dict]:
    """Compare an author receiving material in the scope it will speak about."""
    if mode == "operation-paths":
        return operation_handoff(inputs)
    topic = inputs["topic"]
    if mode in ("report-and-story", "comparison-evidence"):
        if not report_item or not topic.get("story") or inputs.get("original_material") is not None:
            raise ValueError("The material trial needs the original single-item inputs")
        material = inputs["report"]
        receipt = {}
        if mode == "comparison-evidence":
            from core.video.excerpt import heading_text

            visible = heading_text(report_item)
            observed = re.search(r"(?m)^[-*]\s+\*\*具体材料\*\*\s*[:：]", visible)
            if not observed:
                raise ValueError("The report item does not have a concrete material field")
            remainder = visible[observed.end():]
            following = re.search(r"(?m)^(?:#{1,6} |[-*]\s+\*\*[^*\n]+\*\*\s*[:：])", remainder)
            end = observed.end() + following.start() if following else len(report_item)
            report_item = "## 研究底稿中的具体材料\n" + report_item[observed.end():end].strip() + "\n"
            story = topic["story"]
            topic = {**topic, "title": story["subject"],
                     "story": {**story, "title": story["subject"], "summary": ""},
                     "fields": {"目标受众": (topic.get("fields") or {}).get("目标受众", "")}}
            receipt = {"author_report_field": "具体材料", "story_summary_supplied": False}
        return {**inputs, "topic": topic, "report": report_item, "original_material": material}, {
            "report_chars": len(report_item), "report_sha256": hashlib.sha256(report_item.encode()).hexdigest(),
            "story_material_chars": len(material), "event": inputs["topic"]["story"]["title"], **receipt,
        }
    if mode == "selected-story":
        if not topic.get("story") or topic.get("content_mode") == "episode":
            raise ValueError("The single-item trial requires an automatically selected story")
        old_fields = topic.get("fields") or {}
        audience = inputs["column"].audience
        fields = {"目标受众": audience} if audience else {}
        return {**inputs, "topic": {**topic, "fields": fields}}, {
            "before_audience": old_fields.get("目标受众", ""), "after_audience": audience,
            "event": topic["story"]["title"], "removed_report_fields": sorted(old_fields.keys() - fields.keys()),
        }

    from core.provenance import extract_urls, normalize_url
    from core.video import episodes
    from web.services.video_workflow import MATERIAL_SEPARATOR

    report, material = inputs["report"], inputs.get("original_material") or ""
    items = (topic.get("episode") or {}).get("items") or []
    parts = episodes.item_parts(report, items)
    if not items or len(parts) != len(items) or not material:
        raise ValueError("The episode trial requires identifiable report items and original material")

    def urls(text: str) -> set[str]:
        return {normalize_url(url) for url in extract_urls(text)}

    remaining = [(block, urls(block)) for block in material.split(MATERIAL_SEPARATOR)]
    counts = {}
    for title, part in parts.items():
        cited = urls(part.split("\n## ", 1)[0])
        matches = [block for block, links in remaining if links & cited]
        remaining = [(block, links) for block, links in remaining if not links & cited]
        counts[title] = len(matches)
        if matches:
            evidence = MATERIAL_SEPARATOR.join(matches)
            position = report.index(part) + len(part)
            report = report[:position] + "\n\n#### 本条对应原材料\n\n" + evidence + "\n\n" + report[position:]
    return {**inputs, "report": report, "original_material": MATERIAL_SEPARATOR.join(block for block, _ in remaining)}, {
        "item_blocks": counts, "remaining_blocks": len(remaining), "report_chars": len(report),
    }


def main(prefix: str, out: Path, baseline_dir: Path | None = None, *,
         mode: str = "planned", fresh_entry: bool = False, local_ollama: bool = False,
         omit_raw_material: bool = False, writer_model: str | None = None,
         spoken_examples: bool = False, title_last: bool = False, episode_story: bool = False,
         column_notes: str | None = None, omit_report_fields: tuple[str, ...] = (),
         case_sources: bool = False, task_comparison: bool = False, earned_ending: bool = False,
         local_port: int = 11434, local_model: str = "qwen3-vl:8b-instruct-q4_K_M",
         local_json: bool = False, editorial_rounds: bool = False,
         replay_second_round: bool = False, cut_second_round: bool = False,
         first_revision_only: bool = False, replay_first_revision: bool = False,
         reader_column: bool = False, t04_workflow: bool = False,
         t04_reader_replay: Path | None = None, t04_revision_only: bool = False,
         t04_material_input: Path | None = None, workflow_job: str | None = None,
         handoff: str | None = None, reader_report_replay: Path | None = None,
         reader_opening_replay: Path | None = None, episode_opening_replay: Path | None = None,
         edit_baseline: bool = False) -> None:
    setup(out)
    from core.llm import LLMClient, LLMConfig
    from core.video import manuscript_workflow as editorial, store, writer
    from web.services import video_workflow

    if earned_ending:
        for name in ("SYSTEM_PROMPT", "EPISODE_PROMPT"):
            prompt = getattr(writer, name)
            setattr(writer, name, prompt.replace(writer.ENDING_RULE, EARNED_ENDING))
        save(out / "author-ending.json", {"instruction": EARNED_ENDING})
    if episode_story:
        episode_format = writer.EPISODE_PROMPT[writer.EPISODE_PROMPT.index("每个内容一个 section"):]
        writer.EPISODE_PROMPT = writer.AUTHORING_PROMPT + "\n" + EPISODE_STORY + episode_format
        save(out / "author-episode.json", {"instruction": EPISODE_STORY})
    if task_comparison:
        for name in ("SYSTEM_PROMPT", "EPISODE_PROMPT"):
            prompt = getattr(writer, name)
            if writer.CONCRETE_RULE not in prompt:
                raise RuntimeError("Expected the default concrete writing direction")
            setattr(writer, name, prompt.replace(writer.CONCRETE_RULE, TASK_COMPARISON))
        save(out / "author-task.json", {"instruction": TASK_COMPARISON})
    if spoken_examples:
        for name in ("SYSTEM_PROMPT", "EPISODE_PROMPT"):
            prompt = getattr(writer, name)
            prompt = prompt.replace(writer.STYLE_RULE, SPOKEN_EXAMPLES).replace(writer.CONCRETE_RULE, "")
            setattr(writer, name, prompt)
        save(out / "author-style.json", {"method": "spoken_examples", "replaces": ["STYLE_RULE", "CONCRETE_RULE"],
             "instruction": SPOKEN_EXAMPLES})
    if title_last:
        from core.video.adapt.common import parse_json

        for name in ("SYSTEM_PROMPT", "EPISODE_PROMPT"):
            setattr(writer, name, title_after_body(getattr(writer, name), writer.TITLE_RULE))
        save(out / "author-title.json", {"method": "title_after_body", "instruction": TITLE_FROM_BODY})
        parse = writer._parse

        def capture_order(content: str):
            script = parse(content)
            receipt = out / "author-output-order.json"
            if not receipt.exists():
                save(receipt, {"keys": list(parse_json(content))})
            return script

        writer._parse = capture_order

    with sqlite3.connect(f"file:{ROOT / 'state/video.db'}?mode=ro", uri=True) as db:
        (script_id,), = db.execute("SELECT id FROM scripts WHERE id LIKE ?", (prefix + "%",))
    script = store.get_script(script_id)
    context = script["content_context"]
    t04_job = "chatgpt_acquisition/t04_testing"
    workflow_job = t04_job if t04_workflow else workflow_job
    if workflow_job:
        bound_job = editorial.bound_job(script, str(out / "config"), str(out / "jobs"))
        if bound_job != workflow_job or workflow_job not in editorial.JOB_PROMPTS:
            raise ValueError("The workflow trial needs a real production script bound to the requested job")
        save(out / "author-column.json", {"before": context.get("notes", ""),
             "after": editorial.JOB_PROMPTS[workflow_job].draft})
    if column_notes:
        if not context.get("column") or context.get("notes") == column_notes:
            raise ValueError("Column trial needs a bound column and changed notes")
        save(out / "author-column.json", {"before": context.get("notes", ""), "after": column_notes})
        context = {**context, "notes": column_notes}
    baseline = store.get_version(script_id, script["current_version"])
    history = store.get_version(script_id, script["approved_version"] or script["current_version"])
    source = ROOT / script["report_path"]
    dest = out / "output" / source.relative_to(ROOT / "output")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    meta = out / "state/report_meta.jsonl"
    entries = [json.loads(line) for line in meta.read_text().splitlines()]
    for entry in entries:
        material = entry.get("material") or {}
        path = Path(material.get("path", ""))
        if path.is_file() and path.parent == source.parent:
            dest_material = dest.parent / path.name
            shutil.copy2(path, dest_material)
            entry["material"] = {**material, "path": str(dest_material)}
    meta.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries))

    config_path = out / "config/system.yaml"
    config = yaml.safe_load(config_path.read_text())
    if local_ollama:
        config["ai"] = {
            "model": local_model, "base_url": f"http://127.0.0.1:{local_port}/v1", "api_key_env": "",
            "max_tokens": 4096, "request_timeout": 420, "timeout": 420, "max_retries": 0,
            "extra_body": {"reasoning_effort": "none"},
        }
        if local_json:
            config["ai"]["extra_body"]["response_format"] = {"type": "json_object"}
        config.pop("review_ai", None)
        fit_material = video_workflow.fit_material
        video_workflow.fit_material = lambda report, material, limit=None: fit_material(
            report, material, limit=limit or 60_000
        )
    direct_mode = mode in {"report-only", "scoped-lead", "production"}
    author_option = "scoped_lead" if mode == "scoped-lead" else None
    config["video"]["manuscript_jobs"] = [workflow_job] if workflow_job else []
    if mode in {"report-only", "production"}:
        config["video"].pop("manuscript_workflow", None)
    else:
        config["video"]["manuscript_workflow"] = author_option or "focused"
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
    author_config = None
    if writer_model:
        author_config = replace(LLMConfig.from_system(config, "review_ai"), model=writer_model,
                                fallback_models=[], fallback_providers=[])
        save(out / "author-model.json", {"model": writer_model, "base_url": author_config.base_url,
             "max_tokens": author_config.max_tokens, "fallbacks": []})
    save(out / "case.json", {"script_id": script_id, "title": baseline["title"],
         "production": script["production"], "report": str(source),
         "report_sha256": hashlib.sha256(source.read_bytes()).hexdigest()})

    run_author = editorial.focused_author if mode == "focused" else editorial.author
    direct_author = writer.write_for_column

    def capture(stage: str, data: dict) -> None:
        save(out / (stage + ".json"), data)
        print(prefix, stage, flush=True)

    def compare_from_same_input(**inputs):
        audience = (inputs["topic"].get("fields") or {}).get("目标受众") or "普通观众"
        receipt = {
            "audience": audience, "model": inputs["llm_config"].model,
            **{k + "_sha256": hashlib.sha256((inputs.get(k) or "").encode()).hexdigest()
               for k in ("report", "original_material", "column_style")},
            "report_chars": len(inputs["report"]), "material_chars": len(inputs.get("original_material") or ""),
        }
        save(out / "input-receipt.json", receipt)
        if workflow_job:
            profile = inputs.pop("profile", None)
            if profile != editorial.JOB_PROMPTS[workflow_job]:
                raise RuntimeError("The normal writing entry did not select the requested job profile")
            original = json.loads((baseline_dir / "input-receipt.json").read_text())
            style_hash = "column_style_sha256"
            changed = {key for key in original.keys() | receipt.keys() if original.get(key) != receipt.get(key)}
            if changed != ({style_hash} if profile.draft else set()):
                raise RuntimeError("The writer inputs do not match the profile's intended changes")
            baseline_draft = baseline_dir / "same-input.json"
            if not baseline_draft.is_file():
                baseline_draft = baseline_dir / "generated.json"
            shutil.copy2(baseline_draft, out / "same-input.json")
            save(out / ("t04-workflow.json" if t04_workflow else "job-workflow.json"), asdict(profile))
            if edit_baseline:
                draft = writer.WrittenScript(**json.loads(baseline_draft.read_text()))
                capture("initial", asdict(draft))
                final = editorial.revise_passage(draft, profile=profile, on_stage=capture, **inputs)
                save(out / "edit-baseline.json", {"source": str(baseline_draft),
                     "receipt": final.meta["manuscript_workflow"]})
                capture("after-edit", asdict(final))
                return final
            if episode_opening_replay:
                previous = json.loads((episode_opening_replay / "input-receipt.json").read_text())
                if previous != receipt:
                    raise RuntimeError("The opening replay needs the same writing inputs")
                source = episode_opening_replay / "generated.json"
                shutil.copy2(source, out / "original-round-1.json")
                draft = writer.WrittenScript(**json.loads(source.read_text()))
                final, opening_receipt = replay_episode_opening(draft, inputs, audience)
                save(out / "episode-opening-replay.json", {"source": str(episode_opening_replay),
                     "opening": opening_receipt})
                capture("after-edit", asdict(final))
                return final
            if replay_path := reader_report_replay or reader_opening_replay:
                from unittest.mock import patch

                previous = json.loads((replay_path / "input-receipt.json").read_text())
                if previous != receipt:
                    raise RuntimeError("The reader replay needs the same writing inputs")
                draft = writer.WrittenScript(**json.loads((replay_path / "initial.json").read_text()))
                shutil.copy2(replay_path / "generated.json", out / "original-round-1.json")
                capture("initial", asdict(draft))
                model_call = editorial.call_json

                def replay_reader(client, prompt, request, schema, *, temperature):
                    if schema is editorial.FocusedReading:
                        if reader_report_replay:
                            prompt += "\n附上本期底稿。用它理解已经有的条件和例子，仍从观众角度指出最影响选择的一处。"
                            request = json.dumps({**json.loads(request), "research_report": inputs["report"]},
                                                 ensure_ascii=False)
                        else:
                            prompt += ("\n这轮从开场给出的观众状态开始，按原稿实际走到下一次分支。"
                                       "只报告最先不能执行的动作，选择一个段落；这段路能走通就留空。")
                    return model_call(client, prompt, request, schema, temperature=temperature)

                with patch.object(editorial, "call_json", replay_reader):
                    final = editorial.revise_passage(draft, profile=profile, on_stage=capture, **inputs)
                replay_receipt = "reader-report-replay.json" if reader_report_replay else "reader-opening-replay.json"
                save(out / replay_receipt, {"source": str(replay_path),
                     "report_sha256": receipt["report_sha256"], "report_chars": receipt["report_chars"],
                     "receipt": final.meta["manuscript_workflow"]})
                capture("after-edit", asdict(final))
                return final
            if t04_material_input:
                previous = json.loads((t04_material_input / "input-receipt.json").read_text())
                if previous != receipt:
                    raise RuntimeError("The T04 material trial needs the same production inputs")
                report, counts = without_report_fields(inputs["report"], ("操作与演示", "画面建议"))
                if not all(counts.values()):
                    raise RuntimeError(f"The T04 report needs the expected planning fields: {counts}")
                shutil.copy2(t04_material_input / "generated.json", out / "production-t04.json")
                save(out / "t04-material-input.json", {"source": str(t04_material_input),
                     "removed_fields": counts, "author_report_chars": len(report),
                     "author_report_sha256": hashlib.sha256(report.encode()).hexdigest()})
                (out / "author-report.md").write_text(report)
                draft = direct_author(**{**inputs, "report": report}, episode_lead=profile.episode_lead)
                capture("initial", asdict(draft))
                final = editorial.revise_passage(draft, profile=profile, on_stage=capture, **inputs)
                capture("after-edit", asdict(final))
                return final
            if t04_reader_replay:
                previous = json.loads((t04_reader_replay / "input-receipt.json").read_text())
                if previous != receipt:
                    raise RuntimeError("The T04 reader replay needs the original author inputs")
                draft = writer.WrittenScript(**json.loads((t04_reader_replay / "initial.json").read_text()))
                capture("same-input", asdict(draft))
                shutil.copy2(t04_reader_replay / "after-edit.json", out / "original-round-1.json")
                replay = replace(
                    profile,
                    read=(
                        "你是第一次听 T04 实测的独立读者。复述你听懂的使用判断，保留最能解释它的原话。"
                        "找一个影响观众决定的理解断点，引用原话，并说明你听到什么、哪一步还缺解释。"
                        "你只描述阅读体验，具体改法交给作者。标出一至三段；没有值得动的局部时 targets 留空。\n"
                        '输出 JSON：{"takeaway":"你听到的判断","keep_quote":"应保留的原话",'
                        '"issue_quote":"卡住的原话，可空","revision":"听众哪里卡住、影响哪个决定，可空","targets":["段落编号"]}'
                    ),
                    revise=(
                        "你是 T04 作者。读者指出的是听懂或卡住的位置，你决定怎么改。用报告中实际展示的结果、动作或错例，"
                        "把测试和你的使用判断接起来。涉及样本、用量或把握程度时，把实测结果和你的判断各自讲清楚。"
                        "可以明确说自己倾向怎么用，解释理由。只处理 targets 的必要内容；原段落已经成立时原样返回。"
                        "保留核心例子、作者判断和主持人介绍，接好前后段。\n" + profile.revise.rsplit("\n", 1)[-1]
                    ),
                )
                if t04_revision_only:
                    from unittest.mock import patch

                    reading = editorial.FocusedReading.model_validate_json(
                        (t04_reader_replay / "reading.json").read_text()
                    )
                    replay = replace(
                        profile,
                        revise=(
                            "你是 T04 口播作者。只改 targets 里的内容，回应读者指出的那个问题。"
                            "先想清楚素材实际展示的动作和结果，再决定你会怎样使用它。"
                            "素材只展示到入口或操作时，讲清你会先试什么、看到什么结果才愿意交给它；"
                            "保留你的判断和理由。把读者给的意见当作理解线索，具体说法由你对照素材决定。"
                            "改到问题解决就停，接好原稿上下文。\n" + profile.revise.rsplit("\n", 1)[-1]
                        ),
                    )
                    model_call = editorial.call_json

                    def replay_reading(client, prompt, request, schema, *, temperature):
                        if schema is editorial.FocusedReading:
                            return reading
                        return model_call(client, prompt, request, schema, temperature=temperature)

                    with patch.object(editorial, "call_json", replay_reading):
                        final = editorial.revise_passage(draft, profile=replay, on_stage=capture, **inputs)
                else:
                    final = editorial.revise_passage(draft, profile=replay, on_stage=capture, **inputs)
                save(out / "first-revision-replay.json", {"source": str(t04_reader_replay),
                     "reader": replay.read, "revision": replay.revise,
                     "frozen_reading": t04_revision_only,
                     "receipt": final.meta["manuscript_workflow"]})
                return final
            if handoff:
                report_item = (video_workflow.report_of(script, str(dest))
                               if handoff in ("report-and-story", "comparison-evidence") else "")
                inputs, handoff_receipt = material_handoff(inputs, handoff, report_item=report_item)
                save(out / "material-handoff.json", {"mode": handoff, **handoff_receipt})
            final = run_author(**inputs, profile=profile,
                               on_stage=lambda stage, data: capture("initial" if stage == "direct" else stage, data))
            capture("after-edit", asdict(final))
            return final
        if direct_mode:
            if author_option and not inputs.get(author_option):
                raise RuntimeError(f"The writing entry did not activate {author_option}")
            if baseline_dir:
                original_receipt = json.loads((baseline_dir / "input-receipt.json").read_text())
                comparable = dict(receipt)
                if writer_model:
                    comparable["model"] = original_receipt["model"]
                if column_notes:
                    key = "column_style_sha256"
                    if comparable[key] == original_receipt[key]:
                        raise RuntimeError("The writer did not receive the changed column direction")
                    comparable[key] = original_receipt[key]
                if original_receipt != comparable:
                    raise RuntimeError("The previous prompt used different writer inputs")
                author_trial = (writer_model or spoken_examples or title_last or episode_story
                                or column_notes or omit_report_fields or case_sources or task_comparison or earned_ending)
                if author_trial:
                    baseline_omits_raw = (baseline_dir / "experiment.json").exists()
                    if baseline_omits_raw != (mode == "report-only" or omit_raw_material):
                        raise RuntimeError("The previous author used different original material")
                baseline_file = baseline_dir / ("generated.json" if author_trial else "direct.json")
                if not baseline_file.exists():
                    baseline_file = baseline_dir / "direct.json"
                shutil.copy2(baseline_file, out / "same-input.json")
                save(out / "baseline.json", {"source": str(baseline_file)})
            if mode == "report-only" or omit_raw_material:
                if not inputs.get("original_material"):
                    raise RuntimeError("Report-only comparison needs a report with separate original material")
                inputs = {**inputs, "original_material": ""}
                save(out / "experiment.json", {"input": "report_without_original_material", "report_chars": len(inputs["report"])})
            if omit_report_fields:
                report, counts = without_report_fields(inputs["report"], omit_report_fields)
                if not all(counts.values()):
                    raise RuntimeError(f"The expected report fields were not found: {counts}")
                save(out / "author-report.json", {"omitted_fields": counts, "author_report_chars": len(report),
                     "author_report_sha256": hashlib.sha256(report.encode()).hexdigest()})
                (out / "author-report.md").write_text(report)
                inputs = {**inputs, "report": report}
            if case_sources:
                client = LLMClient(inputs["llm_config"])
                notes, cases = case_source_notes(client, inputs["report"], inputs.get("original_material") or "")
                save(out / "author-cases.json", cases)
                if not notes:
                    raise RuntimeError("The trial found no cases with source excerpts")
                inputs = {**inputs, "editorial_context":
                          "## 案例的原文定位\n以下索引区分各案例的对象、动作和结果，详细依据仍见底稿与原材料。"
                          "作者按讲述需要取舍，自行判断案例说明了什么。\n" + notes}
            final = direct_author(**inputs)
            capture("direct", asdict(final))
            return final
        if replay_first_revision or replay_second_round:
            original_receipt = json.loads((baseline_dir / "input-receipt.json").read_text())
            if receipt != original_receipt:
                raise RuntimeError("The replay received different author inputs")
        if replay_first_revision:
            draft = writer.WrittenScript(**json.loads((baseline_dir / "same-input.json").read_text()))
            capture("same-input", asdict(draft))
            shutil.copy2(baseline_dir / "round-1.json", out / "original-round-1.json")
            reading_prompt = editorial.FOCUSED_READ
            if reader_column:
                previous_round = json.loads((baseline_dir / "author-rounds.json").read_text())
                previous_final = json.loads((baseline_dir / "generated.json").read_text())
                writing_direction = previous_final["meta"]["programme_direction"]
                if not writing_direction.strip():
                    raise RuntimeError("The trial needs the writing direction from the original run")
                replay_prompt = reading_prompt + "\n" + previous_round["directions"][0]
                replay_prompt += "\n\n## 本稿沿用的写作方向\n" + writing_direction
            else:
                replay_prompt = EXPLANATION_READ
            try:
                editorial.FOCUSED_READ = replay_prompt
                final = editorial.revise_passage(draft, **inputs, on_stage=capture)
            finally:
                editorial.FOCUSED_READ = reading_prompt
            save(out / "first-revision-replay.json", {"source": str(baseline_dir),
                 "reader": replay_prompt, "receipt": final.meta["manuscript_workflow"]})
            return final
        if replay_second_round:
            draft = writer.WrittenScript(**json.loads((baseline_dir / "round-1.json").read_text()))
            capture("same-input", asdict(draft))
            shutil.copy2(baseline_dir / "round-2.json", out / "original-round-2.json")
            if cut_second_round:
                client = LLMClient(inputs["llm_config"])
                final, cut_receipt = cut_repetition(draft, client, inputs["column"].voice_id or None)
                save(out / "second-round-replay.json", {"source": str(baseline_dir), "receipt": cut_receipt})
                return final
            read = editorial.FOCUSED_READ
            write = editorial.FOCUSED_WRITE
            reading_direction = (
                "本轮读的是已经改过一轮的稿。找仍影响听下去的一处，优先看目录、重复和转场。"
                "如果同一个判断或例子出现两次，明确把它留在哪一处，把哪里删短；"
                "若只需要换位置或删掉重复，就不用加新例子。可以留空。"
            )
            writing_direction = (
                "先对照反馈和相邻段落。反馈要求删重复时，把那层信息留在原来讲得最清楚的地方，"
                "在目标段直接省掉。用现有上下文接下去，不为填补删掉的篇幅另写预告。"
                "保留主持人身份和本轮无需改动的事实、例子与判断。"
            )
            try:
                editorial.FOCUSED_READ = read + "\n" + reading_direction
                editorial.FOCUSED_WRITE = write + "\n" + writing_direction
                final = editorial.revise_passage(draft, **inputs)
            finally:
                editorial.FOCUSED_READ, editorial.FOCUSED_WRITE = read, write
            save(out / "second-round-replay.json", {"source": str(baseline_dir),
                 "reader": reading_direction, "author": writing_direction,
                 "receipt": final.meta["manuscript_workflow"]})
            return final
        if editorial_rounds:
            draft = writer.write_for_column(**inputs)
            capture("same-input", asdict(draft))
            capture("direct", asdict(draft))
            directions = (
                "这一轮只关注正文理解。读完后找出最值得讲透的判断，检查依据是否讲在恰当的位置，"
                "有没有跳过关键一步。优先改能提升解释力的那处，保留有用的例子与条件。",
                "这一轮只关注听感。沿当前稿的钩子、主持人介绍、段落转场和结尾一路听，"
                "找最明显的重复、堆目录或生硬停顿。保留上轮已经讲清的判断和例子。",
            )
            if first_revision_only:
                directions = directions[:1]
            reader_prompt = editorial.FOCUSED_READ
            receipts = []
            try:
                for number, direction in enumerate(directions, 1):
                    editorial.FOCUSED_READ = reader_prompt + "\n" + direction
                    draft = editorial.revise_passage(draft, **inputs)
                    receipts.append(draft.meta["manuscript_workflow"])
                    capture(f"round-{number}", asdict(draft))
            finally:
                editorial.FOCUSED_READ = reader_prompt
            draft.meta["manuscript_workflow"] = {"mode": "focused_rounds", "rounds": receipts}
            save(out / "author-rounds.json", {"directions": directions, "rounds": receipts})
            return draft
        if baseline_dir is None and mode == "focused":
            final = run_author(**inputs, on_stage=capture)
            save(out / "editorial.json", final.meta["manuscript_workflow"])
            return final
        if baseline_dir:
            original_receipt = json.loads((baseline_dir / "input-receipt.json").read_text())
            if original_receipt != receipt:
                raise RuntimeError("The first round used different writer inputs")
            direct = writer.WrittenScript(**json.loads((baseline_dir / "direct.json").read_text()))
            save(out / "baseline.json", {"source": str(baseline_dir)})
        else:
            direct = writer.write_for_column(**inputs)
        capture("direct", asdict(direct))
        if mode == "focused":
            final = editorial.revise_passage(direct, on_stage=capture, **inputs)
        elif mode == "read-direct":
            final = editorial.read_and_revise(
                direct, client=LLMClient(inputs["llm_config"]), audience=audience, on_stage=capture, **inputs,
            )
        else:
            final = run_author(**inputs, on_stage=capture)
        save(out / "selection.json", final.meta["manuscript_workflow"])
        return final

    if direct_mode:
        writer.write_for_column = compare_from_same_input
    else:
        editorial.focused_author = compare_from_same_input
    if mode == "scoped-lead":
        author_opening = writer.author_episode_opening

        def capture_opening(script, *args, **kwargs):
            capture("before-entrance", {"sections": [section.model_dump() for section in script.sections[:2]]})
            return author_opening(script, *args, **kwargs)

        writer.author_episode_opening = capture_opening
    if fresh_entry:
        topic = {**script["topic"], "from_report": script["report_path"]}
        fresh = store.create_script(script["column_key"], topic, created_by="autopilot",
                                    production=script["production"], content_context=context)
        output_id = fresh["id"]
        previous_version = 0
        status = video_workflow.run(output_id, started_by="autopilot", llm_config=author_config)
    else:
        if column_notes:
            store.update_script(script_id, content_context=context)
        output_id = script_id
        previous_version = baseline["version"]
        status = video_workflow.run_rewrite(script_id, started_by="manuscript-trial", previous_status=script["status"],
                                            llm_config=author_config)
    current = store.get_script(output_id)
    if status != "DRAFT" or current["current_version"] <= previous_version:
        raise RuntimeError(current.get("error") or f"Rewrite ended in {status}")
    saved = store.get_version(output_id, current["current_version"])
    save(out / "saved-version.json", saved)
    if direct_mode or (mode == "focused" and (baseline_dir is None or replay_first_revision or replay_second_round
                                              or workflow_job)):
        fields = ("title", "sections", "length_check", "meta")
        save(out / "historical.json", {key: history[key] for key in fields})
        save(out / "generated.json", {key: saved[key] for key in fields})
        save(out / "comparison-receipt.json", {"historical_version": history["version"],
             "generated_version": saved["version"], "entry": "run" if fresh_entry else "run_rewrite",
             "script_id": output_id, "history_script_id": script_id})
        if not local_ollama:
            blind(out, prefix)
    elif any((out / f"{name}.json").exists() for name in ("planned", "revision")):
        blind(out, prefix)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("script_prefix")
    parser.add_argument("output", type=Path)
    parser.add_argument("--blind-only", action="store_true")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--fresh-entry", action="store_true")
    parser.add_argument("--local-ollama", action="store_true", help="Use the installed local model with no fallback")
    parser.add_argument("--local-port", type=int, default=11434, help="Loopback Ollama port")
    parser.add_argument("--local-model", choices=("qwen3-vl:8b-instruct-q4_K_M", "qwen3.5:9b-mlx"),
                        default="qwen3-vl:8b-instruct-q4_K_M", help="Installed local Ollama model")
    parser.add_argument("--local-json", action="store_true", help="Use the local endpoint's JSON response mode")
    parser.add_argument("--editorial-rounds", action="store_true", help="Revise the same draft for structure, then spoken flow")
    parser.add_argument("--first-revision-only", action="store_true", help="Stop after the structural revision")
    parser.add_argument("--replay-first-revision", action="store_true", help="Try an explanation-focused reader on the frozen draft")
    parser.add_argument("--reader-column", action="store_true", help="Give the first-round reader the existing writing direction")
    parser.add_argument("--t04-workflow", action="store_true", help="Compare a Job-specific T04 manuscript workflow")
    parser.add_argument("--job-workflow", help="Compare the manuscript profile for a specific research job")
    parser.add_argument("--material-handoff",
                        choices=("episode-items", "selected-story", "report-and-story", "comparison-evidence",
                                 "operation-paths"),
                        help="Compare how the automatically available report and story material reach the author")
    reader_replay = parser.add_mutually_exclusive_group()
    reader_replay.add_argument("--reader-report-replay", type=Path,
                               help="Compare a reader that also sees the report on a frozen Job draft")
    reader_replay.add_argument("--reader-opening-replay", type=Path,
                               help="Replay a reader who follows the actions from the opening")
    reader_replay.add_argument("--episode-opening-replay", type=Path,
                               help="Rewrite only the opening of a saved episode")
    reader_replay.add_argument("--edit-baseline", action="store_true",
                               help="Apply the Job reader and revision to the frozen production draft")
    parser.add_argument("--t04-reader-replay", type=Path, help="Try an observation-only reader on a frozen T04 draft")
    parser.add_argument("--t04-revision-only", action="store_true", help="Keep the previous reading and change only the revision")
    parser.add_argument("--t04-material-input", type=Path, help="Separate planning fields from the T04 initial author input")
    parser.add_argument("--replay-second-round", action="store_true", help="Compare a revised second round on the frozen first revision")
    parser.add_argument("--cut-second-round", action="store_true", help="Replay the second round by removing exact sentences only")
    parser.add_argument("--omit-raw-material", action="store_true",
                        help="Hold the scoped opening workflow fixed and supply only the report")
    parser.add_argument("--writer-model", choices=("minimax-m3",),
                        help="Override the author model on the configured review endpoint")
    parser.add_argument("--spoken-examples", action="store_true", help="Replace abstract style rules with spoken examples")
    parser.add_argument("--title-last", action="store_true", help="Write the title from the finished narration")
    parser.add_argument("--episode-story", action="store_true", help="Replace the programme's narrative instructions")
    parser.add_argument("--column-notes", help="Replace the frozen column writing direction in the trial database")
    parser.add_argument("--omit-report-field", action="append", default=[], help="Omit a labeled report field from author input")
    parser.add_argument("--case-sources", action="store_true", help="Index independent cases with verbatim source excerpts")
    parser.add_argument("--task-comparison", action="store_true", help="Follow a real task through the outcome that changes a decision")
    parser.add_argument("--earned-ending", action="store_true", help="End on the answer earned by the body")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--read-direct", action="store_true")
    mode.add_argument("--focused", action="store_true")
    mode.add_argument("--report-only", action="store_true")
    mode.add_argument("--scoped-lead", action="store_true")
    mode.add_argument("--production", action="store_true")
    args = parser.parse_args()
    if args.editorial_rounds and (not args.focused or args.baseline):
        parser.error("--editorial-rounds requires --focused and no external baseline")
    if args.first_revision_only and not args.editorial_rounds:
        parser.error("--first-revision-only requires --editorial-rounds")
    if args.replay_first_revision and (not args.focused or not args.baseline or args.replay_second_round):
        parser.error("--replay-first-revision requires --focused and the previous first revision directory")
    if args.reader_column and not args.replay_first_revision:
        parser.error("--reader-column requires --replay-first-revision")
    if args.t04_workflow and args.job_workflow:
        parser.error("Choose --t04-workflow or --job-workflow")
    if (args.t04_workflow or args.job_workflow) and (
        not args.focused or not args.baseline or args.editorial_rounds or args.replay_first_revision or args.replay_second_round
    ):
        parser.error("A job workflow requires --focused and a same-input baseline")
    if args.t04_reader_replay and not args.t04_workflow:
        parser.error("--t04-reader-replay requires --t04-workflow")
    if args.t04_revision_only and not args.t04_reader_replay:
        parser.error("--t04-revision-only requires --t04-reader-replay")
    if args.t04_material_input and (not args.t04_workflow or args.t04_reader_replay):
        parser.error("--t04-material-input requires --t04-workflow without a revision replay")
    if args.replay_second_round and (not args.focused or not args.baseline or args.editorial_rounds):
        parser.error("--replay-second-round requires --focused and the prior editorial rounds directory")
    if args.cut_second_round and not args.replay_second_round:
        parser.error("--cut-second-round requires --replay-second-round")
    if args.omit_raw_material and not args.scoped_lead:
        parser.error("--omit-raw-material requires --scoped-lead")
    if args.writer_model and (args.local_ollama or not args.baseline or not (args.production or args.scoped_lead)):
        parser.error("--writer-model requires a hosted direct-author trial and an existing baseline")
    if args.spoken_examples and (args.writer_model or not args.baseline or not (args.production or args.scoped_lead)):
        parser.error("--spoken-examples requires a direct-author trial and an existing baseline")
    if args.title_last and (args.writer_model or args.spoken_examples or not args.baseline
                            or not (args.production or args.scoped_lead)):
        parser.error("--title-last requires a direct-author trial with an existing baseline")
    if args.episode_story and (args.writer_model or args.spoken_examples or args.title_last or not args.baseline
                               or not (args.production or args.scoped_lead)):
        parser.error("--episode-story requires a direct-author trial with an existing baseline")
    if args.column_notes and (args.writer_model or args.spoken_examples or args.title_last or not args.baseline
                               or not (args.production or args.scoped_lead)):
        parser.error("--column-notes requires a direct-author trial with an existing baseline")
    if args.omit_report_field and (args.writer_model or args.spoken_examples or args.title_last or args.column_notes
                                    or not args.baseline or not args.production):
        parser.error("--omit-report-field requires a direct-author trial with an existing baseline")
    if args.case_sources and (args.writer_model or args.spoken_examples or args.title_last or args.column_notes
                              or not args.baseline or not args.production):
        parser.error("--case-sources requires a direct-author trial with an existing baseline")
    if args.task_comparison and (args.writer_model or args.spoken_examples or args.title_last or args.column_notes
                                 or args.case_sources or not args.baseline or not args.production):
        parser.error("--task-comparison requires a direct-author trial with an existing baseline")
    if args.earned_ending and (args.writer_model or args.spoken_examples or args.title_last or args.column_notes
                               or args.case_sources or args.task_comparison or args.episode_story
                               or not args.baseline or not args.production):
        parser.error("--earned-ending requires a direct-author trial with an existing baseline")
    if args.material_handoff and not args.job_workflow:
        parser.error("--material-handoff requires --job-workflow")
    if (args.reader_report_replay or args.reader_opening_replay or args.episode_opening_replay or args.edit_baseline) and (
        not (args.job_workflow and args.focused and args.baseline) or args.material_handoff
    ):
        parser.error("A reader replay requires --job-workflow, --focused and --baseline")
    out = args.output.resolve()
    if args.blind_only:
        setup(out)
        blind(out, args.script_prefix)
    else:
        modes = ("read_direct", "focused", "report_only", "scoped_lead", "production")
        selected_mode = next((name.replace("_", "-") for name in modes if getattr(args, name)), "planned")
        main(args.script_prefix, out, args.baseline, mode=selected_mode, fresh_entry=args.fresh_entry,
             local_ollama=args.local_ollama, omit_raw_material=args.omit_raw_material,
             writer_model=args.writer_model, spoken_examples=args.spoken_examples, title_last=args.title_last,
             episode_story=args.episode_story, column_notes=args.column_notes,
             omit_report_fields=tuple(args.omit_report_field), case_sources=args.case_sources,
             task_comparison=args.task_comparison, earned_ending=args.earned_ending,
             local_port=args.local_port, local_model=args.local_model, local_json=args.local_json,
             editorial_rounds=args.editorial_rounds, replay_second_round=args.replay_second_round,
             cut_second_round=args.cut_second_round, first_revision_only=args.first_revision_only,
             replay_first_revision=args.replay_first_revision, reader_column=args.reader_column,
             t04_workflow=args.t04_workflow, t04_reader_replay=args.t04_reader_replay,
             t04_revision_only=args.t04_revision_only, t04_material_input=args.t04_material_input,
             workflow_job=args.job_workflow, handoff=args.material_handoff,
             reader_report_replay=args.reader_report_replay, reader_opening_replay=args.reader_opening_replay,
             episode_opening_replay=args.episode_opening_replay, edit_baseline=args.edit_baseline)

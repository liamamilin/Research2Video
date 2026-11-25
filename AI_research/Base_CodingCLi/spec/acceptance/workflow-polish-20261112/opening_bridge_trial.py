"""Replay a single scoped introduction edit on a real saved draft."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sqlite3

from pydantic import BaseModel, Field

from opening_run import ROOT, save, setup


class Bridge(BaseModel):
    paragraph: int = Field(ge=0)
    after: str = Field(min_length=1)
    introduction: str = Field(min_length=1)


class Introduction(BaseModel):
    introduction: str = Field(min_length=1)


def name_sentence(identity: str):
    return re.compile(r"(^|(?<=[。！？!?]))\s*(?:(?:大家好|各位好)[，,]\s*)?"
                      + re.escape(identity) + r"\s*[。！!](?![”’\"'])")


def connect_in_place(script, persona, client):
    from core.video import persona as narrator
    from core.video.adapt.common import call_json

    opening = script.sections[0]
    identity = persona["introduce"].rstrip("。.!！ ")
    bare = name_sentence(identity)
    for index, paragraph in enumerate(opening.paragraphs):
        match = bare.search(paragraph.text)
        if match and (index > 0 or paragraph.text[:match.start()].strip()):
            break
    else:
        return script, {"status": "unchanged", "reason": "no_original_position"}
    before, following = paragraph.text[:match.start()], paragraph.text[match.end():]
    instruction = (
        f"只替换孤立的「{identity}。」这句话，前后原文照常播出。加自然问候，把介绍接到紧跟的那一句。"
        "后文已有「今天」「本期」和话题时，介绍用逗号接过去即可。"
        "后文直接进入例子时，可以用半句点一下整篇主题。只输出替换身份句的文字，保留结尾标点。"
        '输出 JSON：{"introduction":"替换句"}'
    )
    context = {"title": script.title, "before": before, "following": following,
               "following_titles": [part.title for part in script.sections[1:]]}
    response = call_json(client, instruction, json.dumps(context, ensure_ascii=False), Introduction, temperature=0.4)
    paragraphs = list(opening.paragraphs)
    paragraphs[index] = paragraph.model_copy(update={"text": before + response.introduction + following})
    record = {"instruction": instruction, "input": context, "reply": response.model_dump(), "status": "unchanged"}
    if narrator.opening_needs_introduction(paragraphs, persona):
        return script, record
    lead = opening.model_copy(update={"paragraphs": paragraphs})
    return script.model_copy(update={"sections": [lead, *script.sections[1:]]}), {**record, "status": "rewritten"}


def bridge_opening(script, persona, client):
    from core.video import persona as narrator
    from core.video.adapt.common import call_json

    opening = script.sections[0]
    identity = persona["introduce"].rstrip("。.!！ ")
    bare = name_sentence(identity)
    paragraphs = [p.model_copy(update={"text": bare.sub("", p.text).strip()}) for p in opening.paragraphs]
    context = {
        "title": script.title,
        "opening": dict(enumerate(p.text for p in paragraphs)),
        "following_titles": [part.title for part in script.sections[1:]],
    }
    instruction = (
        "给这篇中文口播补一条自然的主持人介绍。原有旁白会原样播出，不能改写或重复。"
        "选已经说完钩子的位置。paragraph 为段号，after 原样复制该位置前一整句的句尾（包括句号或问号）。"
        f"introduction 只写一句「大家好，{identity}，今天……」，补出这篇稿实际讲的问题。"
        "整期节目要介绍整期看点，下一句就会进入原稿。不要再讲一遍钩子，不列目录。"
        '输出 JSON：{"paragraph":0,"after":"原句句尾。","introduction":"新介绍。"}'
    )
    candidate = call_json(client, instruction, json.dumps(context, ensure_ascii=False), Bridge, temperature=0.4)
    record = {"instruction": instruction, "input": context, "reply": candidate.model_dump(), "status": "unchanged"}
    if candidate.paragraph >= len(paragraphs):
        return script, record
    paragraph = paragraphs[candidate.paragraph]
    if paragraph.text.count(candidate.after) != 1 or candidate.after[-1] not in "。！？!?":
        return script, record
    before, after = paragraph.text.split(candidate.after)
    intro = candidate.introduction.strip()
    paragraphs[candidate.paragraph] = paragraph.model_copy(
        update={"text": before + candidate.after + intro + after}
    )
    if narrator.opening_needs_introduction(paragraphs, persona):
        return script, record
    record["status"] = "rewritten"
    # A removed name-only paragraph has no speech to render. Its citations, if
    # any, stay associated with the repaired introduction.
    removed_links = [url for p in paragraphs if not p.text for url in p.citations]
    if removed_links:
        target = paragraphs[candidate.paragraph]
        paragraphs[candidate.paragraph] = target.model_copy(
            update={"citations": list(dict.fromkeys([*target.citations, *removed_links]))}
        )
    lead = opening.model_copy(update={"paragraphs": [p for p in paragraphs if p.text]})
    return script.model_copy(update={"sections": [lead, *script.sections[1:]]}), record


def main(out: Path, prefix: str, local_port: int | None, original_position: bool) -> None:
    if out.exists():
        raise FileExistsError(f"Use a fresh isolated directory: {out}")
    setup(out)
    from core.llm import LLMClient, LLMConfig
    from core.video import persona, store, writer
    from web.services import video_workflow

    with sqlite3.connect(f"file:{ROOT / 'state/video.db'}?mode=ro", uri=True) as db:
        (script_id,), = db.execute("SELECT id FROM scripts WHERE id LIKE ?", (prefix + "%",))
    saved = store.get_script(script_id)
    version = store.get_version(script_id, saved["approved_version"] or saved["current_version"])
    source = writer._Script.model_validate(version)
    save(out / "before.json", {"report": saved["report_path"], "script_id": script_id,
         "version": version["version"], "script": source.model_dump()})
    if local_port is not None:
        config = LLMConfig(model="qwen3.5:9b-mlx", base_url=f"http://127.0.0.1:{local_port}/v1",
                           request_timeout=180, max_tokens=2048, max_retries=0,
                           extra_body={"reasoning_effort": "none", "response_format": {"type": "json_object"}})
    else:
        config = video_workflow._llm_configs(None)[0]
    narrator = persona.load()
    if not original_position:
        client = LLMClient(config)
        old, old_receipt = writer.introduce_opening(source, narrator, client)
        save(out / "current.json", {"script": old.model_dump(), "receipt": old_receipt, "usage": dict(client.usage)})
    client = LLMClient(config)
    repair = connect_in_place if original_position else bridge_opening
    new, new_receipt = repair(source, narrator, client)
    save(out / "proposed.json", {"script": new.model_dump(), "receipt": new_receipt, "usage": dict(client.usage)})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("script_prefix")
    parser.add_argument("--local-port", type=int)
    parser.add_argument("--original-position", action="store_true")
    args = parser.parse_args()
    main(args.output.resolve(), args.script_prefix, args.local_port, args.original_position)

"""Compile a user-edited script without model rewriting or deduplication."""
import hashlib
import json
import re
from g2.content_video import source_units


def split_script(text: str) -> list[str]:
    chunks = []
    for paragraph in re.split(r"[\r\n]+", text):
        for sentence in re.split(r"(?<=[。！？!?；;])", paragraph):
            remaining = sentence.strip()
            while remaining:
                end = min(80, len(remaining))
                if len(remaining) > 80:
                    candidates = [remaining.rfind(mark, 0, 80) + 1 for mark in "，,、 "]
                    natural = max(candidates)
                    if natural >= 40:
                        end = natural
                part, remaining = remaining[:end].strip(), remaining[end:].strip()
                if part:
                    chunks.append(part)
    if not chunks:
        raise ValueError("讲稿不能为空")
    if re.sub(r"\s", "", "".join(chunks)) != re.sub(r"\s", "", text):
        raise ValueError("讲稿分句不得丢失或改写文字")
    return chunks


def plan(pipeline, source: str):
    saved = json.loads((pipeline.PROJECT_INPUT.parent / "input.json").read_text())
    title = saved.get("title") or "内容讲解"
    values = split_script(source)
    points = [{"id": f"c1_p{i+1}", "point": t, "source_quotes": [t]} for i,t in enumerate(values)]
    segments = [{"id": f"c1_n{i+1}", "text": t, "chapter_id": "c1", "point_ids": [points[i]["id"]], "source_quotes": [t]} for i,t in enumerate(values)]
    units = source_units(source)
    result = {"title": title, "core_message": title, "necessary_points": points,
              "omissions": [], "ambiguities": [], "segments": segments,
              "chapters": [{"id": "c1", "title": title[:32], "question": "用户修订的内容是什么？",
                            "source_unit_ids": [u["id"] for u in units], "points": points,
                            "narration_segment_ids": [s["id"] for s in segments]}]}
    pipeline.write_json(pipeline.ARTIFACTS / "narration-plan.json", result)
    pipeline.write_json(pipeline.ARTIFACTS / "authored-narration.json", {
        "mode": "user_authored", "text_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "model_rewrite": False, "whitespace_normalized_only": True, "segments": len(segments),
        "provenance": "修订讲稿是本版本制作依据；原始材料和原视频保留在父任务中。"})
    pipeline.write_fact_ledger(result, source)
    return result


def prepare(pipeline, source: str, form: str):
    units = source_units(source)
    brief = {"status": "ready", "requested_form": form, "selected_form": form, "recommended_form": form,
             "selection_mode": "manual", "reason": "沿用原视频形式，按用户修订后的讲稿逐句配音。",
             "issues": [], "ordered_unit_ids": [u["id"] for u in units], "excluded_units": [],
             "source_units": units, "source_sha256": hashlib.sha256(source.encode()).hexdigest(), "source_rewritten": False}
    pipeline.write_json(pipeline.ARTIFACTS / "input-brief.json", brief)
    (pipeline.ARTIFACTS / "prepared-source.txt").write_text(source + "\n", encoding="utf-8")
    return source, brief

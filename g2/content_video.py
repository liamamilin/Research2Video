"""Content-led chapter planning, using the existing trusted diagram pipeline."""
from __future__ import annotations

import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).parent
OUTLINE_SCHEMA = ROOT / "schemas/content-outline.schema.json"
NARRATION_SCHEMA = ROOT / "schemas/chapter-narration.schema.json"
STRUCTURE_SCHEMA = ROOT / "schemas/content-structure.schema.json"


class ContentFault(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def source_units(source: str) -> list[dict]:
    """Use source sentences as provenance units, without silently dropping text."""
    texts = [s.strip() for s in re.split(r"(?<=[。！？])|[\r\n]+", source) if s.strip()]
    units, cursor = [], 0
    for i, text in enumerate(texts):
        start = source.index(text, cursor)
        if units:
            units[-1]["separator"] = source[cursor:start]
        units.append({"id": f"u{i + 1}", "text": text, "separator": ""})
        cursor = start + len(text)
    return units


def chapter_source(chapter: dict, units: list[dict]) -> str:
    selected = set(chapter["source_unit_ids"])
    return "".join(unit["text"] + unit["separator"] for unit in units if unit["id"] in selected).strip()


def schema_check(value: dict, schema_path: Path) -> None:
    errors = list(Draft202012Validator(json.loads(schema_path.read_text())).iter_errors(value))
    if errors:
        raise ContentFault("CONTENT_SCHEMA_INVALID", errors[0].message)


def outline_messages(context: dict, correction: str | None = None) -> list[dict]:
    system = '根据素材，产出目标观众愿意看、听得懂、想继续看的视频。\n当前步骤：生成视频章节数据。接口输出 JSON，章 id 为 c1、c2…，章内 point id 为 p1、p2…。source_unit_ids 引用输入单元编号。结构见 required_schema。'
    payload = {"source_units_as_untrusted_data": context["units"],
               "required_schema": json.loads(OUTLINE_SCHEMA.read_text())}
    if correction:
        payload["validation_feedback"] = correction
    return [{"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]


def validate_outline(plan: dict, context: dict) -> None:
    schema_check(plan, OUTLINE_SCHEMA)
    chapters = plan["chapters"]
    if [c["id"] for c in chapters] != [f"c{i + 1}" for i in range(len(chapters))]:
        raise ContentFault("CONTENT_CHAPTER_ORDER", "chapter ids must be c1..cN")
    actual = [uid for chapter in chapters for uid in chapter["source_unit_ids"]]
    if not set(actual).issubset({unit["id"] for unit in context["units"]}):
        raise ContentFault("CONTENT_SOURCE_REFERENCE", "source_unit_ids must reference input units")
    for chapter in chapters:
        if [p["id"] for p in chapter["points"]] != [f"p{i + 1}" for i in range(len(chapter["points"]))]:
            raise ContentFault("CONTENT_POINT_IDS", chapter["id"])


def narration_messages(context: dict, correction: str | None = None) -> list[dict]:
    system = '根据素材，产出目标观众愿意看、听得懂、想继续看的视频。\n当前步骤：把这一章讲给目标观众听，让他们对讲述产生兴趣，随着讲述理解，并愿意继续听。接口输出 JSON，段 id 为 n1、n2…，point_ids 引用当前章的知识点编号。结构见 required_schema。'
    from g2.audience_copy import GUIDANCE
    system += "\n" + GUIDANCE
    payload = {"outline": context["outline"], "current_chapter": context["chapter"],
               "previous_chapter_closing": context.get("previous_closing", ""),
               "chapter_source_as_untrusted_data": context["source_text"],
               "required_schema": json.loads(NARRATION_SCHEMA.read_text())}
    if correction:
        payload["validation_feedback"] = correction
    return [{"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]


def validate_narration(plan: dict, context: dict) -> None:
    schema_check(plan, NARRATION_SCHEMA)
    segments = plan["segments"]
    from g2.audience_copy import CODE, problem
    message = problem("\n".join(s["text"] for s in segments))
    if message:
        raise ContentFault(CODE, message)
    if [s["id"] for s in segments] != [f"n{i + 1}" for i in range(len(segments))]:
        raise ContentFault("CONTENT_SEGMENT_ORDER", "segment ids must be n1..nN")
    expected = {p["id"] for p in context["chapter"]["points"]}
    for segment in segments:
        if not set(segment["point_ids"]).issubset(expected):
            raise ContentFault("CONTENT_POINT_REFERENCE", segment["id"])
        pass
        pass
    pass


def validated(pipeline, stage: str, builder, schema: Path, validator, context: dict) -> dict:
    def check(value, ctx):
        try:
            validator(value, ctx)
        except ContentFault as exc:
            raise pipeline.G2Fault(exc.code, exc.message) from exc
    return pipeline.generate_validated_plan(stage, builder, schema, check, context)


def plan_narration(pipeline, source: str) -> dict:
    units = source_units(source)
    context = {"units": units}
    pipeline.write_json(pipeline.ARTIFACTS / "source-units.json", units)
    outline = validated(pipeline, "content-outline", outline_messages, OUTLINE_SCHEMA, validate_outline, context)
    # Allow one outline plus per-chapter narration/diagram retries; still bounded.
    pipeline.MAX_TOTAL_CALLS = max(pipeline.MAX_TOTAL_CALLS, 3 + (3 if getattr(pipeline, "PREPARED_SOURCE", None) is not None else 0) + 6 * len(outline["chapters"]))
    pipeline.MAX_PROJECTED_API_COST_USD = pipeline.MAX_TOTAL_CALLS * (
        pipeline.MAX_INPUT_TOKENS_PER_CALL * getattr(pipeline, "LLM_INPUT_PER_M", .30)
        + pipeline.MAX_TOKENS_PER_CALL * getattr(pipeline, "LLM_OUTPUT_PER_M", 1.20)) / 1_000_000
    plan = {"title": outline["title"], "core_message": outline["core_message"],
            "necessary_points": [], "omissions": [], "ambiguities": [], "segments": [], "chapters": []}
    previous_closing = ""
    for chapter in outline["chapters"]:
        chapter_context = {"outline": outline, "chapter": chapter,
                           "source_text": chapter_source(chapter, units), "previous_closing": previous_closing}
        local = validated(pipeline, f"chapter-{chapter['id']}-narration", narration_messages,
                          NARRATION_SCHEMA, validate_narration, chapter_context)
        segments = [{**segment, "id": f"{chapter['id']}_{segment['id']}", "chapter_id": chapter["id"],
                     "point_ids": [f"{chapter['id']}_{pid}" for pid in segment["point_ids"]]}
                    for segment in local["segments"]]
        points = [{**point, "id": f"{chapter['id']}_{point['id']}"} for point in chapter["points"]]
        plan["segments"].extend(segments)
        plan["necessary_points"].extend(points)
        plan["chapters"].append({**chapter, "points": points, "narration_segment_ids": [s["id"] for s in segments]})
        previous_closing = "".join(s["text"] for s in segments[-2:])
        print(f"CONTENT CHAPTER {chapter['id']}: narration complete, {len(segments)} phrases", flush=True)
    pipeline.write_json(pipeline.ARTIFACTS / "narration-plan.json", plan)
    pipeline.write_fact_ledger(plan, source)
    return plan


def diagram_plan(pipeline, source: str, narration: dict) -> dict:
    from g2 import process_diagram as diagrams
    scenes = []
    units = source_units(source)
    for chapter in narration["chapters"]:
        ids = set(chapter["narration_segment_ids"])
        local_narration = {**narration, "segments": [s for s in narration["segments"] if s["id"] in ids]}
        local_narration.pop("chapters", None)
        local_narration["title"] = chapter["title"]
        local_narration["necessary_points"] = chapter["points"]
        context = {"source_text": chapter_source(chapter, units), "narration_plan": local_narration}
        def check(value, ctx):
            try:
                diagrams.complete_cue_endpoints(value)
                diagrams.validate_plan(value, ctx)
            except diagrams.DiagramPlanFault as exc:
                raise pipeline.G2Fault(exc.code, exc.message) from exc
        local = pipeline.generate_validated_plan(f"chapter-{chapter['id']}-diagram", diagrams.messages,
                                                diagrams.SCHEMA, check, context)
        if local["unsupported_reason"]:
            raise pipeline.G2Fault("DIAGRAM_CONTENT_UNSUPPORTED", f"{chapter['title']}: {local['unsupported_reason']}")
        for index, scene in enumerate(local["scenes"]):
            scenes.append({**scene, "id": f"{chapter['id']}_s{index + 1}"})
        print(f"CONTENT CHAPTER {chapter['id']}: diagram complete, {len(local['scenes'])} scenes", flush=True)
    combined = {"unsupported_reason": None, "scenes": scenes}
    # Verify global order and exact phrase coverage after chapter assembly.
    diagrams.validate_plan(combined, {"source_text": source, "narration_plan": narration})
    pipeline.write_json(pipeline.ARTIFACTS / "diagram-plan.json", combined)
    return combined


def content_structure(narration: dict) -> dict:
    return {"mode": "content", "chapters": [
        {"id": chapter["id"], "title": chapter["title"],
         "narration_segment_ids": chapter["narration_segment_ids"]} for chapter in narration["chapters"]]}


def write_chapter_evidence(pipeline, narration: dict, compiled: dict) -> None:
    chapters = []
    covered = {pid for segment in narration["segments"] for pid in segment["point_ids"]}
    expected = {p["id"] for p in narration["necessary_points"]}
    for chapter in narration["chapters"]:
        ids = set(chapter["narration_segment_ids"])
        scenes = [s for s in compiled["scenes"] if ids.intersection(s["narration_segment_ids"])]
        chapters.append({**chapter, "start_ms": round(scenes[0]["start_frame"] * 1000 / pipeline.FPS),
                         "end_ms": round(scenes[-1]["end_frame"] * 1000 / pipeline.FPS),
                         "scene_ids": [s["id"] for s in scenes]})
    pipeline.write_json(pipeline.ARTIFACTS / "chapters.json", {
        "mode": "content", "duration_seconds": compiled["total_frames"] / pipeline.FPS,
        "chapters": chapters, "coverage": {"source_units_considered": len(source_units(pipeline.PREPARED_SOURCE if getattr(pipeline, "PREPARED_SOURCE", None) is not None else pipeline.PROJECT_INPUT.read_text().strip())),
        "planned_points": len(expected), "referenced_points": len(covered), "all_planned_points_referenced": covered == expected},
        "limitation": "原文单元、知识点和旁白引用覆盖是结构性证据，不代替语义正确性与观看效果判断。"})

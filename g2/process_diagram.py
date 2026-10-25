"""Source-grounded process diagrams using the registered flow_diagram component."""
from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator
from PIL import ImageFont

from g2.technical_text import altered_technical_text, display_width, wrap_units

SCHEMA = Path(__file__).parent / "schemas/process-diagram-plan.schema.json"
LAYOUT_SCRIPT = Path(__file__).parent / "layout/compile.cjs"
FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"


class DiagramPlanFault(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def messages(context: dict, correction: str | None = None) -> list[dict]:
    system = '根据素材，产出目标观众愿意看、听得懂、想继续看的视频。\n当前步骤：把讲述里的过程或关系变成看得懂的图解，让观众随着讲述看见各部分怎样连接。接口输出 JSON，使用给定 schema。每段既有旁白按顺序映射到一个场景；节点与边 id 唯一，边的 from/to 引用本场节点。cues.quote 是对应旁白的连续片段，用于音画定位；cues 引用本场节点和边，边同时关联两端节点。'
    from g2.content_video import source_units
    payload = {"source_text_as_untrusted_data": context["source_text"],
               "source_quote_candidates": [u["text"] for u in source_units(context["source_text"])],
               "narration_plan": context["narration_plan"],
               "required_schema": json.loads(SCHEMA.read_text())}
    if correction:
        payload["validation_feedback"] = correction
    return [{"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]


def validate_plan(plan: dict, context: dict) -> None:
    errors = list(Draft202012Validator(json.loads(SCHEMA.read_text())).iter_errors(plan))
    if errors:
        raise DiagramPlanFault("INVALID_DIAGRAM_PLAN", errors[0].message)
    if plan["unsupported_reason"]:
        if plan["scenes"]:
            raise DiagramPlanFault("INVALID_DIAGRAM_PLAN", "unsupported output must have no scenes")
        return
    narration = context["narration_plan"]["segments"]
    expected = [s["id"] for s in narration]
    actual = [sid for scene in plan["scenes"] for sid in scene["narration_segment_ids"]]
    if actual != expected:
        raise DiagramPlanFault("DIAGRAM_COVERAGE", "cover every narration segment once, in order")
    text = {s["id"]: s["text"] for s in narration}
    reference = "\n".join([context["source_text"], *text.values()])
    seen_scenes = set()
    for scene in plan["scenes"]:
        if scene["id"] in seen_scenes:
            raise DiagramPlanFault("DUPLICATE_DIAGRAM_ID", scene["id"])
        seen_scenes.add(scene["id"])
        nodes = {n["id"]: n for n in scene["nodes"]}
        edges = {e["id"]: e for e in scene["edges"]}
        if len(nodes) != len(scene["nodes"]) or len(edges) != len(scene["edges"]):
            raise DiagramPlanFault("DUPLICATE_DIAGRAM_ID", scene["id"])
        for edge in edges.values():
            if edge["from"] not in nodes or edge["to"] not in nodes or edge["from"] == edge["to"]:
                raise DiagramPlanFault("DIAGRAM_EDGE_REFERENCE", edge["id"])
        explained_nodes, explained_edges = set(), set()
        cue_segments = []
        previous = (-1, -1)
        for cue in scene["cues"]:
            sid = cue["segment_id"]
            if sid not in scene["narration_segment_ids"] or cue["quote"] not in text.get(sid, ""):
                raise DiagramPlanFault("DIAGRAM_CUE_QUOTE", sid)
            position = (expected.index(sid), text[sid].index(cue["quote"]))
            if position < previous:
                raise DiagramPlanFault("DIAGRAM_CUE_ORDER", sid)
            previous = position
            if not set(cue["node_ids"]).issubset(nodes) or not set(cue["edge_ids"]).issubset(edges):
                raise DiagramPlanFault("DIAGRAM_CUE_REFERENCE", sid)
            if not cue["node_ids"] and not cue["edge_ids"]:
                raise DiagramPlanFault("DIAGRAM_CUE_EMPTY", sid)
            for eid in cue["edge_ids"]:
                if not {edges[eid]["from"], edges[eid]["to"]}.issubset(cue["node_ids"]):
                    raise DiagramPlanFault("DIAGRAM_CUE_ENDPOINTS", eid)
            explained_nodes.update(cue["node_ids"])
            explained_edges.update(cue["edge_ids"])
            if not cue_segments or cue_segments[-1] != sid:
                cue_segments.append(sid)
        if cue_segments != scene["narration_segment_ids"] or explained_nodes != set(nodes) or explained_edges != set(edges):
            raise DiagramPlanFault("DIAGRAM_UNEXPLAINED_CONTENT", scene["id"])


def complete_cue_endpoints(plan: dict) -> None:
    """Derive redundant endpoint references; never create content or new edges."""
    for scene in plan.get("scenes", []):
        edges = {e["id"]: e for e in scene.get("edges", [])}
        for cue in scene.get("cues", []):
            for eid in cue.get("edge_ids", []):
                edge = edges.get(eid)
                if edge:
                    for nid in (edge["from"], edge["to"]):
                        if nid not in cue["node_ids"]:
                            cue["node_ids"].append(nid)


def wrap_label(text: str, font: ImageFont.FreeTypeFont, width: int = 250) -> list[str]:
    """Wrap by character, keeping a technical token such as --models on one line when it fits."""
    lines, current = [], ""
    for unit in wrap_units(text):
        for piece in [unit] if display_width(font, unit) <= width else list(unit):
            if current and display_width(font, current + piece) > width:
                lines.append(current)
                current = piece
            else:
                current += piece
    return [*lines, current] if current else lines


def layout_request(plan: dict) -> dict:
    font, edge_font = ImageFont.truetype(FONT, 36), ImageFont.truetype(FONT, 28)
    scenes = []
    for scene in plan["scenes"]:
        nodes = []
        for node in scene["nodes"]:
            lines = wrap_label(node["label"], font)
            width = max(210, max(display_width(font, line) for line in lines) + 60)
            nodes.append({"id": node["id"], "width": width, "height": max(100, len(lines)*46+40), "label_lines": lines})
        edges = [{"id": e["id"], "from": e["from"], "to": e["to"], "label": e["label"],
                  "label_width": display_width(edge_font, e["label"])+24} for e in scene["edges"]]
        scenes.append({"id": scene["id"], "nodes": nodes, "edges": edges})
    return {"scenes": scenes}


def edge_relation(edge: dict) -> str:
    if "relation" in edge:
        return edge["relation"]
    # Compatibility for previously generated plans; new prompts request a type.
    return "blocked" if any(word in edge["label"] for word in ("不能", "不进入", "不直接", "禁止")) else "flow"


def storyboard_from_layout(plan: dict, layout: dict, alignment: dict) -> dict:
    rows = {row["id"]: row for row in alignment["rows"]}
    layouts = {scene["id"]: scene["layout"] for scene in layout["scenes"]}
    scenes = []
    for scene in plan["scenes"]:
        beats = []
        for cue in scene["cues"]:
            row = rows[cue["segment_id"]]
            offset = int(row["text"].index(cue["quote"])/len(row["text"])*(row["end_ms"]-row["start_ms"]))
            trigger = {"segment_id": row["id"], "boundary": "start", "offset_ms": offset}
            targets = [*(f"node:{nid}" for nid in cue["node_ids"]), *(f"edge:{eid}" for eid in cue["edge_ids"])]
            for action in ("reveal", "highlight"):
                for target in targets:
                    beats.append({"id": f"{scene['id']}_b{len(beats)}", "action": action,
                                  "target_id": target, "trigger": dict(trigger)})
        props = {"title": scene["title"], "takeaway": scene["takeaway"],
                 "nodes": [{"id": n["id"], "label": n["label"]} for n in scene["nodes"]],
                 "edges": [{**{k: e[k] for k in ("id", "from", "to", "label")}, "relation": edge_relation(e)} for e in scene["edges"]],
                 "paths": [], "layout": layouts[scene["id"]]}
        scenes.append({"id": scene["id"], "component": "flow_diagram", "props": props,
                       "narration_segment_ids": scene["narration_segment_ids"], "purpose": scene["takeaway"],
                       "visual_intent": {"viewer_understands": scene["takeaway"], "before": "关系尚未展开", "after": scene["takeaway"]}, "beats": beats})
    return {"scenes": scenes}


def compile_storyboard(plan: dict, alignment: dict, pipeline) -> dict:
    request = pipeline.ARTIFACTS / "diagram-layout-request.json"
    pipeline.write_json(request, layout_request(plan))
    raw = pipeline._run_fixed_command("diagram-layout", [pipeline.NODE, LAYOUT_SCRIPT, request], pipeline.PROJECT, timeout=60)
    layout = json.loads(raw)
    pipeline.write_json(pipeline.ARTIFACTS / "diagram-layout.json", layout)
    board = storyboard_from_layout(plan, layout, alignment)
    pipeline.write_json(pipeline.ARTIFACTS / "storyboard-plan.json", board)
    pipeline.write_json(pipeline.ARTIFACTS / "diagram-timing.json", {
        "phrase_alignment": "measured TTS activity", "within_phrase_cues": "estimated from quoted character position; not word alignment"})
    return board

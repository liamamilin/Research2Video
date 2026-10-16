"""Strict Video IR v0.2 validator and millisecond-to-frame compiler.

Model-facing data remains JSON. This module validates it before it can reach the
fixed allowlisted Remotion component switch. It never evaluates IR strings.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from PIL import ImageFont


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas/video-ir-v0.2.schema.json"
REGISTRY_PATH = ROOT / "schemas/component-registry-v0.2.json"
COMPONENT_SCHEMA_DIR = ROOT / "schemas/components"
FPS = 30
MAX_SUBTITLE_WIDTH = 1680
SUBTITLE_FONT_SIZE = 46
SUBTITLE_FONT_PATH = "/System/Library/Fonts/Hiragino Sans GB.ttc"


class ValidationFault(ValueError):
    def __init__(self, code: str, field_path: str, message: str):
        self.code = code
        self.field_path = field_path
        self.message = message
        super().__init__(f"{code} at {field_path}: {message}")


def frame_at(ms: int, fps: int = FPS) -> int:
    """Half-up rounding on an absolute nonnegative time boundary."""
    if isinstance(ms, bool) or not isinstance(ms, int) or ms < 0:
        raise ValueError("ms must be a nonnegative integer")
    if isinstance(fps, bool) or not isinstance(fps, int) or fps <= 0:
        raise ValueError("fps must be a positive integer")
    return (ms * fps + 500) // 1000


def _schema(path: Path) -> dict[str, Any]:
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


def _pointer(parts) -> str:
    def escape(value):
        return str(value).replace("~", "~0").replace("/", "~1")
    return "/" + "/".join(escape(x) for x in parts)


def _fail(code: str, path: str, message: str):
    raise ValidationFault(code, path, message)


def _unique(items, key, path, label):
    seen = set()
    for index, item in enumerate(items):
        value = key(item)
        if value in seen:
            _fail("DUPLICATE_ID", f"{path}/{index}", f"duplicate {label}: {value}")
        seen.add(value)
    return seen


def _validate_component(component: str, props: dict[str, Any], path: str):
    schema_path = COMPONENT_SCHEMA_DIR / f"{component}.schema.json"
    validator = Draft202012Validator(_schema(schema_path))
    errors = sorted(validator.iter_errors(props), key=lambda e: tuple(str(x) for x in e.path))
    if errors:
        error = errors[0]
        _fail("INVALID_COMPONENT_PROPS", _pointer([*path.strip("/").split("/"), "props", *error.path]),
              error.message)


def _asset_path(project_root: Path, raw_path: str, field_path: str) -> Path:
    if not re.fullmatch(r"assets/(?:[a-zA-Z0-9_-]+/)*[a-zA-Z0-9_-]+(?:\.[a-zA-Z0-9_-]+)*", raw_path):
        _fail("INVALID_ASSET_PATH", field_path, "asset path must be a relative assets/... path")
    current = project_root
    for part in raw_path.split("/"):
        current = current / part
        if current.is_symlink():
            _fail("ASSET_SYMLINK", field_path, "symbolic links are not accepted in asset paths")
    resolved_root = project_root.resolve(strict=True)
    try:
        resolved = current.resolve(strict=True)
    except FileNotFoundError:
        _fail("ASSET_MISSING", field_path, "asset file does not exist")
    if not resolved.is_relative_to(resolved_root) or not resolved.is_file():
        _fail("ASSET_PATH_ESCAPE", field_path, "asset does not resolve to a file under the fixture root")
    return resolved


def _validate_targets(component, props, action, target, path):
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    entries = registry["components"].get(component)
    if not entries:
        _fail("UNKNOWN_COMPONENT", f"{path}/component", component)
    allowed = entries["actions"].get(action)
    if not allowed:
        _fail("UNSUPPORTED_ACTION", f"{path}/action", f"{component} does not support {action}")
    if target not in allowed and not any(token.endswith(":*") and target.startswith(token[:-1])
                                         for token in allowed):
        _fail("INVALID_ACTION_TARGET", f"{path}/target_id", f"{target} is not allowed for {component}.{action}")

    exists = False

    def numeric_target_index() -> int:
        suffix = target.split(":", 1)[1]
        if not suffix.isascii() or not suffix.isdecimal():
            _fail("INVALID_ACTION_TARGET", f"{path}/target_id", f"{target} must use a non-negative integer index")
        return int(suffix)

    if target == "scene":
        exists = True
    elif target in {"title", "subtitle"}:
        exists = ("title" in props and target == "title") or ("subtitle" in props and target == "subtitle")
        if component == "concept_reveal" and target == "title":
            exists = "title" in props
    elif target == "diff":
        exists = bool(props.get("highlights"))
    elif target.startswith("node:"):
        exists = target.split(":", 1)[1] in {item["id"] for item in props.get("nodes", [])}
    elif target.startswith("edge:"):
        exists = target.split(":", 1)[1] in {item["id"] for item in props.get("edges", [])}
    elif target.startswith("path:"):
        exists = target.split(":", 1)[1] in {item["id"] for item in props.get("paths", [])}
    elif target.startswith("line:"):
        index = numeric_target_index()
        exists = index < len(props.get("lines", []))
    elif target.startswith("side:"):
        exists = target.split(":", 1)[1] in {"left", "right"} and target.split(":", 1)[1] in props
    elif target.startswith("event:"):
        exists = target.split(":", 1)[1] in {item["id"] for item in props.get("events", [])}
    elif target.startswith("bar:"):
        index = numeric_target_index()
        exists = index < min(len(props.get("categories", [])), len(props.get("values", [])))
    if not exists:
        _fail("INVALID_BEAT_TARGET", f"{path}/target_id", f"target {target} does not exist in component props")


def _validate_asset_content(asset: dict, actual_path: Path, field_path: str):
    if actual_path.suffix.lower() not in {".wav", ".mp3", ".aac", ".m4a", ".png", ".jpg", ".jpeg", ".mp4"}:
        _fail("INVALID_ASSET_TYPE", f"{field_path}/path", f"unsupported media extension: {actual_path.suffix}")
    digest = hashlib.sha256(actual_path.read_bytes()).hexdigest()
    if digest != asset["sha256"]:
        _fail("ASSET_HASH_MISMATCH", f"{field_path}/sha256", "asset SHA-256 does not match file contents")


def validate_video_ir(ir: dict[str, Any], project_root: Path) -> dict[str, Any]:
    """Validate schema, media references, component semantics, and deterministic time."""
    schema = _schema(SCHEMA_PATH)
    # Preserve the frozen MVP schema. Only a validated, laid-out flow diagram
    # supports additional cues throughout a measured phrase, beyond 1 second.
    if isinstance(ir, dict) and "presentation" not in ir and isinstance(ir.get("scenes"), list) and any(
            isinstance(s, dict) and s.get("component") == "flow_diagram"
            and isinstance(s.get("props"), dict) and "layout" in s["props"] for s in ir["scenes"]):
        original_scene = schema["properties"]["scenes"]["items"]
        diagram_scene = copy.deepcopy(original_scene)
        diagram_scene["properties"]["component"] = {"const": "flow_diagram"}
        diagram_scene["properties"]["props"]["required"] = ["layout"]
        beats = diagram_scene["properties"]["beats"]
        beats.pop("maxItems", None)
        beats["items"]["properties"]["trigger"]["properties"]["offset_ms"].pop("maximum", None)
        schema["properties"]["scenes"]["items"] = {"anyOf": [original_scene, diagram_scene]}
    information = isinstance(ir, dict) and ir.get("presentation") == {"preset": "information_motion"}
    if isinstance(ir, dict) and "presentation" in ir:
        schema["properties"]["presentation"] = {"type": "object", "additionalProperties": False,
            "required": ["preset"], "properties": {"preset": {"const": "information_motion"}}}
        if information:
            schema["properties"]["style"]["properties"]["preset"] = {"const": "minimal_light"}
            motion_scene = copy.deepcopy(schema["properties"]["scenes"]["items"])
            motion_scene["properties"]["component"] = {"enum": ["title_card", "concept_reveal", "comparison"]}
            motion_scene["properties"]["beats"].pop("maxItems", None)
            motion_scene["properties"]["beats"]["items"]["properties"]["action"] = {"const": "reveal"}
            motion_scene["properties"]["beats"]["items"]["properties"]["trigger"]["properties"]["offset_ms"].pop("maximum", None)
            schema["properties"]["scenes"]["items"] = motion_scene
            schema["properties"]["scenes"].pop("maxItems", None)
    if isinstance(ir, dict) and "content_structure" in ir:
        # Explicit runtime extension; the frozen MVP contract remains unchanged.
        schema["properties"]["content_structure"] = _schema(ROOT / "g2/schemas/content-structure.schema.json")
        schema["properties"]["video"]["properties"]["duration_ms"].pop("maximum", None)
        schema["properties"]["video"]["properties"]["duration_ms"]["minimum"] = 1
        schema["properties"]["scenes"].pop("maxItems", None)
        if not isinstance(ir.get("scenes"), list) or not ir["scenes"] or any(not isinstance(s, dict) or not (
                (information and s.get("component") in {"title_card", "concept_reveal", "comparison"})
                or (not information and s.get("component") == "flow_diagram" and isinstance(s.get("props"), dict) and "layout" in s["props"])) for s in ir["scenes"]):
            _fail("CONTENT_COMPONENT_UNSUPPORTED", "/scenes", "chapter extension requires laid-out flow diagrams or explicit information motion")
    if isinstance(ir, dict) and "playback" in ir:
        schema["properties"]["playback"] = {"type":"object","additionalProperties":False,
            "required":["speed","source_duration_ms","method"],"properties":{
                "speed":{"enum":[.5,1,1.5,2]},"source_duration_ms":{"type":"integer","minimum":1},
                "method":{"const":"pitch-preserving audio tempo; synchronized captions and animation"}}}
        schema["properties"]["video"]["properties"]["duration_ms"].pop("maximum",None)
        schema["properties"]["video"]["properties"]["duration_ms"]["minimum"] = 1
    errors = sorted(Draft202012Validator(schema).iter_errors(ir), key=lambda e: tuple(str(x) for x in e.path))
    if errors:
        error = errors[0]
        _fail("SCHEMA_VALIDATION", _pointer(error.path), error.message)
    if "playback" in ir:
        expected=ir["playback"]["source_duration_ms"]/ir["playback"]["speed"]
        if abs(ir["video"]["duration_ms"]-expected)>max(200,expected*.02):
            _fail("PLAYBACK_DURATION_MISMATCH","/playback","retimed duration does not match the selected speed")
    project_root = Path(project_root)
    if not project_root.is_dir():
        _fail("INVALID_PROJECT_ROOT", "/", "fixture root must be an existing directory")
    original_text = ir["source"].get("original_text")
    if original_text is not None:
        source_digest = hashlib.sha256(original_text.encode("utf-8")).hexdigest()
        if source_digest != ir["source"]["text_sha256"]:
            _fail("SOURCE_HASH_MISMATCH", "/source/text_sha256", "source text SHA-256 does not match original_text")

    asset_ids = _unique(ir["assets"], lambda x: x["id"], "/assets", "asset id")
    for index, asset in enumerate(ir["assets"]):
        actual = _asset_path(project_root, asset["path"], f"/assets/{index}/path")
        _validate_asset_content(asset, actual, f"/assets/{index}")
    narration = ir["narration"]
    if narration["audio_asset_id"] not in asset_ids:
        _fail("MISSING_AUDIO_ASSET", "/narration/audio_asset_id", narration["audio_asset_id"])
    audio = next(a for a in ir["assets"] if a["id"] == narration["audio_asset_id"])
    if audio["type"] != "audio":
        _fail("INVALID_AUDIO_ASSET", "/narration/audio_asset_id", "narration asset must be audio")

    segment_ids = _unique(narration["segments"], lambda x: x["id"], "/narration/segments", "segment id")
    structure = ir.get("content_structure")
    if structure:
        chapters = structure["chapters"]
        if [c["id"] for c in chapters] != [f"c{i+1}" for i in range(len(chapters))]:
            _fail("CONTENT_CHAPTER_ORDER", "/content_structure", "chapter ids must be sequential")
        coverage = [sid for c in chapters for sid in c["narration_segment_ids"]]
        if coverage != [s["id"] for s in narration["segments"]]:
            _fail("CONTENT_NARRATION_COVERAGE", "/content_structure", "chapters must cover narration once, in order")
        scene_coverage = [sid for s in ir["scenes"] for sid in s["narration_segment_ids"]]
        if scene_coverage != coverage:
            _fail("CONTENT_SCENE_COVERAGE", "/scenes", "scenes must cover narration once, in order")
        owner = {sid: c["id"] for c in chapters for sid in c["narration_segment_ids"]}
        if any(len({owner[sid] for sid in s["narration_segment_ids"]}) != 1 for s in ir["scenes"]):
            _fail("CONTENT_SCENE_CHAPTER", "/scenes", "a scene cannot cross a chapter boundary")
    previous_end = 0
    for index, segment in enumerate(narration["segments"]):
        path = f"/narration/segments/{index}"
        if segment["start_ms"] < previous_end:
            _fail("NONMONOTONIC_NARRATION", f"{path}/start_ms", "segments overlap or are out of order")
        if segment["end_ms"] <= segment["start_ms"]:
            _fail("INVALID_NARRATION_TIME", f"{path}/end_ms", "segment end must be after start")
        if segment["end_ms"] > ir["video"]["duration_ms"]:
            _fail("NARRATION_OUT_OF_RANGE", f"{path}/end_ms", "segment extends beyond video duration")
        previous_end = segment["end_ms"]
    make_burned_subtitle_segments(narration["segments"])

    scene_ids = _unique(ir["scenes"], lambda x: x["id"], "/scenes", "scene id")
    all_beats = []
    all_beat_ids = set()
    scheduled_scenes = []
    previous_scene_end_ms = 0
    previous_scene_end_frame = 0
    for scene_index, scene in enumerate(ir["scenes"]):
        sp = f"/scenes/{scene_index}"
        end_ms = scene["start_ms"] + scene["duration_ms"]
        start_frame = frame_at(scene["start_ms"], ir["video"]["fps"])
        end_frame = frame_at(end_ms, ir["video"]["fps"])
        if scene["start_ms"] != previous_scene_end_ms:
            _fail("SCENE_GAP_OR_OVERLAP", f"{sp}/start_ms", "scenes must cover a contiguous millisecond timeline")
        if start_frame != previous_scene_end_frame:
            _fail("FRAME_GAP_OR_OVERLAP", f"{sp}/start_ms", "absolute scene frame boundaries are not contiguous")
        if end_frame <= start_frame:
            _fail("ZERO_FRAME_SCENE", f"{sp}/duration_ms", "scene has no frames after boundary conversion")
        if end_ms > ir["video"]["duration_ms"]:
            _fail("SCENE_OUT_OF_RANGE", f"{sp}/duration_ms", "scene extends beyond the video duration")
        _validate_component(scene["component"], scene["props"], sp)

        # Component invariants not expressed by the authored JSON Schemas.
        props = scene["props"]
        if scene["component"] == "bar_chart" and len(props["categories"]) != len(props["values"]):
            _fail("CHART_CARDINALITY_MISMATCH", f"{sp}/props/values", "values and categories must have equal length")
        if scene["component"] == "flow_diagram":
            node_ids = _unique(props["nodes"], lambda x: x["id"], f"{sp}/props/nodes", "node id")
            edge_ids = _unique(props["edges"], lambda x: x["id"], f"{sp}/props/edges", "edge id")
            _unique(props["paths"], lambda x: x["id"], f"{sp}/props/paths", "path id")
            for ei, edge in enumerate(props["edges"]):
                if edge["from"] not in node_ids or edge["to"] not in node_ids:
                    _fail("INVALID_EDGE_NODE", f"{sp}/props/edges/{ei}", "edge endpoints must reference declared nodes")
            for pi, path in enumerate(props["paths"]):
                if len(set(path["edge_ids"])) != len(path["edge_ids"]) or any(e not in edge_ids for e in path["edge_ids"]):
                    _fail("INVALID_PATH_EDGE", f"{sp}/props/paths/{pi}/edge_ids", "path must reference unique declared edges")
            if "layout" in props:
                layout = props["layout"]
                lp = f"{sp}/props/layout"
                if not props.get("title") or not props.get("takeaway"):
                    _fail("DIAGRAM_LAYOUT_CONTENT", lp, "laid out diagrams require title and takeaway")
                laid_nodes = _unique(layout["nodes"], lambda n: n["id"], f"{lp}/nodes", "layout node")
                laid_edges = _unique(layout["edges"], lambda e: e["id"], f"{lp}/edges", "layout edge")
                if laid_nodes != node_ids or laid_edges != edge_ids:
                    _fail("DIAGRAM_LAYOUT_COVERAGE", lp, "layout must cover all declared nodes and edges exactly")
                labels = {n["id"]: n["label"] for n in props["nodes"]}
                for ni, node in enumerate(layout["nodes"]):
                    if "".join(node["label_lines"]) != labels[node["id"]]:
                        _fail("DIAGRAM_LAYOUT_LABEL", f"{lp}/nodes/{ni}", "wrapped text must preserve the original label")
                    if node["x"]+node["width"] > layout["width"]+1e-6 or node["y"]+node["height"] > layout["height"]+1e-6:
                        _fail("DIAGRAM_LAYOUT_BOUNDS", f"{lp}/nodes/{ni}", "node exceeds the diagram stage")
                    for other in layout["nodes"][:ni]:
                        if (node["x"] < other["x"]+other["width"] and node["x"]+node["width"] > other["x"]
                                and node["y"] < other["y"]+other["height"] and node["y"]+node["height"] > other["y"]):
                            _fail("DIAGRAM_LAYOUT_OVERLAP", lp, "diagram nodes overlap")

        referenced = set(scene["narration_segment_ids"])
        if not referenced.issubset(segment_ids):
            _fail("MISSING_NARRATION_SEGMENT", f"{sp}/narration_segment_ids", "scene references a missing segment")
        segments = {s["id"]: s for s in narration["segments"]}
        if any(segments[sid]["start_ms"] < scene["start_ms"] or segments[sid]["end_ms"] > end_ms for sid in referenced):
            _fail("NARRATION_OUTSIDE_SCENE", f"{sp}/narration_segment_ids", "a referenced segment is outside its scene")

        scheduled_beats = []
        for beat_index, beat in enumerate(scene["beats"]):
            bp = f"{sp}/beats/{beat_index}"
            if beat["id"] in all_beat_ids:
                _fail("DUPLICATE_ID", f"{bp}/id", f"duplicate beat id: {beat['id']}")
            all_beat_ids.add(beat["id"])
            trigger = beat["trigger"]
            sid = trigger["segment_id"]
            if sid not in segment_ids:
                _fail("MISSING_TRIGGER_SEGMENT", f"{bp}/trigger/segment_id", sid)
            if sid not in referenced:
                _fail("TRIGGER_SEGMENT_NOT_IN_SCENE", f"{bp}/trigger/segment_id", sid)
            base = segments[sid]["start_ms"] if trigger["boundary"] == "start" else segments[sid]["end_ms"]
            absolute_ms = base + trigger["offset_ms"]
            if ((information or (scene["component"] == "flow_diagram" and "layout" in props))
                    and trigger["boundary"] == "start" and trigger["offset_ms"] >= 0
                    and absolute_ms > segments[sid]["end_ms"]):
                _fail("DIAGRAM_CUE_OUTSIDE_PHRASE", f"{bp}/trigger", "cue extends beyond its own measured phrase")
            if absolute_ms < 0 or absolute_ms > ir["video"]["duration_ms"]:
                _fail("BEAT_TIME_OUT_OF_RANGE", f"{bp}/trigger/offset_ms", "trigger resolves outside the video")
            beat_frame = frame_at(absolute_ms, ir["video"]["fps"])
            if beat_frame < start_frame or beat_frame >= end_frame:
                _fail("BEAT_OUTSIDE_SCENE", f"{bp}/trigger", "beat resolves outside its scene")
            _validate_targets(scene["component"], props, beat["action"], beat["target_id"], bp)
            item = {"id": beat["id"], "action": beat["action"], "target_id": beat["target_id"],
                    "frame": beat_frame, "absolute_ms": absolute_ms, "order": beat_index}
            scheduled_beats.append(item)
            all_beats.append(item)
        scheduled_scenes.append({"id": scene["id"], "component": scene["component"],
            "purpose": scene["purpose"], "visual_intent": scene["visual_intent"],
            "props": props, "narration_segment_ids": scene["narration_segment_ids"],
            "start_frame": start_frame, "end_frame": end_frame, "beats": scheduled_beats})
        previous_scene_end_ms, previous_scene_end_frame = end_ms, end_frame

    if previous_scene_end_ms != ir["video"]["duration_ms"]:
        _fail("SCENE_COVERAGE_INCOMPLETE", "/scenes", "scenes must cover the complete video duration")
    total_frames = frame_at(ir["video"]["duration_ms"], ir["video"]["fps"])
    if previous_scene_end_frame != total_frames:
        _fail("FRAME_COVERAGE_INCOMPLETE", "/scenes", "scenes must cover every output frame")
    all_beats.sort(key=lambda b: (b["frame"], b["order"]))
    return {"schema_version": "0.2", "video": ir["video"], "style": ir["style"],
            "narration": narration, "scenes": scheduled_scenes, "beats": all_beats,
            "total_frames": total_frames, **({"content_structure": structure} if structure else {}),
            **({"presentation": ir["presentation"]} if information else {}),
            **({"playback": ir["playback"]} if "playback" in ir else {})}


def make_burned_subtitle_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # The renderer scales longer Chinese phrases to 30px and wraps at the same
    # content width. Validate that every caption remains within two lines.
    content_width = MAX_SUBTITLE_WIDTH - 56  # 28px horizontal padding on each side
    for index, segment in enumerate(segments):
        text = segment["text"]
        font_size = SUBTITLE_FONT_SIZE if len(text) <= 32 else 30
        font = ImageFont.truetype(SUBTITLE_FONT_PATH, font_size)
        lines = 1
        line_width = 0.0
        for character in text:
            if character in "\r\n":
                lines += 1
                line_width = 0.0
                continue
            character_width = font.getlength(character)
            if character_width > content_width:
                lines += 1
                line_width = character_width
            elif line_width + character_width > content_width:
                lines += 1
                line_width = character_width
            else:
                line_width += character_width
        glyph_height = font.getbbox("潮")[3] - font.getbbox("潮")[1]
        if lines > 2 or lines * glyph_height > 86:
            _fail("SUBTITLE_TOO_WIDE", f"/narration/segments/{index}/text", "subtitle exceeds the 1680px safe width")
    return segments


def schema_action_catalog() -> dict[str, dict[str, list[str]]]:
    """Return copied action catalog for audit/reporting."""
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))["components"]

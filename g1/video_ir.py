"""Strict Video IR v0.2 validator and millisecond-to-frame compiler.

Model-facing data remains JSON. This module validates it before it can reach the
fixed allowlisted Remotion component switch. It never evaluates IR strings.
"""
from __future__ import annotations

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
        index = int(target.split(":", 1)[1])
        exists = index < len(props.get("lines", []))
    elif target.startswith("side:"):
        exists = target.split(":", 1)[1] in {"left", "right"} and target.split(":", 1)[1] in props
    elif target.startswith("event:"):
        exists = target.split(":", 1)[1] in {item["id"] for item in props.get("events", [])}
    elif target.startswith("bar:"):
        index = int(target.split(":", 1)[1])
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
    errors = sorted(Draft202012Validator(schema).iter_errors(ir), key=lambda e: tuple(str(x) for x in e.path))
    if errors:
        error = errors[0]
        _fail("SCHEMA_VALIDATION", _pointer(error.path), error.message)
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
            "total_frames": total_frames}


def make_burned_subtitle_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    font = ImageFont.truetype(SUBTITLE_FONT_PATH, SUBTITLE_FONT_SIZE)
    for index, segment in enumerate(segments):
        bbox = font.getbbox(segment["text"])
        if bbox[2] - bbox[0] > MAX_SUBTITLE_WIDTH or bbox[3] - bbox[1] > 74:
            _fail("SUBTITLE_TOO_WIDE", f"/narration/segments/{index}/text", "subtitle exceeds the 1680px safe width")
    return segments


def schema_action_catalog() -> dict[str, dict[str, list[str]]]:
    """Return copied action catalog for audit/reporting."""
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))["components"]

from __future__ import annotations

import copy
import hashlib
import json
import wave
from pathlib import Path

import pytest

from g1.video_ir import (
    ValidationFault,
    _validate_component,
    frame_at,
    schema_action_catalog,
    validate_video_ir,
)


FLOW_PROPS = {
    "nodes": [{"id": "sender", "label": "发送者"}, {"id": "channel", "label": "信道"}, {"id": "receiver", "label": "接收者"}],
    "edges": [{"id": "sender_to_channel", "from": "sender", "to": "channel"}, {"id": "channel_to_receiver", "from": "channel", "to": "receiver"}],
    "paths": [{"id": "main", "edge_ids": ["sender_to_channel", "channel_to_receiver"]}],
}


def make_root(tmp_path: Path) -> Path:
    root = tmp_path / "fixture"
    (root / "assets").mkdir(parents=True)
    with wave.open(str(root / "assets/audio.wav"), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\0\0" * 24000)
    return root


def base_ir(root: Path) -> dict:
    source = "传输示例。"
    return {
        "schema_version": "0.2",
        "video": {"title": "测试", "width": 1920, "height": 1080, "fps": 30, "duration_ms": 1000},
        "source": {"language": "zh-CN", "text_sha256": hashlib.sha256(source.encode()).hexdigest(), "original_text": source},
        "style": {"preset": "minimal_dark", "seed": 7},
        "assets": [{"id": "audio", "type": "audio", "path": "assets/audio.wav", "origin": "generated-test-fixture", "sha256": hashlib.sha256((root / "assets/audio.wav").read_bytes()).hexdigest()}],
        "narration": {"text": "传输示例。", "audio_asset_id": "audio", "segments": [{"id": "n1", "text": "传输示例。", "start_ms": 100, "end_ms": 900}], "alignment": {"method": "provider", "granularity": "phrase"}},
        "scenes": [{"id": "s1", "start_ms": 0, "duration_ms": 1000, "component": "flow_diagram", "props": copy.deepcopy(FLOW_PROPS), "narration_segment_ids": ["n1"], "purpose": "测试通信图", "visual_intent": {"viewer_understands": "信号沿路径传输", "before": "尚未出现路径", "after": "路径激活后可见"}, "beats": [{"id": "reveal_sender", "action": "reveal", "target_id": "node:sender", "trigger": {"segment_id": "n1", "boundary": "start", "offset_ms": 0}}]}],
    }


def fault_code(ir: dict, root: Path) -> str:
    with pytest.raises(ValidationFault) as exc:
        validate_video_ir(ir, root)
    return exc.value.code


def test_half_up_frame_conversion_and_absolute_boundaries():
    assert [frame_at(x) for x in [1001, 2002, 3003]] == [30, 60, 90]
    assert frame_at(150) == 5
    assert frame_at(50) == 2


def test_happy_path_and_compiled_timeline(tmp_path):
    root = make_root(tmp_path)
    ir = base_ir(root)
    result = validate_video_ir(ir, root)
    assert result["total_frames"] == 30
    assert result["scenes"][0]["start_frame"] == 0
    assert result["scenes"][0]["end_frame"] == 30
    assert result["beats"][0]["frame"] == 3


def test_all_six_component_schemas_accept_valid_props():
    props = {
        "title_card": {"title": "标题卡", "subtitle": "副标题"},
        "concept_reveal": {"title": "概念", "lines": ["第一行", "第二行"]},
        "flow_diagram": FLOW_PROPS,
        "comparison": {"left": {"label": "之前", "items": ["原信息"]}, "right": {"label": "之后", "items": ["受扰信息"]}, "highlights": ["内容发生变化"]},
        "timeline": {"events": [{"id": "one", "label": "开始"}, {"id": "two", "label": "结束"}]},
        "bar_chart": {"categories": ["A", "B"], "values": [3, 7], "unit": "次", "baseline_zero": True},
    }
    for component, value in props.items():
        _validate_component(component, value, "/scene")
    assert set(props) == set(schema_action_catalog())


@pytest.mark.parametrize("mutate,expected", [
    (lambda x: x.update(unknown=1), "SCHEMA_VALIDATION"),
    (lambda x: x["scenes"][0].update(component="unknown_component"), "SCHEMA_VALIDATION"),
    (lambda x: x["scenes"][0]["props"].update(extra="no"), "INVALID_COMPONENT_PROPS"),
    (lambda x: x["scenes"][0]["beats"][0].update(action="not_registered"), "SCHEMA_VALIDATION"),
    (lambda x: (x["scenes"][0].update(component="title_card", props={"title": "标题"}), x["scenes"][0]["beats"][0].update(action="activate_path", target_id="path:main")), "UNSUPPORTED_ACTION"),
    (lambda x: x["scenes"][0]["props"]["paths"][0].update(edge_ids=["missing"]), "INVALID_PATH_EDGE"),
    (lambda x: x["scenes"][0]["beats"][0].update(target_id="node:missing"), "INVALID_BEAT_TARGET"),
    (lambda x: x["source"].update(text_sha256="0" * 64), "SOURCE_HASH_MISMATCH"),
])
def test_rejects_invalid_schema_semantics_and_references(tmp_path, mutate, expected):
    root = make_root(tmp_path)
    ir = base_ir(root)
    mutate(ir)
    assert fault_code(ir, root) == expected


def test_rejects_trigger_segment_present_but_not_owned_by_scene(tmp_path):
    root = make_root(tmp_path)
    ir = base_ir(root)
    ir["narration"]["segments"][0]["end_ms"] = 500
    ir["narration"]["segments"].append({"id": "n2", "text": "另一段。", "start_ms": 600, "end_ms": 900})
    ir["scenes"][0]["beats"][0]["trigger"]["segment_id"] = "n2"
    assert fault_code(ir, root) == "TRIGGER_SEGMENT_NOT_IN_SCENE"


def test_rejects_beat_time_that_resolves_outside_own_scene(tmp_path):
    root = make_root(tmp_path)
    ir = base_ir(root)
    ir["narration"]["segments"] = [
        {"id": "n1", "text": "第一段。", "start_ms": 100, "end_ms": 400},
        {"id": "n2", "text": "第二段。", "start_ms": 600, "end_ms": 900},
    ]
    first = copy.deepcopy(ir["scenes"][0])
    second = {**copy.deepcopy(first), "id": "s2", "start_ms": 500, "duration_ms": 500, "narration_segment_ids": ["n2"]}
    second["beats"][0]["id"] = "scene_two_beat"
    second["beats"][0]["trigger"]["segment_id"] = "n2"
    first["narration_segment_ids"] = ["n1"]
    ir["scenes"] = [first, second]
    ir["scenes"][0]["duration_ms"] = 500
    ir["scenes"][0]["beats"][0]["trigger"]["boundary"] = "end"
    ir["scenes"][0]["beats"][0]["trigger"]["offset_ms"] = 500
    assert fault_code(ir, root) == "BEAT_OUTSIDE_SCENE"


def test_rejects_duplicate_scene_and_beat_ids(tmp_path):
    root = make_root(tmp_path)
    ir = base_ir(root)
    ir["scenes"][0]["beats"].append(copy.deepcopy(ir["scenes"][0]["beats"][0]))
    assert fault_code(ir, root) == "DUPLICATE_ID"
    ir = base_ir(root)
    second = copy.deepcopy(ir["scenes"][0])
    second["beats"][0]["id"] = "another_beat"
    ir["scenes"].append({**second, "start_ms": 1000, "duration_ms": 100})
    assert fault_code(ir, root) == "DUPLICATE_ID"


def test_rejects_duplicate_narration_segment_ids(tmp_path):
    root = make_root(tmp_path)
    ir = base_ir(root)
    duplicate = copy.deepcopy(ir["narration"]["segments"][0])
    duplicate.update(start_ms=920, end_ms=980)
    ir["narration"]["segments"].append(duplicate)
    assert fault_code(ir, root) == "DUPLICATE_ID"


def test_rejects_path_escape_symlink_and_hash_mismatch(tmp_path):
    root = make_root(tmp_path)
    ir = base_ir(root)
    ir["assets"][0]["path"] = "assets/../../outside.wav"
    assert fault_code(ir, root) == "SCHEMA_VALIDATION"

    outside = tmp_path / "outside.wav"
    outside.write_bytes((root / "assets/audio.wav").read_bytes())
    (root / "assets/link.wav").symlink_to(outside)
    ir = base_ir(root)
    ir["assets"][0]["path"] = "assets/link.wav"
    assert fault_code(ir, root) == "ASSET_SYMLINK"

    ir = base_ir(root)
    ir["assets"][0]["sha256"] = "0" * 64
    assert fault_code(ir, root) == "ASSET_HASH_MISMATCH"


def test_rejects_oversized_chinese_subtitle_before_render(tmp_path):
    root = make_root(tmp_path)
    ir = base_ir(root)
    ir["narration"]["segments"][0]["text"] = "通信" * 400
    assert fault_code(ir, root) == "SUBTITLE_TOO_WIDE"


def test_bar_chart_requires_matching_categories_and_values(tmp_path):
    root = make_root(tmp_path)
    ir = base_ir(root)
    ir["scenes"][0].update(component="bar_chart", props={"categories": ["甲", "乙"], "values": [1], "unit": "个", "baseline_zero": True})
    assert fault_code(ir, root) == "CHART_CARDINALITY_MISMATCH"

from __future__ import annotations

import base64
import json
import struct
import zlib
from io import BytesIO
from pathlib import Path
from urllib.error import URLError

import pytest

import g2.draw_things as draw_things


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def _png(width: int = 512, height: int = 288) -> bytes:
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    pixels = b"\x00" + b"\x18\x28\x48" * width
    data = zlib.compress(pixels * height)
    return (draw_things.PNG_SIGNATURE + _chunk(b"IHDR", header)
            + _chunk(b"IDAT", data) + _chunk(b"IEND", b""))


class _Response(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def _board():
    return {"scenes": [
        {"id": "opening", "purpose": "引出信息传递", "visual_intent": {"after": "一个信号开始移动"}},
        {"id": "middle", "purpose": "解释信道干扰", "visual_intent": {"after": "信号路径受到干扰"}},
        {"id": "ending", "purpose": "总结传递结果", "visual_intent": {"after": "接收端收到信息"}},
    ]}


def test_scene_background_generation_uses_loopback_and_writes_verified_manifest(tmp_path, monkeypatch):
    png = _png()
    encoded = base64.b64encode(png).decode("ascii")
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return _Response(json.dumps({"images": [encoded]}).encode("utf-8"))

    monkeypatch.setattr(draw_things, "urlopen", fake_urlopen)
    fixture_root, artifacts_dir = tmp_path / "fixture", tmp_path / "artifacts"
    result = draw_things.generate_scene_backgrounds(_board(), fixture_root, artifacts_dir,
                                                    endpoint="http://127.0.0.1:7860", timeout=2)

    assert result == {
        "opening": "assets/scene-backgrounds/scene-01.png",
        "middle": "assets/scene-backgrounds/scene-02.png",
        "ending": "assets/scene-backgrounds/scene-03.png",
    }
    assert len(requests) == 3
    for request, timeout in requests:
        body = json.loads(request.data)
        assert request.full_url == "http://127.0.0.1:7860/sdapi/v1/txt2img"
        assert body["width"] == 1280 and body["height"] == 720
        assert body["negative_prompt"] == ""
        assert timeout == 2
    for index, path in enumerate(result.values(), start=1):
        assert (fixture_root / path).read_bytes() == png
        assert path.endswith(f"scene-{index:02d}.png")
    manifest = json.loads((artifacts_dir / "scene-backgrounds.json").read_text(encoding="utf-8"))
    assert manifest["provider"] == "Draw Things local HTTP API"
    assert [image["scene_id"] for image in manifest["images"]] == ["opening", "middle", "ending"]


def test_draw_things_base_url_rejects_non_loopback_destinations():
    with pytest.raises(draw_things.DrawThingsFault, match="必须是本机 HTTP"):
        draw_things.base_url("http://example.com:7860")
    with pytest.raises(draw_things.DrawThingsFault):
        draw_things.base_url("https://127.0.0.1:7860")


def test_probe_reports_unavailable_without_treating_it_as_required(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise URLError("connection refused")

    monkeypatch.setattr(draw_things, "urlopen", unavailable)
    status = draw_things.probe_draw_things()
    assert status["ready"] is False
    assert status["endpoint"] == "http://127.0.0.1:7860"
    assert "开启 HTTP API 服务器" in status["error"]


def test_scene_image_output_must_be_png(tmp_path, monkeypatch):
    responses = iter([base64.b64encode(b"not a png").decode("ascii")] * 3)
    monkeypatch.setattr(draw_things, "urlopen", lambda *_args, **_kwargs: _Response(
        json.dumps({"images": [next(responses)]}).encode("utf-8")))
    with pytest.raises(draw_things.DrawThingsFault, match="不是有效的 PNG"):
        draw_things.generate_scene_backgrounds(_board(), tmp_path / "fixture", tmp_path / "artifacts")

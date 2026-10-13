"""Small local-only client for Draw Things' built-in HTTP API."""
from __future__ import annotations

import base64
import binascii
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import struct
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from PIL import Image


DEFAULT_BASE_URL = "http://127.0.0.1:7860"
BASE_URL_ENV = "DRAW_THINGS_API_BASE_URL"
API_TIMEOUT_SECONDS = 900
IMAGE_WIDTH = 1280
IMAGE_HEIGHT = 720
MAX_IMAGE_BYTES = 24 * 1024 * 1024
MAX_RESPONSE_BYTES = MAX_IMAGE_BYTES * 4 // 3 + 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class DrawThingsFault(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


def base_url(value: str | None = None) -> str:
    """Return a validated loopback URL; image prompts must never leave this Mac."""
    raw = (value or os.environ.get(BASE_URL_ENV) or DEFAULT_BASE_URL).strip().rstrip("/")
    parsed = urlparse(raw)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment):
        raise DrawThingsFault("DRAW_THINGS_URL_INVALID", "Draw Things 地址必须是本机 HTTP 服务，例如 http://127.0.0.1:7860")
    try:
        port = parsed.port or 80
    except ValueError as exc:
        raise DrawThingsFault("DRAW_THINGS_URL_INVALID", "Draw Things API 端口无效") from exc
    if not 1 <= port <= 65535:
        raise DrawThingsFault("DRAW_THINGS_URL_INVALID", "Draw Things API 端口无效")
    return raw


def _read_json(url: str, *, method: str = "GET", body: bytes | None = None,
               timeout: float = 3) -> dict:
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES)
    except HTTPError as exc:
        detail = exc.read(2048).decode("utf-8", errors="replace")
        try:
            parsed = json.loads(detail)
            detail = str(parsed.get("detail") or parsed.get("error") or parsed.get("message") or detail)
        except (ValueError, AttributeError):
            pass
        raise DrawThingsFault("DRAW_THINGS_REQUEST_FAILED", f"Draw Things 返回 HTTP {exc.code}: {detail[:240]}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise DrawThingsFault("DRAW_THINGS_UNAVAILABLE", "无法连接 Draw Things 本机 HTTP API；请在应用设置中开启 HTTP API 服务器") from exc
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise DrawThingsFault("DRAW_THINGS_INVALID_RESPONSE", "Draw Things API 返回了无效 JSON") from exc
    if not isinstance(value, dict):
        raise DrawThingsFault("DRAW_THINGS_INVALID_RESPONSE", "Draw Things API 返回格式不正确")
    return value


def probe_draw_things(value: str | None = None, *, timeout: float = 1.5) -> dict:
    """Check whether the optional local image-generation service is available."""
    try:
        endpoint = base_url(value)
    except DrawThingsFault as exc:
        return {"ready": False, "endpoint": None, "error": str(exc)}
    try:
        _read_json(endpoint + "/", timeout=timeout)
    except DrawThingsFault as exc:
        return {"ready": False, "endpoint": endpoint, "error": str(exc)}
    return {"ready": True, "endpoint": endpoint, "error": None}


def _prompt_for_scene(scene: dict) -> str:
    visual = scene.get("visual_intent", {})
    purpose = scene.get("purpose", "")
    after = visual.get("after", "") if isinstance(visual, dict) else ""
    purpose = re.sub(r"[\x00-\x1f\x7f]", " ", str(purpose))[:120].strip()
    after = re.sub(r"[\x00-\x1f\x7f]", " ", str(after))[:120].strip()
    subject = "；".join(value for value in (purpose, after) if value)
    return (
        "16:9 横幅中文知识动画背景图，精致的科学杂志编辑插画，克制而清晰，深海军蓝、青绿与少量暖橙，"
        "有轻微空间层次，主体靠画面边缘，中央留出干净低细节区域供精确矢量图解叠加，底部较暗以容纳字幕。"
        f"本场景意图：{subject}。只画抽象、辅助性的场景气氛，不要画文字、字母、数字、标签、图表、箭头、"
        "示意图、标志、水印、界面或可读符号。"
    )


def _validate_png(raw: bytes) -> tuple[int, int]:
    if len(raw) < 24 or len(raw) > MAX_IMAGE_BYTES or raw[:8] != PNG_SIGNATURE or raw[12:16] != b"IHDR":
        raise DrawThingsFault("DRAW_THINGS_IMAGE_INVALID", "Draw Things 输出不是有效的 PNG 背景图")
    width, height = struct.unpack(">II", raw[16:24])
    if not (512 <= width <= 4096 and 288 <= height <= 4096):
        raise DrawThingsFault("DRAW_THINGS_IMAGE_INVALID", f"Draw Things PNG 尺寸异常：{width}x{height}")
    try:
        with Image.open(BytesIO(raw)) as image:
            image.verify()
    except (OSError, ValueError) as exc:
        raise DrawThingsFault("DRAW_THINGS_IMAGE_INVALID", "Draw Things 输出 PNG 解码校验失败") from exc
    return width, height


def generate_scene_backgrounds(board: dict, fixture_root: Path, artifacts_dir: Path,
                               *, endpoint: str | None = None, timeout: float = API_TIMEOUT_SECONDS) -> dict:
    """Generate one local background per validated scene and return a renderer-only sidecar."""
    api_base = base_url(endpoint)
    scenes = board.get("scenes")
    if not isinstance(scenes, list) or len(scenes) != 3:
        raise DrawThingsFault("DRAW_THINGS_SCENES_INVALID", "场景背景生成需要 3 个已校验分镜")
    backgrounds_dir = fixture_root / "assets" / "scene-backgrounds"
    backgrounds_dir.mkdir(parents=True, exist_ok=True)
    renderer_paths: dict[str, str] = {}
    records: list[dict] = []

    for index, scene in enumerate(scenes, start=1):
        scene_id = scene.get("id") if isinstance(scene, dict) else None
        if not isinstance(scene_id, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", scene_id):
            raise DrawThingsFault("DRAW_THINGS_SCENES_INVALID", "分镜场景 ID 无效")
        prompt = _prompt_for_scene(scene)
        request_body = json.dumps({
            "prompt": prompt,
            "negative_prompt": "text, letters, numbers, labels, typography, logo, watermark, UI, chart, diagram, arrow, clutter",
            "width": IMAGE_WIDTH,
            "height": IMAGE_HEIGHT,
        }, ensure_ascii=False).encode("utf-8")
        response = _read_json(api_base + "/sdapi/v1/txt2img", method="POST", body=request_body, timeout=timeout)
        images = response.get("images")
        if not isinstance(images, list) or not images or not isinstance(images[0], str):
            raise DrawThingsFault("DRAW_THINGS_IMAGE_MISSING", f"场景 {scene_id} 没有收到图像")
        try:
            raw = base64.b64decode(images[0], validate=True)
        except (binascii.Error, ValueError) as exc:
            raise DrawThingsFault("DRAW_THINGS_IMAGE_INVALID", f"场景 {scene_id} 的图像数据无法解码") from exc
        width, height = _validate_png(raw)
        filename = f"scene-{index:02d}.png"
        image_path = backgrounds_dir / filename
        if image_path.exists():
            raise DrawThingsFault("DRAW_THINGS_OUTPUT_EXISTS", f"拒绝覆盖已有背景图：{filename}")
        image_path.write_bytes(raw)
        sha256 = hashlib.sha256(raw).hexdigest()
        renderer_paths[scene_id] = f"assets/scene-backgrounds/{filename}"
        records.append({"scene_id": scene_id, "path": renderer_paths[scene_id], "prompt": prompt,
                        "sha256": sha256, "bytes": len(raw), "width": width, "height": height})

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"provider": "Draw Things local HTTP API", "endpoint": api_base,
                "image_size_requested": [IMAGE_WIDTH, IMAGE_HEIGHT], "images": records}
    (artifacts_dir / "scene-backgrounds.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return renderer_paths

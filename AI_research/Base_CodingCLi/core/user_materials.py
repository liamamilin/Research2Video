"""Immutable user inputs with reusable, owner-scoped extracted content."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
import uuid

from core.fileio import atomic_write, file_lock

MAX_BYTES = 20 * 1024 * 1024
MAX_TEXT = 200000
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def folder(state_dir: str, key: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", key):
        raise ValueError("材料标识无效")
    return Path(state_dir) / "user_materials" / key


def _write(root: Path, record: dict) -> dict:
    atomic_write(str(root / "record.json"), json.dumps(record, ensure_ascii=False, indent=2))
    return record


def load(state_dir: str, key: str, owner: str) -> dict:
    root = folder(state_dir, key)
    try:
        record = json.loads((root / "record.json").read_text())
    except (OSError, ValueError) as exc:
        raise ValueError("材料不存在或不可读取") from exc
    if record["owner"] != owner:
        raise ValueError("材料不存在或无权访问")
    return record


def path(state_dir: str, record: dict, *, image: bool = False) -> Path:
    root = folder(state_dir, record["id"])
    target = root / ("image.png" if image else record["original"])
    if target.is_symlink() or not target.is_file():
        raise ValueError("原始材料不可读取")
    expected = record["image_sha256"] if image else record["sha256"]
    if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
        raise ValueError("原始材料已变化")
    return target


def summary(record: dict) -> dict:
    return {key: record.get(key) for key in ("id", "name", "kind", "status", "url", "chars", "error")} | {
        "file_url": f"/api/assistant/materials/{record['id']}/file"}


def save(state_dir: str, owner: str, *, name: str = "", text: str = "", url: str = "",
         raw: bytes | None = None, focus: dict | None = None) -> dict:
    if raw is not None and len(raw) > MAX_BYTES:
        raise ValueError("单个材料最多 20 MB")
    if len(text) > MAX_TEXT:
        raise ValueError("单份正文最多 200000 字符")
    key = uuid.uuid4().hex
    root = folder(state_dir, key)
    record = {"id": key, "owner": owner, "name": Path(name).name[:180] or "提供的正文",
              "created_at": time.time(), "kind": "text", "status": "ready", "url": url,
              "text": text, "chars": len(text), "error": ""}
    suffix = Path(name).suffix.lower()
    if url:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            raise ValueError("材料链接须为不带账号密码的 HTTP 或 HTTPS 网址")
        record.update(kind="url", name=name or parts.hostname, status="pending")
        raw, suffix = url.encode(), ".txt"
    elif raw is not None:
        if suffix in IMAGE_SUFFIXES:
            from PIL import Image, ImageOps
            try:
                with Image.open(io.BytesIO(raw)) as im:
                    if im.width * im.height > 40_000_000:
                        raise ValueError("图片尺寸过大")
                    out = io.BytesIO()
                    normalized = ImageOps.exif_transpose(im).convert('RGB')
                    normalized.thumbnail((2560, 2560))
                    if min(normalized.size) < 128:
                        frame = Image.new('RGB', (max(128, normalized.width), max(128, normalized.height)), 'white')
                        frame.paste(normalized, ((frame.width - normalized.width) // 2, (frame.height - normalized.height) // 2))
                        normalized = frame
                    normalized.save(out, format="PNG")
                    png = out.getvalue()
            except (OSError, Image.DecompressionBombError) as exc:
                raise ValueError("图片格式不可读取") from exc
            record.update(kind="image", status="ready" if text.strip() else "pending",
                          image_sha256=hashlib.sha256(png).hexdigest())
            if focus:
                record['focus'] = {**focus, 'width': normalized.width, 'height': normalized.height}
        else:
            from core.video.articles import extract
            text, warnings = extract(name, raw)
            record.update(kind="document", text=text, chars=len(text), warnings=warnings)
    else:
        if not text.strip():
            raise ValueError("请提供正文、链接或文件")
        raw, suffix = text.encode(), ".txt"
    root.mkdir(parents=True)
    record.update(original="original" + suffix, sha256=hashlib.sha256(raw).hexdigest())
    (root / record["original"]).write_bytes(raw)
    if record["kind"] == "image":
        (root / "image.png").write_bytes(png)
    return _write(root, record)


def prepare(state_dir: str, key: str, owner: str, *, read_url, describe_image) -> dict:
    root = folder(state_dir, key)
    with file_lock(str(root / "prepare"), timeout=180):
        record = load(state_dir, key, owner)
        path(state_dir, record)
        if record["status"] == "ready":
            return record
        try:
            if record["kind"] == "url":
                title, text = read_url(record["url"])
                record["name"] = title or record["name"]
            else:
                text = describe_image(path(state_dir, record, image=True).read_bytes())
            if not text.strip():
                raise ValueError("材料未取得可读取内容")
            # Keep the complete extracted input; never silently truncate it.
            if len(text) > MAX_TEXT:
                raise ValueError("提取的正文超过 200000 字符，请拆分材料")
            record.update(text=text, chars=len(text), status="ready", error="", prepared_at=time.time())
        except Exception as exc:
            _write(root, {**record, "status": "error", "error": str(exc)[:400]})
            raise
        return _write(root, record)

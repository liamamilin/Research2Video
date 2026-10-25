"""Local immutable image imports, copied and verified for each production job."""
from __future__ import annotations

import hashlib
from io import BytesIO
import json
from pathlib import Path
import warnings
from uuid import UUID, uuid4

from PIL import Image, ImageOps

MAX_BYTES = 20 * 1024 * 1024
MAX_IMAGES = 12


class ImageImportError(ValueError):
    pass


def image_path(data_dir: Path, image_id: str) -> Path:
    try:
        if str(UUID(image_id)) != image_id:
            raise ValueError()
    except (ValueError, TypeError, AttributeError) as exc:
        raise ImageImportError("图片编号无效") from exc
    root = data_dir / "creative-images"
    path = root / image_id / "image.png"
    if root.is_symlink() or path.parent.is_symlink() or path.is_symlink():
        raise ImageImportError("图片路径不安全")
    return path


def load_image(data_dir: Path, image_id: str) -> tuple[dict, bytes]:
    path = image_path(data_dir, image_id)
    meta = path.with_name("image.json")
    try:
        if meta.is_symlink():
            raise ImageImportError("图片记录不安全")
        record = json.loads(meta.read_text(encoding="utf-8"))
        raw = path.read_bytes()
        if record["image_id"] != image_id or hashlib.sha256(raw).hexdigest() != record["sha256"]:
            raise ImageImportError("图片已改变，请重新上传")
        return record, raw
    except (OSError, KeyError, ValueError) as exc:
        raise ImageImportError("图片不可用或校验失败，请重新上传") from exc


def import_image(data_dir: Path, raw: bytes, filename: str) -> dict:
    if not raw or len(raw) > MAX_BYTES:
        raise ImageImportError("每张图片最大 20 MB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as source:
                if source.format not in {"PNG", "JPEG", "WEBP"} or getattr(source, "n_frames", 1) != 1:
                    raise ImageImportError("请上传静态 PNG、JPEG 或 WebP 图片")
                if min(source.size) < 128 or max(source.size) > 8192 or source.width * source.height > 24_000_000:
                    raise ImageImportError("图片边长需为 128–8192 像素，总像素不超过 2400 万")
                image = ImageOps.exif_transpose(source).convert("RGB")
                image.thumbnail((2560, 2560))
                output = BytesIO()
                image.save(output, format="PNG")
                normalized = output.getvalue()
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageImportError(str(exc) if isinstance(exc, ImageImportError) else "图片无法解码或尺寸过大") from exc
    image_id = str(uuid4())
    path = image_path(data_dir, image_id)
    path.parent.mkdir(parents=True, exist_ok=False)
    path.write_bytes(normalized)
    record = {"image_id": image_id, "filename": Path(filename).name[:120], "width": image.width, "height": image.height,
              "sha256": hashlib.sha256(normalized).hexdigest(), "bytes": len(normalized)}
    path.with_name("image.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**record, "preview_url": f"/api/creative-images/{image_id}"}


def freeze_images(data_dir: Path, input_dir: Path, request: dict) -> dict:
    ids = request.get("slideshow_images") or []
    if not ids:
        return request
    if request.get("video_form") not in {"image_slideshow", "object_showcase", "dynamic_collage"} or len(ids) > MAX_IMAGES or len(set(ids)) != len(ids):
        raise ImageImportError("图片素材只用于图片轮播、对象展示或动态拼贴，最多 12 张且不能重复")
    if request.get("parent_job_id") and request.get("slideshow_images_resolved"):
        parent_id = str(UUID(request["parent_job_id"]))
        parent = data_dir / "jobs" / parent_id
        source_dir = parent / "input"
        try:
            if parent.is_symlink() or source_dir.is_symlink() or (source_dir / "input.json").is_symlink() or (source_dir / "images").is_symlink():
                raise ImageImportError("原版本图片路径不安全")
            saved = json.loads((source_dir / "input.json").read_text(encoding="utf-8"))
            records = request["slideshow_images_resolved"]
            if saved.get("slideshow_images_resolved") != records or [r["image_id"] for r in records] != ids:
                raise ImageImportError("原版本图片记录不一致")
            loaded = []
            for record in records:
                image_id = str(UUID(record["image_id"]))
                path = source_dir / "images" / f"{image_id}.png"
                if path.is_symlink():
                    raise ImageImportError("原版本图片路径不安全")
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != record["sha256"]:
                    raise ImageImportError("原版本图片校验失败")
                loaded.append((record, raw))
        except (OSError, KeyError, ValueError) as exc:
            raise ImageImportError("原版本图片不可用或校验失败") from exc
    else:
        loaded = [load_image(data_dir, image_id) for image_id in ids]
    records = [record for record, _ in loaded]
    if request.get("slideshow_images_resolved") not in (None, records):
        raise ImageImportError("原版本的轮播图片已改变，请重新上传")
    folder = input_dir / "images"
    folder.mkdir()
    for record, raw in loaded:
        (folder / f"{record['image_id']}.png").write_bytes(raw)
    return {**request, "slideshow_images_resolved": records}

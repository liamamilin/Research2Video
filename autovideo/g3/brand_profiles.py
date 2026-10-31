"""Local brand registry, uploads, defaults and per-job immutable snapshots."""
from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
from uuid import UUID, uuid4
import wave

from g2.branding import BrandError, check_record, digest, frozen_input, identity, profile, selection

MAX_BYTES = 4 * 1024 * 1024


def root(data_dir: Path) -> Path:
    path = data_dir / "brands"
    if path.is_symlink():
        raise BrandError("品牌目录不安全")
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe(path: Path) -> Path:
    if path.is_symlink():
        raise BrandError("品牌路径不安全")
    return path


def write(path: Path, value: dict) -> None:
    safe(path)
    tmp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load(data_dir: Path, key: str) -> dict:
    folder = safe(root(data_dir) / identity(key))
    try:
        return profile(json.loads(safe(folder / "profile.json").read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise BrandError("品牌配置不存在或无法读取") from exc


def save(data_dir: Path, key: str, body: dict) -> dict:
    folder = safe(root(data_dir) / identity(key))
    folder.mkdir(exist_ok=True)
    # Assets are changed only by upload; editing copy cannot point to arbitrary files.
    assets = load(data_dir, key)["assets"] if (folder / "profile.json").exists() else {}
    result = profile({**body, "id": key, "assets": assets})
    write(folder / "profile.json", result)
    return result


def active(data_dir: Path) -> dict:
    path = safe(root(data_dir) / "active.json")
    if not path.exists():
        return {"enabled": False}
    try:
        return selection(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise BrandError("默认品牌配置无法读取") from exc


def activate(data_dir: Path, body: dict) -> dict:
    value = selection(body)
    if value is None:
        raise BrandError("请选择品牌或关闭品牌")
    if value["enabled"]:
        load(data_dir, value["profile"])
    write(root(data_dir) / "active.json", value)
    return value


def listing(data_dir: Path) -> dict:
    profiles = []
    for path in sorted(root(data_dir).glob("*/profile.json")):
        profiles.append(load(data_dir, path.parent.name))
    return {"active": active(data_dir), "profiles": profiles}


def asset_path(data_dir: Path, key: str, kind: str) -> Path:
    config = load(data_dir, key)
    if kind not in config["assets"]:
        raise BrandError("尚未上传该品牌素材")
    asset = config["assets"][kind]
    path = safe(root(data_dir) / key / asset["file"])
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != asset["sha256"]:
        raise BrandError("品牌素材缺失或已改变")
    return path


def upload(data_dir: Path, key: str, kind: str, encoded: str) -> dict:
    from PIL import Image
    if kind not in {"logo", "sonic"} or not isinstance(encoded, str) or len(encoded) > MAX_BYTES * 1.4:
        raise BrandError("请选择 logo PNG 或 sonic WAV，每份最大 4 MB")
    try:
        raw = base64.b64decode(encoded, validate=True)
        if not raw or len(raw) > MAX_BYTES:
            raise BrandError("品牌素材最大 4 MB")
        if kind == "logo":
            with Image.open(io.BytesIO(raw)) as image:
                if image.format != "PNG" or max(image.size) > 2048 or min(image.size) < 16:
                    raise BrandError("Logo 须为 16–2048 像素的 PNG")
                image.load()
                normalized = io.BytesIO()
                image.convert("RGBA").save(normalized, format="PNG")
                raw = normalized.getvalue()
        else:
            with wave.open(io.BytesIO(raw)) as audio:
                if (audio.getcomptype() != "NONE" or audio.getsampwidth() != 2
                        or audio.getnchannels() not in {1, 2} or audio.getframerate() not in {44100, 48000}
                        or not 1 <= audio.getnframes() / audio.getframerate() <= 6):
                    raise BrandError("声标须为 1–6 秒、44.1/48kHz、16bit PCM WAV")
                frames = audio.readframes(audio.getnframes())
                if len(frames) != audio.getnframes() * audio.getnchannels() * 2:
                    raise BrandError("声标文件不完整")
    except (OSError, ValueError, wave.Error, binascii.Error, Image.DecompressionBombError) as exc:
        raise BrandError("品牌素材格式无效：" + str(exc)) from exc
    config = load(data_dir, key)
    sha = hashlib.sha256(raw).hexdigest()
    asset = {"file": sha + (".png" if kind == "logo" else ".wav"), "sha256": sha}
    path = safe(root(data_dir) / key / asset["file"])
    if not path.exists():
        with path.open("xb") as stream:
            stream.write(raw)
    config["assets"][kind] = asset
    write(root(data_dir) / key / "profile.json", config)
    return config


def freeze(data_dir: Path, input_dir: Path, request: dict) -> dict:
    result = copy.deepcopy(request)
    prior = request.get("brand_resolved")
    # Revisions of historical unbranded videos remain unbranded.
    choice = selection(request.get("brand"))
    if choice is None:
        choice = {"enabled": False} if request.get("parent_job_id") else active(data_dir)
    if not choice["enabled"]:
        result.pop("brand_resolved", None)
        if "brand" in result:
            result["brand"] = choice
        return result
    if prior is not None:
        prior = check_record(prior)
    if request.get("parent_job_id") and prior and choice == prior["selection"]:
        parent = data_dir / "jobs" / str(UUID(request["parent_job_id"])) / "input"
        if parent.parent.is_symlink() or parent.is_symlink():
            raise BrandError("父版本品牌目录不安全")
        frozen_input(parent, {"brand": choice, "brand_resolved": prior})
        config = prior["profile"]
        source_dir = parent / "brand"
    else:
        config = load(data_dir, choice["profile"])
        source_dir = root(data_dir) / choice["profile"]
    target = input_dir / "brand"
    target.mkdir()
    for kind, asset in config["assets"].items():
        source = safe(source_dir / asset["file"])
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != asset["sha256"]:
            raise BrandError("品牌素材校验失败")
        shutil.copyfile(source, target / asset["file"])
    record = {"schema_version": 1, "selection": choice, "profile": config}
    record["sha256"] = digest(record)
    result.update(brand=choice, brand_resolved=record)
    # Own the corner mark and tail, retaining source cards and column labels.
    if not prior and not request.get("production_plan"):
        packaging = copy.deepcopy(result.get("packaging") or {})
        if result.get("package") is not None and not result.get("packaging"):
            legacy = result["package"]
            packaging = {"accent": legacy["accent"], "source_card": {"enabled": False}}
            if legacy.get("badge"):
                packaging["label"] = legacy["badge"]
        for slot in ("intro", "outro", "badge", "sound"):
            packaging[slot] = {"enabled": False}
        result["packaging"] = packaging
        result.pop("packaging_resolved", None)
        result.pop("package", None)
    return result

"""Acquire static public-page pictures and preserve version-specific editorial choices.

Screenshots are evidence of what a page displayed at capture time, not verification
of its claims. Browser automation never decides which paragraph a picture supports.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import threading
import time
from datetime import datetime, timezone
from contextlib import contextmanager
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from core.fileio import atomic_write, file_lock
from core.video import store

MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 24_000_000
CHAPTER_TITLE_MAX = 32
_captures = threading.BoundedSemaphore(2)
_FAKE_IP = ipaddress.ip_network("198.18.0.0/15")


class ScreenshotError(ValueError):
    pass


@lru_cache(maxsize=256)
def _proxy_public_addresses(host: str, minute: int) -> tuple[str, ...]:
    """A fake-IP proxy masks the real DNS answer. Check it independently before allowing the hostname."""
    import httpx
    addresses = []
    try:
        with httpx.Client(timeout=5, follow_redirects=False) as client:
            for kind in ("A", "AAAA"):
                response = client.get("https://cloudflare-dns.com/dns-query", params={"name": host, "type": kind},
                                      headers={"Accept": "application/dns-json"})
                response.raise_for_status()
                result = response.json()
                if result.get("Status") != 0:
                    raise ValueError()
                addresses.extend(str(item["data"]) for item in result.get("Answer", []) if item.get("type") in {1, 28})
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ValueError()
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise ScreenshotError("当前代理隐藏了真实 DNS 地址，且公开 DNS 核验未通过，请检查网络后重试") from exc
    return tuple(addresses)


def public_url(url: str) -> str:
    """Validate page and subresource destinations, including redirect destinations."""
    try:
        parsed = urlsplit(url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.port not in {None, 80, 443} or len(url) > 2048
                or any(ord(c) <= 32 or ord(c) == 127 for c in url)):
            raise ValueError()
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80),
                                       type=socket.SOCK_STREAM)
        ips = [ipaddress.ip_address(item[4][0]) for item in addresses]
        if not ips:
            raise ValueError()
        if all(ip.version == 4 and ip in _FAKE_IP for ip in ips):
            # This exception is for proxy-resolved DNS hostnames only. Literal
            # benchmark addresses and local hostnames remain forbidden.
            if not any(c.isalpha() for c in parsed.hostname) or parsed.hostname.endswith((".local", ".localhost", ".internal", ".home.arpa")):
                raise ValueError()
            _proxy_public_addresses(parsed.hostname, int(time.time() // 60))
        elif any(not ip.is_global for ip in ips):
            raise ValueError()
    except (OSError, ValueError) as exc:
        raise ScreenshotError("截图仅支持可解析的公开 HTTP/HTTPS 页面，不能访问本机或内网地址") from exc
    return url


def capture(url: str, *, mode: str = "viewport", selector: str = "", clip: dict | None = None) -> tuple[bytes, dict]:
    """Use a fresh anonymous Chromium context; never reuse publishing login state."""
    public_url(url)
    if mode not in {"viewport", "full_page", "element", "crop"}:
        raise ScreenshotError("请选择整屏、整页、指定元素或矩形裁切")
    if mode == "element" and not selector.strip():
        raise ScreenshotError("指定元素截图需要 CSS 选择器")
    if mode == "crop" and (not clip or min(clip.get("x", -1), clip.get("y", -1)) < 0
                           or min(clip.get("width", 0), clip.get("height", 0)) < 128
                           or clip["x"] + clip["width"] > 1440 or clip["y"] + clip["height"] > 900):
        raise ScreenshotError("裁切区域须在 1440×900 首屏内，宽高至少 128 像素")
    with page_for_capture(url) as page:
        return capture_page(page, url, mode=mode, selector=selector, clip=clip)


@contextmanager
def page_for_capture(url: str, *, timeout_seconds: float = 60):
    """One guarded anonymous page, shared by DOM inspection and the final screenshot."""
    public_url(url)
    deadline = time.monotonic() + max(1, timeout_seconds)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ScreenshotError("截图组件未安装：请安装 requirements.txt 后运行 python -m playwright install chromium") from exc
    if not _captures.acquire(blocking=False):
        raise ScreenshotError("已有两项截图正在执行，请稍后重试")
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--disable-quic"],
                                         **({} if Path(pw.chromium.executable_path).is_file() else {"channel": "chrome"}))
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1,
                                              locale="zh-CN", timezone_id="Asia/Shanghai", service_workers="block",
                                              accept_downloads=False)
                checked = set()

                def guard(route):
                    try:
                        if time.monotonic() >= deadline:
                            raise ScreenshotError("网页获取已超时")
                        target = route.request.url
                        if target not in checked:
                            public_url(target)
                            checked.add(target)
                        route.continue_()
                    except ScreenshotError:
                        route.abort("blockedbyclient")

                context.route("**/*", guard)
                context.route_web_socket("**/*", lambda ws: ws.close())
                page = context.new_page()
                page.set_default_timeout(min(15_000, max(1000, int(timeout_seconds * 1000))))
                page.on("dialog", lambda dialog: dialog.dismiss())
                response = page.goto(url, wait_until="domcontentloaded", timeout=min(30_000, max(1000, int(timeout_seconds * 1000))))
                if response is None or response.status >= 400:
                    raise ScreenshotError(f"页面未成功打开（HTTP {response.status if response else '无响应'}），请检查来源网址")
                public_url(page.url)
                # Read-only DOM inspection. User input is never evaluated as code.
                page.wait_for_function("document.fonts.status === 'loaded'", timeout=min(10_000, max(1000, int((deadline-time.monotonic())*1000))))
                if time.monotonic() >= deadline:
                    raise ScreenshotError("网页获取已达到时间预算")
                yield page
            finally:
                browser.close()
    except ScreenshotError:
        raise
    except Exception as exc:
        # Browser errors can include page content, URLs and local executable paths.
        raise ScreenshotError("网页截图失败，请检查页面是否可公开访问、元素是否可见，或运行 python -m playwright install chromium 安装浏览器") from exc
    finally:
        _captures.release()


def capture_page(page, url: str, *, mode: str = "viewport", selector: str = "", clip: dict | None = None) -> tuple[bytes, dict]:
    """Capture an already inspected page; caller input never becomes executable code."""
    options = {"type": "png", "animations": "disabled", "caret": "hide", "timeout": 15_000}
    if mode == "element":
        target = page.locator(selector)
        if target.count() != 1:
            raise ScreenshotError("选择器必须准确匹配一个元素，请调整后重试")
        target.scroll_into_view_if_needed()
        box = target.bounding_box()
        if not box or max(box["width"], box["height"]) > 8192 or box["width"] * box["height"] > MAX_PIXELS:
            raise ScreenshotError("元素不可见或过大，请缩小截图范围")
        raw = target.screenshot(**options)
    else:
        if mode == "full_page":
            size = page.evaluate("({width:document.documentElement.scrollWidth,height:document.documentElement.scrollHeight})")
            if max(size.values()) > 8192 or size["width"] * size["height"] > MAX_PIXELS:
                raise ScreenshotError("整页过长，请改用指定元素或矩形裁切，保证文字可读")
        raw = page.screenshot(**options, full_page=mode == "full_page", **({"clip": clip} if mode == "crop" else {}))
    return raw, {"url": url, "final_url": page.url, "title": page.title()[:300], "mode": mode,
                 "selector": selector if mode == "element" else "", "clip": clip if mode == "crop" else None,
                 "viewport": {"width": 1440, "height": 900},
                 "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def _root() -> Path:
    return Path(store.db_path()).parent / "video_screenshots"


def _folder(asset_id: str) -> Path:
    try:
        if str(UUID(asset_id)) != asset_id:
            raise ValueError()
    except (ValueError, TypeError, AttributeError) as exc:
        raise ScreenshotError("截图编号无效") from exc
    folder = _root() / asset_id
    if _root().is_symlink() or folder.is_symlink():
        raise ScreenshotError("截图路径无效")
    return folder


def save(script_id: str, version: int, raw: bytes, metadata: dict, *, created_by: str) -> dict:
    from PIL import Image
    if not raw or len(raw) > MAX_BYTES:
        raise ScreenshotError("截图最大 20 MB，请缩小截图范围")
    with Image.open(BytesIO(raw)) as image:
        if image.format != "PNG" or min(image.size) < 128 or max(image.size) > 8192 or image.width * image.height > MAX_PIXELS:
            raise ScreenshotError("截图宽高须为 128–8192 像素，总像素不能超过 2400 万")
        width, height = image.size
    asset_id = str(uuid4())
    folder = _folder(asset_id)
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "image.png").write_bytes(raw)
    record = {**metadata, "id": asset_id, "script_id": script_id, "version": version,
              "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "width": width, "height": height,
              "image_id": None, "engine_url": "", "created_by": created_by,
              "preview_url": f"/api/video/scripts/{script_id}/screenshots/{asset_id}/image"}
    atomic_write(str(folder / "record.json"), json.dumps(record, ensure_ascii=False))
    return record


def load(script_id: str, asset_id: str) -> tuple[dict, Path]:
    folder = _folder(asset_id)
    try:
        if (folder / "record.json").is_symlink() or (folder / "image.png").is_symlink():
            raise ScreenshotError("截图路径无效")
        record = json.loads((folder / "record.json").read_text(encoding="utf-8"))
        if record["id"] != asset_id or record["script_id"] != script_id:
            raise ScreenshotError("截图不属于这篇文稿")
        raw = (folder / "image.png").read_bytes()
        if hashlib.sha256(raw).hexdigest() != record["sha256"]:
            raise ScreenshotError("截图完整性校验失败，请重新获取")
        return record, folder / "image.png"
    except (OSError, KeyError, ValueError) as exc:
        raise ScreenshotError("截图不可用或已改变，请重新获取") from exc


def register(script_id: str, asset_id: str, client, *, force: bool = False, timeout: float | None = None) -> dict:
    folder = _folder(asset_id)
    with file_lock(str(folder / "record.json")):
        record, path = load(script_id, asset_id)
        if not force and record.get("image_id") and record.get("engine_url") == client.base_url:
            return record
        result = client.upload_creative_image(path.read_bytes(), f"source-{asset_id}.png", **({"timeout": timeout} if timeout is not None else {}))
        record.update(image_id=result["image_id"], engine_url=client.base_url,
                      registered_sha256=result.get("sha256", ""))
        if result.get("width") and result.get("height"):
            record.update(registered_width=result["width"], registered_height=result["height"])
        atomic_write(str(folder / "record.json"), json.dumps(record, ensure_ascii=False))
        return record


def assets(script_id: str) -> list[dict]:
    out = []
    for path in _root().glob("*/record.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("script_id") == script_id:
                record, _ = load(script_id, record["id"])
                out.append(record)
        except (OSError, ValueError, KeyError):
            continue
    return sorted(out, key=lambda r: r["captured_at"], reverse=True)


def chapters(version: dict) -> list[dict]:
    return [{"title": " ".join(s["title"].split())[:CHAPTER_TITLE_MAX] or "未命名",
             "paragraphs": [p["text"].strip() for p in s["paragraphs"] if p["text"].strip()]}
            for s in version["sections"] if any(p["text"].strip() for p in s["paragraphs"])]


def chapter_hash(version: dict) -> str:
    return hashlib.sha256(json.dumps(chapters(version), ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _selection_path(script_id: str, version: int) -> Path:
    # Script UUIDs may be stored as hex; normalize only the filesystem component.
    try:
        sid = UUID(script_id).hex
    except ValueError as exc:
        raise ScreenshotError("文稿编号无效") from exc
    return _root() / "selections" / sid / f"v{int(version)}.json"


def selection(script_id: str, version: dict) -> dict:
    path = _selection_path(script_id, version["version"])
    if not path.exists():
        return {"form": "object_showcase", "materials": []}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if value["chapter_hash"] != chapter_hash(version):
            raise ScreenshotError("文稿已改变，请重新确认段落配图")
        return value
    except (OSError, KeyError, ValueError) as exc:
        raise ScreenshotError("段落配图记录与文稿不一致，请重新保存配图") from exc


def has_selection(script_id: str, version: int) -> bool:
    """An explicitly empty manual selection is an opt-out too."""
    return _selection_path(script_id, version).is_file()


def select(script_id: str, version: dict, form: str, materials: list[dict], engine_url: str) -> dict:
    if form not in {"object_showcase", "dynamic_collage"} or len(materials) > 12:
        raise ScreenshotError("请选择对象展示或动态拼贴，最多使用 12 张图片")
    if len({row["screenshot_id"] for row in materials}) != len(materials):
        raise ScreenshotError("同一截图请在一项中选择多个段落，不要重复添加")
    chapter_list = chapters(version)
    frozen = []
    for row in materials:
        record, _ = load(script_id, row["screenshot_id"])
        if not record.get("image_id") or record.get("engine_url") != engine_url:
            raise ScreenshotError("截图尚未登记到当前视频引擎，请先登记图片")
        placements = row.get("placements") or []
        keys = [(p["chapter_index"], p["paragraph_index"]) for p in placements]
        if not keys or len(keys) != len(set(keys)) or any(
            min(c, p) < 0 or c >= len(chapter_list) or p >= len(chapter_list[c]["paragraphs"]) for c, p in keys
        ):
            raise ScreenshotError("每张图片至少选择一个有效且不重复的讲稿段落")
        caption = row.get("caption", "").strip()
        if len(caption) > 80 or any(ord(c) < 32 or ord(c) == 127 for c in caption):
            raise ScreenshotError("图片说明最多 80 字，须为一行文字")
        if row.get("role") not in {"reference", "overall", "detail", "comparison"}:
            raise ScreenshotError("图片用途无效")
        from core.video.visual_focus import for_asset
        focus = for_asset(record)
        frozen.append({**row, "caption": caption, "image_id": record["image_id"], **({"focus": focus} if focus else {}),
                       "source": {k: record[k] for k in ("url", "final_url", "title", "captured_at", "sha256")}})
    value = {"version": version["version"], "chapter_hash": chapter_hash(version), "origin": "manual", "form": form,
             "engine_url": engine_url, "materials": frozen}
    path = _selection_path(script_id, version["version"])
    with file_lock(str(path)):
        atomic_write(str(path), json.dumps(value, ensure_ascii=False))
    return value


def render_materials(script_id: str, version: dict, engine_url: str) -> dict:
    chosen = selection(script_id, version)
    if not chosen["materials"]:
        return {}
    if chosen["engine_url"] != engine_url:
        raise ScreenshotError("视频引擎地址已改变，请重新登记截图并保存配图")
    for row in chosen["materials"]:
        load(script_id, row["screenshot_id"])
    return {"video_form": chosen["form"], "production_mode": "standard", "use_draw_things": False,
            "image_materials": [{k: row[k] for k in ("image_id", "caption", "role", "placements", "source", "focus") if k in row}
                                for row in chosen["materials"]]}

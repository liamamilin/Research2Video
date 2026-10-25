"""Local work identity; never follows arbitrary user supplied URLs."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit


def identify(platform: str, url: str, *, strict: bool = False) -> tuple[str, str]:
    """Return a canonical work ID and URL, validating hosts for manual records."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    allowed = {"bilibili": ("bilibili.com", "b23.tv"), "douyin": ("douyin.com", "iesdouyin.com"), "kuaishou": ("kuaishou.com",), "xiaohongshu": ("xiaohongshu.com",), "weixin_channels": ("weixin.qq.com",)}
    if platform not in allowed or parts.scheme not in ("http", "https") or parts.username is not None or parts.password is not None or not any(
        host == domain or host.endswith("." + domain) for domain in allowed.get(platform, ())
    ):
        if strict:
            raise ValueError("视频链接与所选平台不匹配；请填写所选平台的完整作品链接。")
        return "", url.strip()
    if platform == "bilibili":
        match = re.fullmatch(r"/video/(BV[0-9A-Za-z]{10}|av[0-9]+)/*", parts.path)
        if match:
            return match[1], f"https://www.bilibili.com/video/{match[1]}"
        # Preserve old b23 links without guessing the work or making a network request.
        if host == "b23.tv" and parts.path.strip("/"):
            return "", f"https://b23.tv/{parts.path.strip('/')}"
    elif platform == "douyin":
        match = re.fullmatch(r"/(?:share/)?video/([0-9]{15,25})/*", parts.path)
        value = match[1] if match else parse_qs(parts.query).get("modal_id", [""])[0]
        if re.fullmatch(r"[0-9]{15,25}", value):
            return value, f"https://www.douyin.com/video/{value}"
    elif platform == "kuaishou":
        match = re.fullmatch(r"/short-video/([A-Za-z0-9_-]{6,80})/*", parts.path)
        if match:
            return match[1], f"https://www.kuaishou.com/short-video/{match[1]}"
    elif platform == "xiaohongshu":
        match = re.fullmatch(r"/(?:explore|discovery/item)/([a-f0-9]{24})/*", parts.path)
        if match:
            return match[1], f"https://www.xiaohongshu.com/explore/{match[1]}"
    elif platform == "weixin_channels" and host == "weixin.qq.com":
        match = re.fullmatch(r"/sph/([A-Za-z0-9_-]{4,160})/*", parts.path)
        if match:
            # This is an opaque share code, not a provider work ID.
            return "", f"https://weixin.qq.com/sph/{match[1]}"
    if strict:
        raise ValueError("请填写可识别的完整视频链接；抖音请使用含作品 ID 的 /video/ 链接。")
    return "", url.strip()

"""Cover image for a platform upload, made once per render and title."""
from __future__ import annotations

import hashlib
from pathlib import Path

from core.social.cover import make_cover
from web.services import bilibili, video_render
from web.settings import get_settings


def publish_cover(render: dict, title: str) -> str:
    """Absolute path of the cover for this render/title, or '' (the platform then picks its own frame)."""
    if cover := next((p for name in ('cover.png', 'cover.jpg') if (p := video_render.safe_file(render, name))), None):
        return str(cover.resolve())
    video = video_render.safe_file(render, 'video.mp4')
    if video is None:
        return ''
    key = hashlib.sha256(' '.join(title.split()).encode()).hexdigest()[:10]
    dest = Path(get_settings().paths.state_dir) / 'platforms' / 'covers' / f"{render['id']}-{key}.jpg"
    if dest.is_file() and dest.stat().st_size:
        return str(dest.resolve())
    made = make_cover(video, title, dest, bilibili.ffmpeg_binary())
    return str(made.resolve()) if made else ''

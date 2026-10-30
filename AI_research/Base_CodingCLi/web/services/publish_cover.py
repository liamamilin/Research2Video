"""Cover image for a platform upload, made once per render and title."""
from __future__ import annotations

import hashlib
from pathlib import Path

from core.social.cover import make_covers
from web.services import bilibili, video_render
from web.settings import get_settings


def publish_cover(render: dict, title: str) -> str:
    """Absolute path of the cover for this render/title, or '' (the platform then picks its own frame)."""
    return publish_covers(render, title)['cover']


def publish_covers(render: dict, title: str, *, portrait: bool = False) -> dict[str, str]:
    """Resolve orientation-specific assets and sample once when either one needs generating."""
    key = hashlib.sha256(' '.join(title.split()).encode()).hexdigest()[:10]
    folder = Path(get_settings().paths.state_dir) / 'platforms' / 'covers'
    destinations = ({'douyin_landscape': folder / f"{render['id']}-{key}-douyin-landscape-v1.jpg"} if portrait else
                    {'landscape': folder / f"{render['id']}-{key}.jpg"})
    if portrait:
        destinations['portrait'] = folder / f"{render['id']}-{key}-portrait-v1.jpg"
    result, missing = {}, {}
    for orientation, dest in destinations.items():
        prefix = {'portrait': 'cover-portrait', 'douyin_landscape': 'cover-douyin-landscape'}.get(orientation, 'cover')
        shipped = next((p for ext in ('png', 'jpg') if (p := video_render.safe_file(render, f'{prefix}.{ext}'))), None)
        if shipped:
            result[orientation] = shipped
        elif dest.is_file() and dest.stat().st_size:
            result[orientation] = dest
        else:
            missing[orientation] = dest
    video = video_render.safe_file(render, 'video.mp4')
    if missing and video is not None:
        result.update(make_covers(video, title, missing, bilibili.ffmpeg_binary()))
    return {('cover_portrait' if k == 'portrait' else 'cover'): str(result[k].resolve()) if k in result else ''
            for k in destinations}

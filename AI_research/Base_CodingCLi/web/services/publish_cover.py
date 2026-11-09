"""Cover image for a platform upload, made once per render and title."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from core.social import cover_copy
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
    # v2: number-led covers; covers made earlier in the title layout are not reused
    destinations = ({'douyin_landscape': folder / f"{render['id']}-{key}-douyin-landscape-v2.jpg"} if portrait else
                    {'landscape': folder / f"{render['id']}-{key}-v2.jpg"})
    if portrait:
        destinations['portrait'] = folder / f"{render['id']}-{key}-portrait-v2.jpg"
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
        # the script's frozen cover style (default: number-led); "title" keeps the title layout and asks no model
        copy = None if cover_style(render) != 'number' else \
            cover_copy.for_render(render, title, folder / f"{render['id']}-{key}-copy.json")
        result.update(make_covers(video, title, missing, bilibili.ffmpeg_binary(), copy,
                                  video_render.safe_file(render, 'cover-frame.png')))
    return {('cover_portrait' if k == 'portrait' else 'cover'): str(result[k].resolve()) if k in result else ''
            for k in destinations}


def cover_style(render: dict) -> str:
    """``title`` (default: the title over a content frame) or ``number``, from the script's frozen content_context.

    Number-led covers were the default for one morning (11-05); early plays fell against the day before, so they
    went back to being only what an A/B experiment's B arm asks for (spec/ab-testing.md §6)."""
    try:
        from core.video import store
        script = store.get_script(render.get('script_id') or '') or {}
    except Exception:  # noqa: BLE001 - an unreadable script keeps today's cover
        return 'title'
    return 'number' if (script.get('content_context') or {}).get('cover_style') == 'number' else 'title'


#: Cover styles the weekly review compares.
NUMBER_LED, TITLE, FRAME = '数字封面', '标题封面', '成片截帧'


def style(cover: str) -> str:
    """The style of an uploaded cover from its file: number-led when its copy was used, else the title layout;
    no cover means B站 took a frame of the video."""
    if not cover:
        return FRAME
    path = Path(cover)
    if path.name.endswith('-v2.jpg'):
        stem = path.name[:-len('-v2.jpg')]
        try:
            if json.loads(path.with_name(f'{stem}-copy.json').read_text(encoding='utf-8')).get('copy'):
                return NUMBER_LED
        except (OSError, ValueError):
            pass
    return TITLE


def upload_styles(state_dir: str) -> dict[str, str]:
    """render id -> cover style of its published B站 upload."""
    path = Path(state_dir) / 'platforms.db'
    if not path.is_file():
        return {}
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=2) as db:
        rows = db.execute("SELECT render_id, json_extract(payload,'$.cover') FROM uploads WHERE status='PUBLISHED'").fetchall()
    return {render: style(cover or '') for render, cover in rows}

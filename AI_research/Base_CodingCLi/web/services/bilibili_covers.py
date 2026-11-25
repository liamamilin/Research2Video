"""Independent, immutable B站 cover assets shared by import, UI and the outbox."""
from __future__ import annotations

import hashlib
import io
import json
import time
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, UnidentifiedImageError

from core.fileio import file_lock
from core.video import store
from web.services import bilibili, video_render

MAX_BYTES = 20 * 1024 * 1024
RATIOS = {'4x3': (4, 3), '16x9': (16, 9)}


def image_data(data: bytes, ratio: str) -> tuple[bytes, tuple[int, int]]:
    """Check the selected slot without cropping or stretching the artwork."""
    if ratio not in RATIOS or not data or len(data) > MAX_BYTES:
        raise bilibili.BilibiliError('请选择 4:3 或 16:9 的 PNG/JPEG 封面，单张不超过 20 MB。')
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in ('PNG', 'JPEG'):
                raise ValueError('format')
            width, height = image.size
            x, y = RATIOS[ratio]
            if width < 120 or height < 90 or width * height > 40_000_000:
                raise ValueError('size')
            if abs(width / height - x / y) > 0.015:
                raise bilibili.BilibiliError(f'这张图是 {width}×{height}，请为 {x}:{y} 展示位提供对应比例的封面。')
            image.load()
            if image.format == 'PNG':
                return data, image.size
            output = io.BytesIO()
            image.convert('RGB').save(output, format='PNG')
            return output.getvalue(), image.size
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        if isinstance(exc, bilibili.BilibiliError):
            raise
        raise bilibili.BilibiliError('封面不是可读取的 PNG/JPEG 图片，或图片尺寸过大。') from exc


def save(render_id: str, images: dict[str, bytes]) -> dict:
    """Register both slots atomically; queued jobs keep their original immutable files."""
    prepared = {ratio: image_data(data, ratio) for ratio, data in images.items()}
    if not prepared:
        raise bilibili.BilibiliError('请提供至少一张封面。')
    folder = video_render.files_dir(render_id)
    render = store.get_render(render_id)
    if not render or render['status'] != 'COMPLETED':
        raise bilibili.BilibiliError('请先选择已完成的成片。')
    folder.mkdir(parents=True, exist_ok=True)
    with file_lock(str(folder / 'bilibili-covers'), timeout=30):
        render = store.get_render(render_id)
        artifacts = list(render.get('artifacts') or [])
        for ratio, (data, size) in prepared.items():
            sha = hashlib.sha256(data).hexdigest()
            name = f'cover-bili{ratio}-{sha[:16]}.png'
            path = folder / name
            if not path.exists():
                path.write_bytes(data)
            artifacts = [a for a in artifacts if a['name'] != name]
            artifacts.append({'name': name, 'size': len(data), 'content_type': 'image/png',
                              'bilibili_ratio': ratio, 'width': size[0], 'height': size[1], 'sha256': sha})
        store.update_render(render_id, artifacts=artifacts)
    return preview(store.get_render(render_id))


def import_paths(render_id: str, paths: dict[str, str]) -> dict:
    images = {}
    for ratio, raw in paths.items():
        path = Path(raw).expanduser()
        if not path.is_absolute() or not path.is_file() or path.stat().st_size > MAX_BYTES:
            raise bilibili.BilibiliError('请提供可读取的封面绝对路径，单张不超过 20 MB。')
        images[ratio] = path.read_bytes()
    return save(render_id, images)


def selected(render: dict) -> dict[str, Path]:
    result = {}
    for ratio in RATIOS:
        rows = [a for a in render.get('artifacts') or [] if a.get('bilibili_ratio') == ratio]
        name = rows[-1]['name'] if rows else f'cover-bilibili-{ratio}.png'
        path = video_render.safe_file(render, name)
        if rows and path is None:
            raise bilibili.BilibiliError(f'已选的 {ratio} 封面文件不可用，请重新选择。')
        if path:
            image_data(path.read_bytes(), ratio)
            result[ratio] = path.resolve()
    return result


def preview(render: dict) -> dict:
    return {ratio: {'name': path.name, 'url': f'/api/video/renders/{render["id"]}/files/{path.name}'}
            for ratio, path in selected(render).items()}


def for_upload(render: dict, title: str) -> dict[str, str]:
    from web.services.publish_cover import publish_cover
    covers = selected(render)
    return {'cover': str(covers['16x9']) if '16x9' in covers else publish_cover(render, title),
            **({'cover43': str(covers['4x3'])} if '4x3' in covers else {})}


def update_published(task_id: str, account_uid: str) -> dict:
    """Apply the selected pair to one existing BV; repeat calls read back before editing."""
    from web.services import platform_manager as manager
    with manager.identity_lock():
        if not account_uid or manager.uid() != account_uid:
            raise bilibili.BilibiliError('平台账号已改变，请刷新页面后重试。')
        task = manager.upload_task(task_id)
        if task['status'] not in ('SUBMITTED', 'PUBLISHED') or not task['bvid']:
            raise bilibili.BilibiliError('只可更新已取得 BV 号的稿件封面。')
        render = store.get_render(task['render_id'])
        if not render:
            raise bilibili.BilibiliError('原成片记录不可用，无法取得配套封面。')
        covers = selected(render)
        if set(covers) != set(RATIOS):
            raise bilibili.BilibiliError('请先分别选择 4:3 和 16:9 封面。')
        folder = manager.directory() / 'uploads' / task_id
        folder.mkdir(parents=True, exist_ok=True)
        api = manager.client(account_uid)
        before = api.archive_for_edit(task['bvid'])
        remote = {ratio: api.upload_cover(path, folder) for ratio, path in covers.items()}
        expected = {'cover': remote['16x9'], 'cover43': remote['4x3']}
        def matches(snapshot):
            return all(urlparse(snapshot['archive'].get(k) or '').path == urlparse(v).path
                       for k, v in expected.items())
        receipt = {'bvid': task['bvid'], 'account_uid': account_uid, 'updated_at': time.time(),
                   'files': {k: str(p) for k, p in covers.items()}, 'remote': expected,
                   'before': {k: before['archive'].get(k, '') for k in expected}, 'status': 'CONFIRMED'}
        # Keep the current title/description/settings and the existing CID for every part.
        bilibili.private_json(folder / f'cover-edit-before-{time.time_ns()}.json', before)
        try:
            if not matches(before):
                receipt['status'] = 'UNKNOWN'
                bilibili.private_json(folder / 'cover-edit.json', receipt)
                api.edit_covers(before, **expected)
                receipt['status'] = 'SUBMITTED'
                after = api.archive_for_edit(task['bvid'])
                if matches(after):
                    receipt['status'] = 'CONFIRMED'
                receipt['after'] = {k: after['archive'].get(k, '') for k in expected}
        except bilibili.BilibiliRejected:
            if receipt['status'] == 'UNKNOWN':
                receipt['status'] = 'FAILED'
            raise
        finally:
            bilibili.private_json(folder / 'cover-edit.json', receipt)
            db = manager.store()
            with db.connect(write=True) as conn:
                current = conn.execute('SELECT payload FROM uploads WHERE id=? AND uid=?', (task_id, account_uid)).fetchone()
                payload = json.loads(current['payload'])
                payload['cover_update'] = receipt
                if receipt['status'] == 'CONFIRMED':
                    payload.update(cover=str(covers['16x9']), cover43=str(covers['4x3']))
                conn.execute('UPDATE uploads SET payload=? WHERE id=? AND uid=?',
                             (json.dumps(payload, ensure_ascii=False), task_id, account_uid))
        return receipt

"""Import an existing local movie and publish through the normal account-bound outbox."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field

from core.fileio import file_lock
from core.video import store
from web.settings import get_settings


class LocalVideoFields(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    title: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=20000)
    tags: list[str] | None = Field(default=None, max_length=30)
    tid: int | None = Field(default=None, ge=1, le=65535)
    first_comment: dict | None = None


class LocalVideoRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, str_strip_whitespace=True)
    action: Literal['prepare', 'submit'] = 'prepare'
    path: str = Field(min_length=1, max_length=4096)
    platform: str = Field(min_length=1, max_length=40)
    account_uid: str = Field(default='', max_length=300)
    fields: LocalVideoFields = Field(default_factory=LocalVideoFields)


def is_local(script: dict) -> bool:
    return bool((script.get('topic') or {}).get('local_video'))


def probe_video(path: Path) -> dict:
    """Read a video track, not merely a filename or an audio file's duration."""
    import platform
    import sys
    root = Path(__file__).resolve().parents[4]
    bundled = root / 'autovideo/g0' / f'{sys.platform}_{platform.machine().lower()}' / ('ffprobe.exe' if sys.platform == 'win32' else 'ffprobe')
    for binary in dict.fromkeys([str(bundled), shutil.which('ffprobe')]):
        if not binary or not os.access(binary, os.X_OK):
            continue
        try:
            run = subprocess.run([binary, '-v', 'error', '-show_entries',
                                  'format=duration,format_name:stream=codec_type,width,height', '-of', 'json', str(path)],
                                 capture_output=True, text=True, timeout=30, check=True)
            data = json.loads(run.stdout)
            video = next(s for s in data['streams'] if s.get('codec_type') == 'video' and s.get('width', 0) > 0)
            duration = float(data['format']['duration'])
            if not 0 < duration < float('inf'):
                raise ValueError('duration')
            if not {'mp4', 'mov'}.intersection(data['format']['format_name'].split(',')):
                raise ValueError('请先将这个视频导出为 MP4 或 MOV，再提供路径。')
            return {'duration': duration, 'width': video['width'], 'height': video['height']}
        except (OSError, subprocess.SubprocessError, KeyError, StopIteration, TypeError, ValueError):
            continue
    raise ValueError('无法读取这个视频。请提供可播放的 MP4 / MOV / M4V 文件，并确认本机 ffprobe 可用。')


def import_video(raw_path: str, actor: str) -> dict:
    from web.services import video_render, video_storage
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        raise ValueError('请提供运行本应用的电脑上的视频绝对路径。')
    try:
        path = path.resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise ValueError('找不到视频文件；路径需位于运行本应用的电脑上。') from exc
    if path.suffix.lower() not in ('.mp4', '.mov', '.m4v') or not path.is_file():
        raise ValueError('请选择 MP4 / MOV / M4V 视频文件，不能使用目录或网址。')
    source_stat = path.stat()
    if not source_stat.st_size:
        raise ValueError('这个视频文件是空的。')
    # The exact managed/archived path already has a version and publishing history.
    existing = store.get_render(path.parent.name) if len(path.parent.name) == 32 else None
    if existing and existing['status'] == 'COMPLETED':
        managed = video_render.safe_file(existing, 'video.mp4')
        if managed and managed.resolve() == path:
            return {'script_id': existing['script_id'], 'render_id': existing['id'], 'reused': True, 'imported': False}
    cached = store.local_video_by_path(str(path))
    if cached and cached['metadata'].get('source_mtime_ns') == source_stat.st_mtime_ns and cached['metadata']['size'] == source_stat.st_size:
        render = store.get_render(cached['render_id'])
        if render and video_render.safe_file(render, 'video.mp4'):
            return {**cached, 'reused': True, 'imported': True}
    target = video_render.files_dir('')
    mount = video_storage.volume(target)
    if mount and not os.path.ismount(str(mount)):
        raise ValueError('成片存储磁盘尚未挂载，请连接磁盘后重试。')
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.local-import-', dir=target) as temporary:
        copied = Path(temporary) / 'video.mp4'
        digest = hashlib.sha256()
        with path.open('rb') as source, copied.open('wb') as output:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                output.write(chunk); digest.update(chunk)
        if path.stat().st_mtime_ns != source_stat.st_mtime_ns or copied.stat().st_size != source_stat.st_size:
            raise ValueError('视频文件正在变化，请等待导出或复制完成后再试。')
        sha = digest.hexdigest()
        locks = Path(get_settings().paths.state_dir) / 'local_video_imports'
        locks.mkdir(exist_ok=True)
        with file_lock(str(locks / sha), timeout=60):
            asset = store.local_video_asset(sha)
            if asset and (saved := store.get_render(asset['render_id'])) and video_render.safe_file(saved, 'video.mp4'):
                return {**asset, 'reused': True, 'imported': True}
            metadata = {**probe_video(copied), 'title': path.stem, 'file_name': path.name,
                        'size': copied.stat().st_size, 'sha256': sha, 'source_mtime_ns': source_stat.st_mtime_ns}
            if asset:
                # The original imported file was moved out of storage: restore that asset,
                # retaining its account/version duplicate protection.
                folder = video_render.files_dir(asset['render_id']); folder.mkdir(exist_ok=True)
                os.replace(copied, folder / 'video.mp4')
                return {**asset, 'reused': True, 'imported': True}
            rid = uuid.uuid4().hex
            folder = video_render.files_dir(rid); folder.mkdir()
            os.replace(copied, folder / 'video.mp4')
            (folder / 'video.mp4').chmod(0o600)
            asset = store.register_local_video(sha, str(path), metadata, rid, actor)
            return {**asset, 'reused': False, 'imported': True}


def publish_pack(script: dict, version: dict) -> dict:
    """Imported clips carry publishing metadata, not an invented narration/report."""
    title = version['title']
    return {'title': title, 'description': '', 'tags': ['视频'], 'chapters': [], 'chapter_note': '',
            'warnings': [], 'references': [], 'cover': {'headline': title, 'badge': '本地视频'},
            'content': {'required': []}, 'freshness': {'warnings': []}, 'version': version['version']}


def execute(a: LocalVideoRequest, request, user: dict) -> dict:
    from web.services import assistant_management as management
    profile = management.account(a.platform)  # Validates the active platform before importing anything.
    if not profile.get('connected') or not profile.get('uid'):
        raise ValueError('请先连接指定平台的账号：' + management.platform_href(a.platform))
    if a.account_uid and a.account_uid != profile['uid']:
        raise ValueError('指定账号与当前平台账号不一致，请先切换平台账号。')
    asset = import_video(a.path, user['username'])
    rid, sid = asset['render_id'], asset['script_id']
    render, script = store.get_render(rid), store.get_script(sid)
    preview = management._native(request, 'GET', management.platform_path(a.platform) + '/preview/' + rid)
    saved = (preview.get('draft') or {}).get('payload') or {}
    fields = {key: saved.get(key, preview.get(key)) for key in ('title', 'description', 'tags', 'tid', 'first_comment')
              if key in saved or key in preview}
    if asset['imported'] and 'title' not in saved:
        fields['title'] = ''  # The filename identifies the asset; ask for its publication title.
    fields.update(a.fields.model_dump(exclude_none=True))
    from core.video.platform_publish import inspect
    check = inspect(a.platform, fields)
    if a.platform == 'bilibili' and not 1 <= (fields.get('tid') or 0) <= 65535:
        check['issues'].append('B站需要选择投稿分区 tid；可以沿用平台设置中的默认分区。')
        check['valid'] = False
    href = f'/video/{sid}?tab=publish&version={render["version"]}&render={rid}'
    result = {'local_video': {**((script.get('topic') or {}).get('local_video') or {}),
                              'file_name': Path(a.path).name, 'imported': asset['imported'], 'reused': asset['reused']},
              'script_id': sid, 'render_id': rid, 'version': render['version'], 'href': href,
              'platform': a.platform, 'account_uid': profile['uid'], 'account_name': profile.get('name') or '',
              'fields': fields, 'check': check, 'submitted': False,
              'references': [{'type': 'video', 'id': sid, 'title': render['title'], 'href': href}],
              'message': '视频已就绪，可按当前发布信息提交。' if check['valid'] else '视频已就绪，请按平台要求调整发布信息。'}
    prior = existing_upload(a.platform, profile['uid'], sid, render['version'])
    if prior:
        result.update(fields={k: v for k, v in prior['payload'].items() if k in fields}, existing_task=True)
        if a.action == 'prepare':
            return {**result, 'message': '这个视频已有投稿任务，发布信息沿用原任务。',
                    'publication_tasks': [{'platform': a.platform, 'id': prior['id'], 'account_uid': profile['uid']}]}
        resumed = management.publishing_change(SimpleNamespace(action='resume', platform=a.platform, account_uid=profile['uid'],
            items=[], task_id=prior['id'], video=''), request, user)
        return {**result, **resumed, 'href': href, 'existing_task': True,
                'references': result['references'] + resumed.get('references', [])}
    if store.has_publication(sid, render['version'], a.platform, profile['uid']):
        return {**result, 'status': 'completed', 'existing_publication': True,
                'message': '这个视频在指定平台和账号已有发布记录，可从发布记录查看。'}
    management._native(request, 'PUT', '/api/video/distributions/' + a.platform + '/draft',
                       body={'render_id': rid, 'account_uid': profile['uid'], 'payload': fields})
    result['draft_saved'] = True
    missing = [key for key in ('title', 'description') if not (fields.get(key) or '').strip()]
    if missing:
        labels = {'title': '标题', 'description': '简介'}
        return {**result, 'needs_input': True, 'missing_fields': missing,
                'message': '请补充视频的发布' + '和'.join(labels[key] for key in missing) + '。已填写的信息已保存，补充后可继续投稿。'}
    if a.action == 'prepare':
        return {**result, 'message': '视频已就绪，发布草稿已保存。' if check['valid'] else '发布草稿已保存，请按平台要求调整发布信息。'}
    if not check['valid']:
        return {**result, 'ok': False, 'message': '；'.join(check['issues'])}
    from web.services.assistant_tools.management import PublicationItem
    submitted = management.publishing_change(SimpleNamespace(action='submit', platform=a.platform, account_uid=profile['uid'],
        items=[PublicationItem(render_id=rid, fields=fields)], task_id='', video=''), request, user)
    return {**result, **submitted, 'href': href,
            'references': result['references'] + submitted.get('references', [])}


def existing_upload(platform: str, owner: str, script_id: str, version: int) -> dict | None:
    from web.services import platform_manager, douyin, social_publishing
    rows = (platform_manager.store().script_uploads(owner, script_id) if platform == 'bilibili'
            else douyin.store().list(uid=owner, kind='UPLOAD') if platform == 'douyin'
            else social_publishing.store(platform).list(uid=owner, kind='UPLOAD'))
    return next((row for row in rows if row['script_id'] == script_id and row['version'] == version), None)

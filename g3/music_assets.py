"""Decode local music uploads; freeze verified assets into every production version."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from uuid import UUID, uuid4

from g2.music_library import MusicError, PROJECT, asset_id, catalog, check_record, frozen_input, record, selection

MAX_BYTES = 32 * 1024 * 1024
MAX_SECONDS = 600
FORMATS = {'.wav': 'wav', '.mp3': 'mp3', '.flac': 'flac', '.ogg': 'ogg'}
FFMPEG = PROJECT / 'g0/darwin_arm64/ffmpeg'
FFPROBE = PROJECT / 'g0/darwin_arm64/ffprobe'


def root_dir(data_dir: Path) -> Path:
    root = data_dir / 'music-assets'
    if root.is_symlink():
        raise MusicError('音乐素材目录不安全')
    root.mkdir(parents=True, exist_ok=True)
    return root


def asset_dir(data_dir: Path, identity: str) -> Path:
    identity = asset_id(identity)
    folder = root_dir(data_dir) / identity
    if folder.is_symlink() or (folder / 'asset.json').is_symlink() or (folder / 'asset.wav').is_symlink():
        raise MusicError('音乐素材路径不安全')
    return folder


def load(data_dir: Path, identity: str) -> tuple[dict, Path]:
    folder = asset_dir(data_dir, identity)
    try:
        meta = json.loads((folder / 'asset.json').read_text(encoding='utf-8'))
        path = folder / 'asset.wav'
        if meta['id'] != identity or hashlib.sha256(path.read_bytes()).hexdigest() != meta['sha256']:
            raise MusicError('音乐素材校验失败，请重新导入')
        return meta, path
    except (OSError, KeyError, ValueError) as exc:
        raise MusicError('音乐素材不可用或校验失败，请重新导入') from exc


def _command(args: list) -> str:
    try:
        out = subprocess.run([str(a) for a in args], capture_output=True, text=True, timeout=45)
        if out.returncode:
            raise MusicError('音乐文件无法解码，请选择有效的音频文件')
        return out.stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MusicError('本机音频解码暂不可用，请稍后重试') from exc


def _save(data_dir: Path, identity: str, raw: bytes, filename: str, extra: dict, *, expected_duration: float | None = None) -> dict:
    import numpy as np
    import soundfile as sf
    if not raw or len(raw) > MAX_BYTES:
        raise MusicError('每份音乐文件最大 32 MB')
    extension = Path(filename).suffix.lower()
    if extension not in FORMATS:
        raise MusicError('请选择 WAV、MP3、FLAC 或 OGG 音乐文件')
    root = root_dir(data_dir)
    temporary = Path(tempfile.mkdtemp(prefix='import-', dir=root))
    try:
        source = temporary / ('source' + extension)
        source.write_bytes(raw)
        # Force a self-contained audio demuxer. Uploaded text/playlists never become network requests.
        info = json.loads(_command([FFPROBE, '-v', 'error', '-protocol_whitelist', 'file,pipe', '-f', FORMATS[extension],
                                    '-show_streams', '-show_format', '-of', 'json', source]))
        streams = info.get('streams', [])
        if not any(s.get('codec_type') == 'audio' for s in streams):
            raise MusicError('文件中没有可用音轨')
        duration = float(info.get('format', {}).get('duration') or 0)
        if not 1 <= duration <= MAX_SECONDS:
            raise MusicError('音乐素材时长需为 1 秒至 10 分钟')
        target = temporary / 'asset.wav'
        _command([FFMPEG, '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-f', FORMATS[extension],
                  '-i', source, '-map', '0:a:0', '-vn', '-t', str(MAX_SECONDS + 1), '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s16le', target])
        count, energy, peak = 0, 0.0, 0.0
        with sf.SoundFile(target) as audio:
            frames, rate = len(audio), audio.samplerate
            for block in audio.blocks(blocksize=48000, dtype='float32', always_2d=True):
                count += block.size; energy += float(np.sum(block.astype('float64') ** 2)); peak = max(peak, float(np.max(np.abs(block))))
        if not 1 <= frames / rate <= MAX_SECONDS or peak < .0001 or energy / max(1, count) < 1e-10:
            raise MusicError('音乐素材过短、过长或近乎无声')
        if expected_duration is not None and abs(frames / rate - expected_duration) > .5:
            raise MusicError('生成音乐的实际时长与设置不一致，未入库')
        meta = {'id': identity, 'name': Path(filename).stem[:80], 'filename': Path(filename).name[:120], 'kind': 'uploaded',
                'duration_seconds': round(frames / rate, 6), 'sample_rate': rate, 'channels': 2,
                'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'source_sha256': hashlib.sha256(raw).hexdigest(),
                'source': '用户导入的本机音乐素材', **extra}
        (temporary / 'asset.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
        source.unlink()
        folder = asset_dir(data_dir, identity)
        if folder.exists():
            raise MusicError('素材编号已存在，未覆盖原文件')
        os.rename(temporary, folder)
        return meta
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def import_audio(data_dir: Path, raw: bytes, filename: str) -> dict:
    return public(_save(data_dir, str(uuid4()), raw, filename, {}))


def ensure(data_dir: Path, identity: str) -> tuple[dict, Path]:
    folder = asset_dir(data_dir, identity)
    if folder.exists():
        return load(data_dir, identity)
    builtin = next((a for a in catalog() if a['id'] == identity), None)
    if not builtin:
        raise MusicError('音乐素材不存在，请重新导入')
    lock = root_dir(data_dir) / (identity + '.lock')
    if lock.is_symlink():
        raise MusicError('音乐缓存路径不安全')
    with lock.open('a') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        if folder.exists():
            return load(data_dir, identity)
        from g2.creative_library import asset_root
        root = asset_root(); path = root / builtin['relative_path']
        if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
            raise MusicError('参考音乐暂不可用，请连接素材磁盘或导入音乐')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != builtin['source_sha256']:
            raise MusicError('参考音乐与收录版本不一致，未使用已改变的文件')
        _save(data_dir, identity, raw, path.name, {k:v for k,v in builtin.items() if k not in {'relative_path'}} | {'kind':'builtin'})
        return load(data_dir, identity)


def public(meta: dict) -> dict:
    return {**meta, 'preview_url': f"/api/music-assets/{meta['id']}/preview"}


def listing(data_dir: Path) -> list[dict]:
    from g2.creative_library import asset_root
    items = []
    for item in catalog():
        folder = asset_dir(data_dir, item['id'])
        if folder.exists():
            try: items.append({**public(load(data_dir, item['id'])[0]), 'available': True})
            except MusicError: items.append({**public({k:v for k,v in item.items() if k != 'relative_path'}), 'available': False})
        else:
            path = asset_root() / item['relative_path']
            items.append({**public({k:v for k,v in item.items() if k != 'relative_path'}), 'kind':'builtin', 'available':path.is_file() and not path.is_symlink()})
    for path in sorted(root_dir(data_dir).glob('*/asset.json')):
        try:
            UUID(path.parent.name)
            meta, _ = load(data_dir, path.parent.name)
            items.append({**public(meta), 'available':True})
        except (ValueError, OSError):
            continue
    return items


def freeze(data_dir: Path, input_dir: Path, request: dict) -> dict:
    config = selection(request.get('background_music'))
    if config is None:
        return request
    meta, source = None, None
    if config['enabled']:
        prior = request.get('background_music_resolved')
        if prior is not None:
            prior = check_record(prior)
        if request.get('parent_job_id') and ((prior or {}).get('asset') or {}).get('id') == config['asset_id']:
            parent_id = str(UUID(request['parent_job_id']))
            parent = data_dir / 'jobs' / parent_id / 'input'
            if parent.parent.is_symlink() or parent.is_symlink() or (parent / 'input.json').is_symlink():
                raise MusicError('父任务音乐路径不安全')
            saved = json.loads((parent / 'input.json').read_text(encoding='utf-8'))
            parent_record, source = frozen_input(parent, saved)
            if parent_record['asset'] != prior['asset']:
                raise MusicError('父版本音乐与当前记录不一致')
            meta = parent_record['asset']
        else:
            meta, source = ensure(data_dir, config['asset_id'])
        folder = input_dir / 'music'; folder.mkdir()
        shutil.copyfile(source, folder / 'asset.wav')
    return {**request, 'background_music':config, 'background_music_resolved':record(config, meta)}

"""Content-bound listening records and reusable instrumental selections.

Audio and historical asset metadata stay immutable. Technical sample continuity
and manual listening are separate conclusions, never inferred from one another.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from g2.music_library import MusicError, asset_id

CATALOG = Path(__file__).resolve().parents[1] / 'g2/music_library/common.json'


def folder(data_dir: Path, identity: str) -> Path:
    root = data_dir / 'music-curation'
    target = root / asset_id(identity)
    if root.is_symlink() or target.is_symlink():
        raise MusicError('音乐验收记录路径不安全')
    target.mkdir(parents=True, exist_ok=True)
    return target


@contextmanager
def locked(data_dir: Path, identity: str):
    target = folder(data_dir, identity)
    lock = target / 'record.lock'
    if lock.is_symlink():
        raise MusicError('音乐验收锁路径不安全')
    with lock.open('a') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield target


def write(path: Path, value: dict) -> None:
    if path.is_symlink():
        raise MusicError('音乐验收记录不安全')
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        with temporary.open('x') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read(path: Path, checksum: str) -> dict | None:
    if path.is_symlink():
        raise MusicError('音乐验收记录不安全')
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text())
        return value if value.get('asset_sha256') == checksum else None
    except (OSError, ValueError):
        return None


def definition(identity: str, checksum: str | None, source_checksum: str | None = None) -> dict | None:
    catalog = json.loads(CATALOG.read_text())
    return next((item for item in catalog['assets']
                 if item['id'] == identity and (item['asset_sha256'] == checksum
                     or (not checksum and source_checksum and item.get('source_sha256') == source_checksum))), None)


def describe(data_dir: Path, meta: dict) -> dict:
    target = folder(data_dir, meta['id'])
    checksum = meta.get('sha256')
    curated = definition(meta['id'], checksum, meta.get('source_sha256') if meta.get('kind')=='builtin' else None)
    technical = read(target / 'technical.json', checksum) if checksum else None
    review = read(target / 'review.json', checksum) if checksum else None
    result = {'common': curated is not None, 'technical': technical,
              'review': review or {'status':'pending'},
              'loop_method':'250 毫秒交叉淡化循环；原曲不声明无缝循环',
              'instrumental_basis': '生成条件，待试听确认' if meta.get('kind') == 'generated'
                                    else '器乐素材来源' if meta.get('kind') == 'builtin' else '未确认'}
    if curated:
        result.update({k:v for k,v in curated.items() if k not in {'id','asset_sha256','source_sha256'}})
    if technical and technical['status'] == 'passed':
        result['loop_preview_url'] = f"/api/music-assets/{meta['id']}/loop-preview?sha256={meta['sha256']}"
    mixed = read(target / 'narration-preview.json', checksum) if checksum else None
    if mixed:
        result['narration_preview_url'] = f"/api/music-assets/{meta['id']}/narration-preview?sha256={checksum}"
        result['narration_preview_job_id'] = mixed['video_job_id']
    return result


def enrich(data_dir: Path, items: list[dict]) -> list[dict]:
    return [{**item, **({'curation':describe(data_dir,item)} if item.get('available') else {})}
            for item in items]


def analyze(data_dir: Path, identity: str) -> dict:
    from g3 import music_assets
    from g2.music_mix import LoopReader, RATE
    import numpy as np
    import soundfile as sf
    meta, source = music_assets.ensure(data_dir, identity)
    with locked(data_dir, identity) as target:
        cached = read(target / 'technical.json', meta['sha256'])
        preview = target / 'loop-preview.wav'
        if cached and not preview.is_symlink() and preview.is_file():
            if hashlib.sha256(preview.read_bytes()).hexdigest() == cached['loop_preview_sha256']:
                return cached
        energy = 0.0; count = 0; peak = 0.0
        with sf.SoundFile(source) as audio:
            frames, rate, channels = len(audio), audio.samplerate, audio.channels
            if rate != RATE or channels != 2:
                raise MusicError('音乐须使用库内48kHz双声道规范化版本')
            for block in audio.blocks(blocksize=RATE, dtype='float32', always_2d=True):
                if not np.isfinite(block).all():
                    raise MusicError('音乐含无效采样')
                energy += float(np.sum(block.astype('float64')**2)); count += block.size
                peak = max(peak, float(np.max(np.abs(block))))
            reader = LoopReader(audio)
            # Context around the actual production mix seam, with the join at 3s.
            offset = min(3 * RATE, reader.period)
            reader.cursor = reader.period - offset
            sound = reader.read(offset + 3 * RATE)
            delta = np.abs(np.diff(sound.astype('float64'), axis=0))
            radius = min(RATE // 100, offset, len(sound) - offset - 1)
            seam_delta = float(np.max(delta[offset-radius:offset+radius]))
            baseline = float(np.quantile(delta, .999))
            continuity = seam_delta <= max(.025, baseline * 3)
            duration = frames / rate
            rms = (energy / max(1,count))**.5
            healthy = 1 <= duration <= 600 and .00001 < rms and .0001 < peak < 1
            # Preview-only end fades; the central seam is unchanged.
            fade = min(480, len(sound)//4)
            sound[:fade] *= np.linspace(0,1,fade)[:,None]
            sound[-fade:] *= np.linspace(1,0,fade)[:,None]
            temporary = target / (uuid4().hex + '.wav')
            try:
                sf.write(temporary, sound, RATE, subtype='PCM_16')
                if preview.is_symlink():
                    raise MusicError('音乐试听路径不安全')
                os.replace(temporary, preview)
            finally:
                temporary.unlink(missing_ok=True)
        result = {'schema_version':1, 'asset_sha256':meta['sha256'], 'status':'passed' if healthy and continuity else 'needs_work',
                  'checked_at':datetime.now(timezone.utc).isoformat(), 'method':'full PCM decode and actual mix-loop sample continuity',
                  'duration_seconds':duration, 'sample_rate':rate, 'channels':channels, 'peak':peak, 'rms':rms,
                  'full_decode':healthy, 'loop_sample_continuity':continuity, 'seam_max_sample_delta':seam_delta,
                  'reference_sample_delta_p999':baseline, 'loop_crossfade_seconds':reader.fade / RATE,
                  'preview_join_seconds':offset / RATE, 'loop_preview_sha256':hashlib.sha256(preview.read_bytes()).hexdigest(),
                  'musical_loop_naturalness':'requires_listening', 'vocal_absence':'requires_listening'}
        write(target / 'technical.json', result)
        return result


def preview(data_dir: Path, identity: str, checksum: str) -> Path:
    from g3 import music_assets
    meta, _ = music_assets.ensure(data_dir, identity)
    target = folder(data_dir, identity)
    report = read(target / 'technical.json', meta['sha256'])
    path = target / 'loop-preview.wav'
    if (checksum != meta['sha256'] or not report or report['status'] != 'passed' or path.is_symlink()
            or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != report['loop_preview_sha256']):
        raise MusicError('循环试听不可用或已改变，请重新检查')
    return path


def narration_preview(data_dir: Path, identity: str, checksum: str) -> Path:
    from g3 import music_assets
    meta, _ = music_assets.ensure(data_dir, identity)
    target = folder(data_dir, identity)
    record = read(target / 'narration-preview.json', meta['sha256'])
    path = target / 'narration-preview.wav'
    if (checksum != meta['sha256'] or not record or path.is_symlink() or not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != record['preview_sha256']):
        raise MusicError('旁白配乐试听不可用或已改变')
    return path


def save_review(data_dir: Path, identity: str, value: dict) -> dict:
    from g3 import music_assets
    meta, _ = music_assets.ensure(data_dir, identity)
    if value.get('listened') is not True:
        raise MusicError('请实际试听后再保存听感结论')
    if meta['sha256'] != value['asset_sha256']:
        raise MusicError('音乐版本已变化，请重新试听')
    with locked(data_dir, identity) as target:
        checked = read(target / 'technical.json', meta['sha256'])
        if not checked or checked['status'] != 'passed':
            raise MusicError('请先完成本曲技术检查再记录试听')
        flags = [value[key] for key in ('no_unexpected_vocals','natural_loop','narration_friendly')]
        status = 'passed' if all(flags) else 'needs_work'
        result = {**value, 'status':status, 'basis':'manual_listening', 'checked_at':datetime.now(timezone.utc).isoformat()}
        previous = read(target / 'review.json', meta['sha256'])
        if previous:
            write(target / ('review-' + uuid4().hex + '.json'), previous)
        write(target / 'review.json', result)
        return result

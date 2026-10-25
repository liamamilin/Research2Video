"""Registered local music materials and versioned, bounded mix selections."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[1]


class MusicError(ValueError):
    code = 'MUSIC_INVALID'


def digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def catalog() -> list[dict]:
    return json.loads((ROOT / 'catalog.json').read_text(encoding='utf-8'))['assets']


def asset_id(value: str) -> str:
    if not isinstance(value, str):
        raise MusicError('请选择音乐素材')
    if value in {a['id'] for a in catalog()}:
        return value
    try:
        if str(UUID(value)) != value:
            raise ValueError()
    except (ValueError, AttributeError) as exc:
        raise MusicError('音乐素材编号无效') from exc
    return value


def selection(value: dict | None) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {'enabled', 'asset_id', 'level_db', 'ducking', 'loop', 'fade_seconds'}:
        raise MusicError('配乐配置仅支持开关、素材、音量、自动压低、循环和淡入淡出')
    out = {'enabled': False, 'level_db': -18.0, 'ducking': True, 'loop': True, 'fade_seconds': 1.2, **value}
    for key in ['enabled', 'ducking', 'loop']:
        if type(out[key]) is not bool:
            raise MusicError(f'{key} 须为布尔值')
    for key, low, high in [('level_db', -30, -12), ('fade_seconds', 0.1, 3)]:
        if isinstance(out[key], bool) or not isinstance(out[key], (int, float)) or not math.isfinite(out[key]) or not low <= out[key] <= high:
            raise MusicError(f'{key} 须在 {low}–{high} 之间')
        out[key] = float(out[key])
    if out.get('asset_id') is not None:
        out['asset_id'] = asset_id(out['asset_id'])
    if out['enabled'] and not out.get('asset_id'):
        raise MusicError('开启背景配乐后请选择音乐素材')
    return out


def check_record(value: dict) -> dict:
    if not isinstance(value, dict) or value.get('schema_version') != 1 or value.get('sha256') != digest({k:v for k,v in value.items() if k != 'sha256'}):
        raise MusicError('已保存的配乐记录完整性校验失败')
    if not isinstance(value.get('selection'), dict) or value['selection'] != selection(value['selection']):
        raise MusicError('配乐记录的设置无效')
    return copy.deepcopy(value)


def record(config: dict, asset: dict | None) -> dict:
    result = {'schema_version': 1, 'selection': selection(config), 'asset': asset, 'music_generated': bool((asset or {}).get('kind') == 'generated'),
              'scope': 'content_only', 'engine': 'local-mix-v1'}
    result['sha256'] = digest(result)
    return result


def frozen_input(input_dir: Path, request: dict) -> tuple[dict | None, Path | None]:
    config = selection(request.get('background_music'))
    frozen = request.get('background_music_resolved')
    if config is None and frozen is None:
        return None, None
    frozen = check_record(frozen)
    if frozen['selection'] != config:
        raise MusicError('任务设置与已保存的配乐快照不一致')
    if not config['enabled']:
        if frozen.get('asset') is not None:
            raise MusicError('关闭配乐的记录不应含音乐资产')
        return frozen, None
    meta = frozen.get('asset')
    if not isinstance(meta, dict) or meta.get('id') != config['asset_id']:
        raise MusicError('配乐素材与任务不一致')
    path = input_dir / 'music' / 'asset.wav'
    if path.parent.is_symlink() or path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != meta.get('sha256'):
        raise MusicError('任务保存的音乐素材不可用或已改变')
    return frozen, path

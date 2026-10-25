"""Bounded local instrumental requests, separate from the video/mixing contract."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

from g2.creative_library import asset_root

PRESETS = {
    'ambient': {'name': '轻柔氛围', 'bpm': 72, 'key': 'C Major', 'topics': ['原理科普', '操作教程'],
                'caption': 'Gentle ambient instrumental background for spoken explanation. Warm soft synthesizer pads, sparse plucked notes, subtle rounded bass, minimal percussion, calm and spacious, restrained dynamics.'},
    'piano': {'name': '温暖钢琴', 'bpm': 80, 'key': 'C Major', 'topics': ['操作教程', '日常叙事'],
              'caption': 'Warm gentle instrumental background for narration. Soft felt piano, slow simple repeating motif, subtle sustained strings, quiet bass, light percussion, clear and reassuring, restrained dynamics.'},
    'electronic': {'name': '轻快电子', 'bpm': 110, 'key': 'A Minor', 'topics': ['AI快讯', '产品更新'],
                   'caption': 'Light electronic instrumental background for technology news. Warm analog synthesizer chords, clean plucked melody, rounded bass and an understated drum machine groove, bright and focused, restrained dynamics.'},
}


class GenerationError(ValueError):
    """A readable local generation error with a stable API code."""
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def model_root() -> Path:
    return Path(os.environ.get('AUTOVIDEO_MUSIC_MODEL_ROOT', asset_root() / 'music-pilot')).expanduser().resolve()


def manifest() -> dict:
    return json.loads((Path(__file__).parent / 'runtime-manifest.json').read_text())


def digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def resolve(request: dict, seed: int) -> dict:
    preset = PRESETS[request['style']]
    return {**copy.deepcopy(request), 'seed': seed,
            'caption': preset['caption'] + (' ' + request['description'] if request['description'] else '') + ' No vocals, no speech, no singing.',
            'bpm': request.get('bpm') or preset['bpm'], 'keyscale': preset['key'], 'instrumental': True,
            'topics': preset['topics'],
            'lyrics': '[Instrumental]', 'inference_steps': 8, 'thinking': False,
            'engine': manifest()['engine'], 'code_revision': manifest()['code_revision'],
            'weight_revision': manifest()['weight_revision']}


def readiness() -> dict:
    """Probe files without importing or loading the music model in the web process."""
    root = model_root()
    missing = []
    if not (root / 'venv/bin/python').is_file():
        missing.append('音乐运行环境')
    if not (root / 'source/acestep/inference.py').is_file():
        missing.append('音乐生成程序')
    for item in manifest()['weights']:
        path = root / 'models' / item['file']
        if not path.is_file() or path.stat().st_size != item['bytes']:
            missing.append('本机模型文件'); break
    return {'ready': not missing, 'engine': manifest()['engine'], 'mode': 'instrumental',
            'duration_seconds': [10, 30], 'styles': [{'id': key, **{k:v for k,v in value.items() if k != 'caption'}} for key,value in PRESETS.items()],
            'message': '本机纯音乐生成已就绪；与视频制作依次执行。' if not missing else '未找到' + '、'.join(missing) + '，仍可导入音乐。',
            'downloads_automatically': False}

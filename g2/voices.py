"""Supported, local voice profiles. Accent labels are separate from timbre."""
from pathlib import Path

KOKORO_VOICES = {
    'zf_xiaoxiao': '小晓 · 普通话女声', 'zf_xiaobei': '小贝 · 普通话女声',
    'zf_xiaoni': '小妮 · 普通话女声', 'zf_xiaoyi': '小伊 · 普通话女声',
    'zm_yunjian': '云健 · 普通话男声', 'zm_yunxi': '云希 · 普通话男声',
    'zm_yunxia': '云夏 · 普通话男声', 'zm_yunyang': '云扬 · 普通话男声',
}
QWEN_VOICES = {'qwen_vivian': ('Vivian', 'Vivian · 普通话女声'),
               'qwen_serena': ('Serena', 'Serena · 普通话女声'),
               'qwen_uncle_fu': ('Uncle_Fu', '福叔 · 普通话男声'),
               'qwen_dylan': ('Dylan', 'Dylan · 北京口音男声'),
               'qwen_eric': ('Eric', 'Eric · 四川口音男声')}
VOICE_IDS = tuple(KOKORO_VOICES) + tuple(QWEN_VOICES)
SPEEDS = (0.5, 1.0, 1.5, 2.0)


def qwen_paths(project: Path):
    return (project / 'g3/data/voice-runtime/bin/python', project / 'g3/data/voice-models/qwen3-0.6b-4bit')


def catalog(project: Path):
    runtime, model = qwen_paths(project)
    ready = (runtime.is_file() and (model / 'config.json').is_file() and (model / 'model.safetensors').is_file()
             and (model / 'speech_tokenizer/model.safetensors').is_file() and (model / '.ready').is_file())
    return [*({'id': vid, 'label': label, 'engine': 'Kokoro', 'accent': '普通话', 'available': True} for vid,label in KOKORO_VOICES.items()),
            *({'id': vid, 'label': item[1], 'engine': 'Qwen3-TTS', 'accent': '四川口音' if vid=='qwen_eric' else '北京口音' if vid=='qwen_dylan' else '普通话', 'available': ready} for vid,item in QWEN_VOICES.items())]

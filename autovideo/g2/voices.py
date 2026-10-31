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
#: VoxCPM2 has no built-in speakers: every segment is cloned from one short anchor clip so the voice does not drift.
VOXCPM_VOICES = {'voxcpm_anchor': 'VoxCPM2 · 普通话女声（参考音色）'}
VOXCPM_ANCHOR_TEXT = '假设你有个每晚跑的脚本，系统提示第一行是当前时间。'
VOXCPM_DEFAULT_CHAIN = 'anchor'
VOICE_IDS = tuple(KOKORO_VOICES) + tuple(QWEN_VOICES) + tuple(VOXCPM_VOICES)
SPEEDS = (0.5, 1.0, 1.25, 1.5, 2.0)


def qwen_paths(project: Path):
    return (project / 'g3/data/voice-runtime/bin/python', project / 'g3/data/voice-models/qwen3-0.6b-4bit')


def voxcpm_paths(project: Path):
    """(runtime python, model dir, anchor wav); the transcript of the anchor is VOXCPM_ANCHOR_TEXT."""
    return (project / 'g3/data/voice-runtime/bin/python', project / 'g3/data/voice-models/voxcpm2-8bit',
            project / 'g2/voice_assets/voxcpm2_anchor.wav')


def mlx_engine(voice: str, project: Path):
    """The fixed local MLX entry point for a voice, or None for Kokoro (which runs in the pipeline process)."""
    if voice in QWEN_VOICES:
        return {'entry': project / 'g2/qwen_voice.py', 'python': qwen_paths(project)[0], 'weights': qwen_paths(project)[1] / 'model.safetensors',
                'provider': 'local_qwen_mlx', 'model': 'Qwen3-TTS-0.6B-4bit'}
    if voice in VOXCPM_VOICES:
        python, model, _ = voxcpm_paths(project)
        return {'entry': project / 'g2/voxcpm_voice.py', 'python': python, 'weights': model / 'model.safetensors',
                'provider': 'local_voxcpm_mlx', 'model': 'VoxCPM2-8bit'}
    return None


def catalog(project: Path):
    runtime, model = qwen_paths(project)
    ready = (runtime.is_file() and (model / 'config.json').is_file() and (model / 'model.safetensors').is_file()
             and (model / 'speech_tokenizer/model.safetensors').is_file() and (model / '.ready').is_file())
    vox_runtime, vox_model, vox_anchor = voxcpm_paths(project)
    vox_ready = (vox_runtime.is_file() and (vox_model / 'config.json').is_file() and (vox_model / 'model.safetensors').is_file()
                 and vox_anchor.is_file() and (vox_model / '.ready').is_file())
    return [*({'id': vid, 'label': label, 'engine': 'Kokoro', 'accent': '普通话', 'available': True} for vid,label in KOKORO_VOICES.items()),
            *({'id': vid, 'label': item[1], 'engine': 'Qwen3-TTS', 'accent': '四川口音' if vid=='qwen_eric' else '北京口音' if vid=='qwen_dylan' else '普通话', 'available': ready} for vid,item in QWEN_VOICES.items()),
            *({'id': vid, 'label': label, 'engine': 'VoxCPM2', 'accent': '普通话', 'available': vox_ready} for vid,label in VOXCPM_VOICES.items())]

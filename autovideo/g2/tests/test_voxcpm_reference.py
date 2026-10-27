import json
import sys
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from g2 import voxcpm_voice


@pytest.mark.parametrize('chain, expected', [(None, ['anchor', 'anchor', 'anchor']),
                                           ('chain', ['anchor', 'previous', 'previous']),
                                           ('guarded', ['anchor', 'previous', 'previous'])])
def test_default_generation_reuses_original_reference_instead_of_generated_speech(tmp_path, monkeypatch, chain, expected):
    """Exercise the model inputs and saved receipt without loading model weights."""
    rate = 48000
    anchor = .1 * np.sin(2 * np.pi * 220 * np.arange(rate * 4) / rate)
    anchor_path = tmp_path / 'anchor.wav'
    sf.write(anchor_path, anchor, rate)
    monkeypatch.setattr(voxcpm_voice, 'ROOT', tmp_path)
    monkeypatch.setattr(voxcpm_voice, 'voxcpm_paths', lambda root: (None, root / 'model', anchor_path))
    calls = []
    anchor_rate = voxcpm_voice.units(voxcpm_voice.VOXCPM_ANCHOR_TEXT) / voxcpm_voice.active_seconds(anchor, rate)

    def generate(**kwargs):
        calls.append(kwargs)
        count = round(voxcpm_voice.units(kwargs['text']) / anchor_rate * rate)
        audio = .12 * np.sin(2 * np.pi * 330 * np.arange(count) / rate)
        return [SimpleNamespace(audio=audio)]

    mx = ModuleType('mlx.core')
    mx.random = SimpleNamespace(seed=lambda value: None)
    mx.clear_cache = lambda: None
    mlx = ModuleType('mlx')
    mlx.core = mx
    monkeypatch.setitem(sys.modules, 'mlx', mlx)
    monkeypatch.setitem(sys.modules, 'mlx.core', mx)
    for name in ['mlx_audio', 'mlx_audio.tts']:
        module = ModuleType(name)
        module.__path__ = []
        monkeypatch.setitem(sys.modules, name, module)
    utils = ModuleType('mlx_audio.tts.utils')
    utils.load_model = lambda path: SimpleNamespace(sample_rate=rate, generate=generate)
    monkeypatch.setitem(sys.modules, 'mlx_audio.tts.utils', utils)
    settings = {'compile': False, 'reference_cache': False}
    if chain is not None:
        settings['chain'] = chain
    plan = tmp_path / 'plan.json'
    plan.write_text(json.dumps({'voice_settings': settings, 'segments': [
        {'id': f'n{i}', 'chapter_id': 'c1', 'text': '同一任务需要稳定清楚的旁白。'} for i in range(3)]}))
    output = tmp_path / 'output'
    voxcpm_voice.generate(plan, output, 'voxcpm_anchor')
    assert len(calls) == 3
    original = calls[0]['ref_audio']
    assert all(call['ref_audio'] is original for call in calls)
    for call, prompt in zip(calls, expected):
        assert (call['prompt_audio'] is original) == (prompt == 'anchor')
    report = json.loads((output / 'voice-report.json').read_text())
    assert [row['prompt'] for row in report['segments']] == expected
    assert report['chain'] == (chain or 'anchor')
    assert all((output / f'n{i}.wav').is_file() for i in range(3))


@pytest.mark.parametrize('override, expected', [(None, 'anchor'), ('guarded', 'guarded')])
def test_production_synthesis_passes_stable_default_and_honors_explicit_override(tmp_path, monkeypatch, override, expected):
    from g2 import run_g2

    class Captured(Exception):
        pass

    captured = {}

    def capture(engine, plan, settings):
        captured.update(settings)
        raise Captured

    placeholder = tmp_path / 'installed'
    placeholder.touch()
    monkeypatch.setattr(run_g2, 'KOKORO_MODEL', placeholder)
    monkeypatch.setattr(run_g2, 'KOKORO_VOICES', placeholder)
    monkeypatch.setattr(run_g2, 'ASSETS', tmp_path / 'assets')
    monkeypatch.setattr(run_g2, 'SEGMENTS_DIR', tmp_path / 'segments')
    monkeypatch.setattr(run_g2, 'VOICE', 'voxcpm_anchor')
    monkeypatch.setattr(run_g2, 'synthesize_mlx_voice', capture)
    if override is None:
        monkeypatch.delenv('AUTOVIDEO_G2_VOXCPM_CHAIN', raising=False)
    else:
        monkeypatch.setenv('AUTOVIDEO_G2_VOXCPM_CHAIN', override)
    with pytest.raises(Captured):
        run_g2.synthesize_and_align({'segments': []})
    assert captured['chain'] == expected

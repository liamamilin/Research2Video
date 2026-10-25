import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from g2.music_library import MusicError, frozen_input, record, selection
from g2.music_mix import RATE, run, write_mix

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('value', [[], {'enabled': 1}, {'enabled': True}, {'asset_id': '../audio'},
    {'level_db': -11}, {'level_db': True}, {'level_db': float('nan')}, {'fade_seconds': 0},
    {'fade_seconds': 4}, {'url': 'https://example.com/audio.mp3'}, {'loop': 'yes'}])
def test_invalid_music_is_rejected(value):
    with pytest.raises(MusicError):
        selection(value)


def signals(root):
    positions = np.arange(RATE * 7) / RATE
    speaking = ((positions >= 1) & (positions < 3)) | ((positions >= 4) & (positions < 6))
    voice = .22 * np.sin(positions * 2 * np.pi * 440) * speaking
    original = root / 'voice.wav'
    sf.write(original, np.repeat(voice[:, None], 2, axis=1), RATE, subtype='FLOAT')
    music = root / 'music.wav'
    t = np.arange(round(RATE * 2.3)) / RATE
    sf.write(music, np.repeat((.2 * np.sin(t * 2 * np.pi * 137))[:, None], 2, axis=1), RATE)
    return original, music


def test_ducking_fades_loop_and_content_scope(tmp_path):
    original, music = signals(tmp_path)
    config = selection({'enabled': True, 'asset_id': 'ambient_pad', 'level_db': -12, 'fade_seconds': .2})
    meta = write_mix(original, music, tmp_path, tmp_path/'mixed.wav', tmp_path/'bed.wav', config,
                     samples=7*RATE, content_start=RATE, content_end=6*RATE)
    bed, _ = sf.read(tmp_path/'bed.wav')
    mixed, _ = sf.read(tmp_path/'mixed.wav')
    assert np.max(np.abs(bed[:RATE])) == 0 and np.max(np.abs(bed[6*RATE:])) == 0
    assert np.max(np.abs(bed[-RATE-1])) == 0
    assert np.max(np.abs(bed[5*RATE:6*RATE])) > .001  # Actually loops beyond the source length.
    assert meta['speech_average_envelope'] < meta['gap_average_envelope']
    assert meta['speech_samples'] > RATE and meta['gap_samples'] >= RATE
    assert np.max(np.abs(mixed)) <= .90001
    config['loop'] = False
    write_mix(original, music, tmp_path, tmp_path/'once.wav', tmp_path/'once-bed.wav', config,
              samples=7*RATE, content_start=RATE, content_end=6*RATE)
    once, _ = sf.read(tmp_path/'once-bed.wav')
    assert np.max(np.abs(once[round(3.3*RATE):])) == 0


@pytest.mark.parametrize('enabled,packaged', [(True, True), (True, False), (False, True)])
def test_real_mp4_mix_keeps_pixels_voice_and_original(tmp_path, enabled, packaged):
    original, music = signals(tmp_path)
    artifacts = tmp_path/'artifacts'; artifacts.mkdir()
    input_dir = tmp_path/'input'; input_dir.mkdir(); (input_dir/'music').mkdir()
    (input_dir/'music/asset.wav').write_bytes(music.read_bytes())
    config = selection({'enabled': enabled, 'asset_id': 'ambient_pad', 'fade_seconds': .2})
    saved = {'background_music': config, 'background_music_resolved': record(config,
        {'id': 'ambient_pad', 'sha256': hashlib.sha256(music.read_bytes()).hexdigest()} if enabled else None)}
    source = artifacts/'original.mp4'
    ffmpeg, ffprobe = ROOT/'g0/darwin_arm64/ffmpeg', ROOT/'g0/darwin_arm64/ffprobe'
    subprocess.run(list(map(str, [ffmpeg, '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
        'color=c=green:s=320x180:r=30:d=7', '-i', original, '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-t', 7, source])), check=True, capture_output=True)
    if packaged:
        (artifacts/'package').mkdir(); (artifacts/'package/video.mp4').write_bytes(source.read_bytes())
        (artifacts/'package/package.json').write_text(json.dumps({'intro': {'frames': 30}, 'content': {'frames': 150}}))
        saved['package'] = {'enabled': True}
    before = source.read_bytes()
    def runner(label, args, cwd, timeout=900):
        return subprocess.check_output(list(map(str,args)), cwd=cwd, timeout=timeout, stderr=subprocess.PIPE, text=True)
    p = SimpleNamespace(PROJECT_INPUT=input_dir/'source.txt', ARTIFACTS=artifacts, VIDEO_PATH=source,
        PROJECT=ROOT, FFMPEG=ffmpeg, FFPROBE=ffprobe, _run_fixed_command=runner,
        digest=lambda path: hashlib.sha256(path.read_bytes()).hexdigest(),
        write_json=lambda path, value: path.write_text(json.dumps(value)))
    report = run(p, saved, force=True)
    assert report['status'] == 'PASS' and report['full_decode'] == 'PASS'
    assert report['video_frames'] == 210 and report['checks']['unchanged_video_stream']
    assert source.read_bytes() == before
    if enabled:
        assert report['checks']['recovered_narration_correlation'] > .98
        assert report['checks']['speech_music_margin_db'] >= 10
        assert report['checks']['music_outside_content_peak'] == 0
    else:
        assert (artifacts/'mix/video.mp4').read_bytes() == before
    (input_dir/'music/asset.wav').write_bytes(b'corrupted')
    if enabled:
        with pytest.raises(MusicError):
            frozen_input(input_dir, saved)

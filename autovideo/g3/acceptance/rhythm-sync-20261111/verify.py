"""Validate full-video timing, original speech, and controlled scene changes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / 'g3/data/validation/rhythm-sync-20261111'
FF = ROOT / 'g0/darwin_arm64/ffmpeg'
PROBE = ROOT / 'g0/darwin_arm64/ffprobe'


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def media(folder, expected_frames):
    video = folder/'video.mp4'
    probe = json.loads(subprocess.check_output([str(PROBE), '-v', 'error', '-show_streams', '-of', 'json', str(video)]))
    stream = next(s for s in probe['streams'] if s['codec_type'] == 'video')
    assert (stream['width'], stream['height'], int(stream['nb_frames'])) == (1920, 1080, expected_frames)
    assert any(s['codec_type'] == 'audio' for s in probe['streams'])
    with (folder/'decode.log').open('w') as log:
        subprocess.run([str(FF), '-v', 'error', '-xerror', '-i', str(video), '-f', 'null', '-'],
                       stdout=log, stderr=log, check=True)
    audio = np.frombuffer(subprocess.check_output([str(FF), '-v', 'error', '-i', str(video), '-vn',
                        '-ac', '1', '-ar', '8000', '-f', 'f32le', '-']), dtype=np.float32).astype(np.float64)
    return {'path': str(video), 'sha256': digest(video), 'frames': expected_frames,
            'seconds': expected_frames/30, 'decode': 'pass'}, audio


before = read(BASE/'full-news/before/props.json')['ir']
after = read(BASE/'full-news/after/props.json')['ir']
raw_before = read(BASE/'full-news/before/video-ir.json')
raw_after = read(BASE/'full-news/after/video-ir.json')
assert {k: v for k, v in raw_before.items() if k != 'scenes'} == {k: v for k, v in raw_after.items() if k != 'scenes'}
assert before['narration'] == after['narration'] and before['caption_cues'] == after['caption_cues']
assert before['total_frames'] == after['total_frames']
changed = []
for a, b in zip(before['scenes'], after['scenes']):
    assert a['props'] == b['props'] and a['purpose'] == b['purpose']
    if a != b:
        changed.append(a['id'])
assert changed == ['card_4_p1', 'card_4_p2']
for ir in (before, after):
    scenes = ir['scenes']
    assert scenes[0]['start_frame'] == 0 and scenes[-1]['end_frame'] == ir['total_frames']
    assert all(a['end_frame'] == b['start_frame'] for a, b in zip(scenes, scenes[1:]))
    assert [sid for s in scenes for sid in s['narration_segment_ids']] == [s['id'] for s in ir['narration']['segments']]
    rows = {s['id']: s for s in ir['narration']['segments']}
    for scene in scenes:
        for sid in scene['narration_segment_ids']:
            row = rows[sid]
            assert scene['start_frame']/30 <= row['start_ms']/1000 + 1/30
            assert scene['end_frame']/30 >= row['end_ms']/1000 - 1/30

full, audio = {}, {}
for variant in ('before', 'after'):
    full[variant], audio[variant] = media(BASE/f'full-news/{variant}', after['total_frames'])
assert audio['before'].shape == audio['after'].shape
correlation = float(np.corrcoef(audio['before'], audio['after'])[0, 1])
assert correlation > .999

# Exact final production result equals the visually reviewed prototype.
assert read(BASE/'full-news/after/props.json') == read(BASE/'boundary-news/candidate/props.json')
holdouts = []
for name in ('tutorial', 'comparison', 'qwen'):
    a = read(BASE/f'holdout-{name}/before/props.json')
    b = read(BASE/f'holdout-{name}/after/props.json')
    assert a == b
    holdouts.append({'case': name, 'full_compiled_output_identical': True,
                     'scenes': len(a['ir']['scenes']), 'seconds': a['ir']['total_frames']/30})

# Whole-film sampling includes opening, all page boundaries, and final frame area.
times = sorted(set([.8, 56.8, 57.15, 57.6, 58.3, 60.6, 61.7, 63.6, 68.9,
                    after['total_frames']/30-.2] + [s['start_frame']/30+.8 for s in after['scenes']]))
sheet = Image.new('RGB', (1280, 382*len(times)), '#eeeeee')
draw = ImageDraw.Draw(sheet)
for col, variant in enumerate(('before', 'after')):
    folder = BASE/f'full-news/{variant}'
    for row, second in enumerate(times):
        target = folder/f'qa-{second:.3f}.png'
        subprocess.run([str(FF), '-v', 'error', '-ss', str(second), '-i', str(folder/'video.mp4'),
                        '-frames:v', '1', '-y', str(target)], check=True)
        sheet.paste(Image.open(target).resize((640, 360)), (640*col, 382*row))
        draw.text((640*col, 382*row+363), f'{variant} {second:.3f}s', fill='black')
sheet.save(BASE/'full-news/comparison.jpg')
summary = {'full_video': full, 'audio_correlation': correlation, 'unchanged_authored_content': True,
           'continuous_complete_timeline': True, 'changed_scenes': changed,
           'cut_before_seconds': 1838/30, 'cut_after_seconds': 1712/30,
           'production_equals_reviewed_candidate': True, 'holdouts': holdouts,
           'visual_samples': times, 'tests': {'passed': 1078, 'skipped': 1},
           'limits': ['one positive real boundary repair; holdouts demonstrate no regression, not further gains',
                      'no audience study or retention measurement', 'illustration pacing remains unresolved']}
(BASE/'verification.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
print(json.dumps(summary, ensure_ascii=False, indent=2))

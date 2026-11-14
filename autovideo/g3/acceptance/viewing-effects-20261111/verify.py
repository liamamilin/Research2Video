"""Record media checks and timeline images for the retained local change."""
from __future__ import annotations

import hashlib
import argparse
import json
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / 'g3/data/validation/viewing-effects-20261111'
FF = ROOT / 'g0/darwin_arm64/ffmpeg'
PROBE = ROOT / 'g0/darwin_arm64/ffprobe'
CASES = [('news', 'card_2_p2'), ('decrease', 'card_2'), ('rates', 'card_38')]
parser = argparse.ArgumentParser()
parser.add_argument('--final', action='store_true')
args = parser.parse_args()
report = []
records = json.loads((BASE / 'baseline.json').read_text())
for name, scene_id in CASES:
    folder = BASE / (f'final-{name}-{scene_id}' if args.final else f'{name}-{scene_id}-readable-video')
    record = next(r for r in records if r['name'] == name)
    original = json.loads(Path(record['props']).read_text())
    props = json.loads((folder / ('after-props.json' if args.final else 'props.json')).read_text())
    if args.final:
        assert json.loads((folder / 'before-props.json').read_text()) == original
        assert props['ir']['narration'] == original['ir']['narration']
        assert len(props['ir']['scenes']) == len(original['ir']['scenes'])
        for before, after in zip(original['ir']['scenes'], props['ir']['scenes']):
            assert {k: v for k, v in before.items() if k != 'beats'} == {k: v for k, v in after.items() if k != 'beats'}
    else:
        assert props == original, 'A readability change must leave the entire compiled story unchanged.'
    scene = next(s for s in props['ir']['scenes'] if s['id'] == scene_id)
    expected = (scene['end_frame'] - scene['start_frame']) / 30
    streams = {}
    sounds = {}
    for variant in ('before', 'after'):
        video = folder / f'{variant}.mp4'
        streams[variant] = json.loads(subprocess.check_output([
            str(PROBE), '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(video)]))
        assert {s['codec_type'] for s in streams[variant]['streams']} >= {'video', 'audio'}
        picture = next(s for s in streams[variant]['streams'] if s['codec_type'] == 'video')
        assert (picture['width'], picture['height']) == (1920, 1080)
        assert abs(float(picture['duration']) - expected) <= 1 / 30 + .001
        with (folder / f'{variant}-decode.log').open('w') as log:
            subprocess.run([str(FF), '-v', 'error', '-xerror', '-i', str(video), '-f', 'null', '-'],
                           stdout=log, stderr=log, check=True)
        sounds[variant] = np.frombuffer(subprocess.check_output([
            str(FF), '-v', 'error', '-i', str(video), '-vn', '-ac', '1', '-ar', '8000',
            '-f', 'f32le', '-']), dtype=np.float32).astype(np.float64)
    size = min(map(len, sounds.values()))
    a, b = sounds['before'][:size], sounds['after'][:size]
    correlation = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
    assert correlation > .98, 'The two clips should carry the same timed narration.'
    # All elapsed seconds, plus a frame after the opening transition. Preserve
    # the full 1080p PNGs and make several readable six-pair contact sheets.
    times = sorted(set([.8, *range(1, int(expected)), round(expected - .2, 3)]))
    sheets = []
    for first in range(0, len(times), 6):
        selected = times[first:first + 6]
        sheet = Image.new('RGB', (1280, len(selected) * 382), '#eeeeee')
        draw = ImageDraw.Draw(sheet)
        for row, second in enumerate(selected):
            for col, variant in enumerate(('before', 'after')):
                frame = folder / f'qa-{variant}-{second}.png'
                subprocess.run([str(FF), '-v', 'error', '-y', '-ss', str(second), '-i',
                                str(folder / f'{variant}.mp4'), '-frames:v', '1', str(frame)], check=True)
                picture = Image.open(frame)
                picture.thumbnail((640, 360))
                sheet.paste(picture, (col * 640, row * 382))
                draw.text((col * 640 + 8, row * 382 + 364), f'{variant} {second}s', fill='#222222')
        path = folder / f'timeline-{first // 6 + 1}.jpg'
        sheet.save(path, quality=90)
        sheets.append(str(path))
    baseline = (BASE / 'baseline-renderer/src/Root.tsx').read_text()
    rendered = (folder / ('after-renderer/src/Root.tsx' if args.final else 'renderer/src/Root.tsx')).read_text()
    production = (ROOT / 'g1/renderer/src/Root.tsx').read_text()
    comment = '                  {/* Establish a readable comparison immediately; speech strengthens emphasis. */}\n'
    assert rendered.replace(comment, '') == baseline.replace('.18+.82*', '.65+.35*')
    assert production.replace(comment, '') == rendered.replace(comment, '')
    if args.final:
        assert (folder / 'before-renderer/src/Root.tsx').read_text() == baseline
    report.append({'case': name, 'scene': scene_id, 'seconds': expected,
        'story_and_scene_props_identical': True, 'beat_timing_may_change': args.final,
        'audio_correlation': correlation, 'decode': 'pass',
        'rendered_code_matches_production_except_comment': True,
        'source_sha256': hashlib.sha256(Path(record['props']).read_bytes()).hexdigest(),
        'streams': streams, 'timeline_sheets': sheets})
(BASE / ('final-verification.json' if args.final else 'verification.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps([{k: r[k] for k in ('case', 'seconds', 'audio_correlation', 'decode')} for r in report], indent=2))

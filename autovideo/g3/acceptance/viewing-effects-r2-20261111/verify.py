"""Check controlled media, untouched story inputs, and visible-content framing."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / 'g3/data/validation/viewing-effects-r2-20261111'
sys.path.insert(0, str(ROOT))
from g1.video_ir import validate_video_ir

CASES = [('tutorial', 'card_3'), ('comparison', 'card_3'), ('comparison', 'card_11'), ('recorder', 'card_2')]
FF = ROOT / 'g0/darwin_arm64/ffmpeg'
PROBE = ROOT / 'g0/darwin_arm64/ffprobe'
report = []
for name, scene_id in CASES:
    folder = BASE / f'final-{name}-{scene_id}'
    original = json.loads((folder / 'before/props.json').read_text())
    updated = json.loads((folder / 'after/props.json').read_text())
    record = json.loads((folder / 'after/source.json').read_text())
    fixtures = Path(record['props']).parents[1] / 'fixtures'
    raw = json.loads((fixtures / 'video-ir-v0.2.json').read_text())
    compiled = validate_video_ir(raw, fixtures)
    assert {**original, 'ir': {**original['ir'], **compiled}} == updated
    assert updated['ir']['narration'] == original['ir']['narration']
    assert updated['ir']['caption_cues'] == original['ir']['caption_cues']
    for old, new in zip(original['ir']['scenes'], updated['ir']['scenes']):
        assert {k: v for k, v in new.items() if k != 'image_reading'} == old
    scene = next(s for s in updated['ir']['scenes'] if s['id'] == scene_id)
    expected_frames = scene['end_frame'] - scene['start_frame']
    seconds = expected_frames / 30
    image_checks = []
    for image, layout in zip(scene['props']['images'], scene['image_reading']):
        path = fixtures / image['image_path']
        asset = next(a for a in raw['assets'] if a['id'] == image['image_asset_id'])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == asset['sha256']
        rgba = Image.open(path).convert('RGBA')
        picture = Image.alpha_composite(Image.new('RGBA', rgba.size, 'white'), rgba).convert('RGB')
        content = ImageChops.difference(picture, Image.new('RGB', picture.size, 'white')).getbbox()
        x1, y1, x2, y2 = layout['box']
        assert content is None or (x1 <= content[0] and y1 <= content[1] and x2 >= content[2] and y2 >= content[3])
        image_checks.append({'path': image['image_path'], 'sha256': asset['sha256'],
                             'box': layout['box'], 'all_visible_content_inside': True})
    audio, clips = {}, {}
    times = [.8, round(seconds * .25, 3), round(seconds * .5, 3), round(seconds * .75, 3), round(seconds - .2, 3)]
    sheet = Image.new('RGB', (1280, 382 * len(times)), '#eeeeee')
    draw = ImageDraw.Draw(sheet)
    for col, variant in enumerate(('before', 'after')):
        video = folder / variant / 'video.mp4'
        media = json.loads(subprocess.check_output([str(PROBE), '-v', 'error', '-show_streams', '-of', 'json', str(video)]))
        stream = next(s for s in media['streams'] if s['codec_type'] == 'video')
        assert (stream['width'], stream['height'], int(stream['nb_frames'])) == (1920, 1080, expected_frames)
        assert any(s['codec_type'] == 'audio' for s in media['streams'])
        with (folder / variant / 'decode.log').open('w') as log:
            subprocess.run([str(FF), '-v', 'error', '-xerror', '-i', str(video), '-f', 'null', '-'], stdout=log, stderr=log, check=True)
        audio[variant] = np.frombuffer(subprocess.check_output([str(FF), '-v', 'error', '-i', str(video), '-vn',
            '-ac', '1', '-ar', '8000', '-f', 'f32le', '-']), dtype=np.float32).astype(np.float64)
        clips[variant] = {'path': str(video), 'sha256': hashlib.sha256(video.read_bytes()).hexdigest()}
        for row, second in enumerate(times):
            target = folder / variant / f'timeline-{row}.png'
            subprocess.run([str(FF), '-v', 'error', '-y', '-ss', str(second), '-i', str(video), '-frames:v', '1', str(target)], check=True)
            image = Image.open(target)
            image.thumbnail((640, 360))
            sheet.paste(image, (col * 640, row * 382))
            draw.text((col * 640 + 8, row * 382 + 364), f'{variant} {second}s', fill='#222222')
    a, b = audio.values()
    assert len(a) == len(b)
    correlation = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
    assert correlation > .999
    sheet.save(folder / 'comparison.jpg', quality=92)
    report.append({'case': name, 'scene': scene_id, 'seconds': seconds, 'clips': clips,
        'normal_compiler_output_matches': True, 'authored_story_and_captions_unchanged': True,
        'audio_correlation': correlation, 'decode': 'pass', 'image_checks': image_checks,
        'comparison': str(folder / 'comparison.jpg')})
hybrid = BASE / 'final-hybrid-tutorial-card_3/after'
with (hybrid / 'decode.log').open('w') as log:
    subprocess.run([str(FF), '-v', 'error', '-xerror', '-i', str(hybrid / 'video.mp4'), '-f', 'null', '-'], stdout=log, stderr=log, check=True)
result = {'cases': report, 'hybrid_picture_only': {'path': str(hybrid / 'video.mp4'), 'decode': 'pass',
    'scope': 'native picture renderer and slot framing; voice/captions mixed by the existing downstream stage'},
    'not_claimed': ['viewer study', 'retention growth', 'all screenshot text readable']}
(BASE / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
print(json.dumps([{k: r[k] for k in ('case', 'scene', 'seconds', 'audio_correlation', 'decode')} for r in report], indent=2))

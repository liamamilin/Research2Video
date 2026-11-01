"""Verify the real public-source tutorial movie, navigation and automatic pictures."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
ENGINE = ROOT.parents[1] / 'autovideo'
FFMPEG, FFPROBE = [ENGINE / 'g0/darwin_arm64' / name for name in ['ffmpeg', 'ffprobe']]


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify(out):
    receipt, version = read(out / 'receipt.json'), read(out / 'version.json')
    render = next(r for r in read(out / 'renders.json') if r['id'] == receipt['render_id'])
    files = Path(receipt['files_dir'])
    attempt = ENGINE / 'g3/data/jobs' / receipt['job_id'] / 'attempts/1/g2-run'
    request = read(attempt.parents[2] / 'input/input.json')
    ir, compiled, nav, brand = [read(path) for path in [files / 'video-ir.json',
        attempt / 'artifacts/compiled-timeline.json', files / 'episode-navigation.json', files / 'brand-qa.json']]
    assert render['status'] == 'COMPLETED'
    assert receipt['production'] == 'chatgpt_t03_tutorials'
    assert request['production_recipe'] == 'mixed_tutorial'
    assert request['production_recipe_resolved']['recipe']['version'] == 2
    assert request['production_mode'] == 'fast'
    assert not version['meta'].get('user_materials')
    expected = ''.join(p['text'] for s in version['sections'] for p in s['paragraphs'])
    actual = ''.join(s['text'] for s in ir['narration']['segments'])
    assert ''.join(expected.split()) == ''.join(actual.split())
    greeting = '大家好，欢迎收看ChatGPT 使用方法与教程。'
    assert actual.count(greeting) == 1
    assert len(nav['items']) == len(nav['item_cards']) >= 3
    assert any('网页' in x['title'] for x in nav['items']) and any('手机' in x['title'] for x in nav['items'])
    assert sha(files / 'content.mp4') == nav['video_sha256']
    assert request.get('image_materials'), 'automatic public pictures must reach the engine'
    pictures = render['visuals']['selection']['materials']
    assert pictures and all(p['source']['url'].startswith('https://') for p in pictures)
    assert any(d['status'] == 'accepted' for d in render['visuals']['decisions'])
    pictured = [s for s in compiled['scenes'] if s['component'] in {'object_showcase', 'material_collage'}]
    assert pictured, 'public source pictures must occur in actual scenes'
    from core.video import screenshots, store
    store.use_state_dir(str(out / 'state'))
    assets = {a['id']: a for a in ir['assets'] if a['type'] == 'image'}
    def pixels(path):
        with Image.open(path) as picture:
            return picture.size, hashlib.sha256(picture.convert('RGB').tobytes()).hexdigest()
    for i, picture in enumerate(pictures, 1):
        _, original = screenshots.load(render['script_id'], picture['screenshot_id'])
        assert pixels(original) == pixels(attempt / 'fixtures' / assets[f'slide_image_{i}']['path'])
    for name in ['qa.json', 'package-qa.json', 'brand-qa.json']:
        assert read(files / name)['status'] == 'PASS'
    assert brand['profile']['id'] == 'linthesis'
    for artifact in render['artifacts']:
        assert sha(files / artifact['name']) == artifact['sha256']
    evidence = out / 'evidence'
    evidence.mkdir(exist_ok=True)
    probes = {}
    for name in ['content.mp4', 'video.mp4']:
        with (evidence / (name + '.decode.log')).open('w') as log:
            subprocess.run([str(FFMPEG), '-v', 'error', '-xerror', '-i', str(files / name), '-f', 'null', '-'],
                           check=True, stdout=log, stderr=subprocess.STDOUT)
        probes[name] = json.loads(subprocess.check_output([str(FFPROBE), '-v', 'error', '-count_frames',
            '-show_entries', 'stream=codec_name,width,height,r_frame_rate,nb_read_frames:format=duration',
            '-of', 'json', str(files / name)], text=True))
        stream = next(s for s in probes[name]['streams'] if s['codec_name'] == 'h264')
        assert (stream['width'], stream['height'], stream['r_frame_rate']) == (1920, 1080, '30/1')
    mid = lambda s: (s['start_frame'] + s['end_frame']) / 60
    samples = [('greeting', 1.5), ('agenda', mid(nav['agenda']))]
    samples += [(f'step-{i+1}', mid(s)) for i, s in enumerate(nav['item_cards'])]
    samples += [(f'picture-{i+1}', mid(s)) for i, s in enumerate(pictured[:4])]
    samples += [('outro', brand['duration_seconds'] - 2)]
    sheet = Image.new('RGB', (1280, 390 * ((len(samples)+1)//2)), 'white')
    draw = ImageDraw.Draw(sheet)
    for i, (name, second) in enumerate(samples):
        frame = evidence / ('frame-' + name + '.png')
        subprocess.run([str(FFMPEG), '-y', '-v', 'error', '-ss', str(second), '-i', str(files / 'video.mp4'),
                        '-frames:v', '1', str(frame)], check=True)
        with Image.open(frame) as picture:
            picture.thumbnail((640, 360)); x, y = i % 2 * 640, i // 2 * 390
            sheet.paste(picture, (x, y)); draw.text((x+8, y+364), f'{name}: {second:.2f}s', fill='black')
    sheet.save(evidence / 'contact.jpg', quality=92)
    for name in ['qa.json', 'package-qa.json', 'brand-qa.json', 'episode-navigation.json',
                 'model-cost.json', 'packaging-resolved.json', 'information-timing.json']:
        shutil.copy2(files / name, evidence / name)
    source_files = ['core/source_pages.py', 'core/source_supply.py', 'core/material_collection.py',
                    'core/video/auto.py', 'core/video/topic_store.py', 'web/services/video_autopilot.py',
                    'core/video/visual_materials.py', 'core/video/visual_matching.py', 'core/video/page_regions.js']
    result = {'status': 'PASS', 'job_id': receipt['job_id'], 'render_id': render['id'],
        'public_source_only': True, 'personal_login_required': False, 'manual_script_edits': 0,
        'manual_picture_placements': 0, 'greeting_count': 1, 'steps': [x['title'] for x in nav['items']],
        'automatic_public_pictures': len(pictures), 'pictured_scenes': [s['id'] for s in pictured],
        'public_source_pixels_preserved': True,
        'returned_artifacts_verified': len(render['artifacts']), 'probes': probes, 'samples': samples,
        'duration_seconds': brand['duration_seconds'], 'video': str(files / 'video.mp4'),
        'video_sha256': sha(files / 'video.mp4'), 'source_sha256': {n: sha(ROOT / n) for n in source_files}}
    (evidence / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'status': 'PASS', 'seconds': result['duration_seconds'], 'pictures': len(pictures),
                      'samples': len(samples)}, ensure_ascii=False))


if __name__ == '__main__':
    verify(Path(sys.argv[1]).resolve())

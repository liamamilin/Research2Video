"""Verify real input → code → output → comparison in the returned movie."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[3]
ENGINE = ROOT.parents[1] / 'autovideo'
FFMPEG = ENGINE / 'g0/darwin_arm64/ffmpeg'
FFPROBE = ENGINE / 'g0/darwin_arm64/ffprobe'


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pixels(path):
    with Image.open(path) as picture:
        return picture.size, hashlib.sha256(picture.convert('RGB').tobytes()).hexdigest()


def verify(out: Path):
    receipt, version, prepared, batch = [read(out / name) for name in
        ['receipt.json', 'version.json', 'preparation.json', 'batch.json']]
    render = next(r for r in read(out / 'renders.json') if r['id'] == receipt['render_id'])
    files = Path(receipt['files_dir'])
    attempt = ENGINE / 'g3/data/jobs' / receipt['job_id'] / 'attempts/1/g2-run'
    saved = read(attempt.parents[2] / 'input/input.json')
    ir, compiled, navigation, brand = [read(path) for path in [files / 'video-ir.json',
        attempt / 'artifacts/compiled-timeline.json', files / 'episode-navigation.json', files / 'brand-qa.json']]
    assert batch['status'] == 'DONE' and batch['items'][0]['state'] == 'DONE'
    assert batch['items'][0]['gate']['approved'] == 'auto' and render['status'] == 'COMPLETED'
    for artifact in render['artifacts']:
        assert sha(files / artifact['name']) == artifact['sha256']
    assert saved['production_recipe'] == 'mixed_tutorial' and saved['production_mode'] == 'fast'
    assert saved['production_recipe_resolved']['recipe']['version'] == 2
    assert version['meta']['user_materials']['ids'] == prepared['material_context']['ids']
    assert len(saved['image_materials']) == len(render['visuals']['decisions']) == 2
    assert all(d['status'] == 'accepted' for d in render['visuals']['decisions'])
    assert all(len(m['focus']['regions']) == 2 for m in saved['image_materials'])
    expected = ''.join(p['text'] for s in version['sections'] for p in s['paragraphs'])
    actual = ''.join(s['text'] for s in ir['narration']['segments'])
    assert ''.join(expected.split()) == ''.join(actual.split())
    assert actual.count('大家好，欢迎收看AI 开源发现。') == 1
    assert len(navigation['items']) == len(navigation['item_cards']) == 3
    assert sha(files / 'content.mp4') == navigation['video_sha256']
    script = (attempt / 'artifacts/episode-navigation/navigation.ass').read_text()
    assert all(word in script for word in ['操作步骤', '下一步', '步骤回顾'])
    codes = [s for s in compiled['scenes'] if s['component'] == 'code_demo']
    text = '\n'.join(line['text'] for s in codes for line in s['props']['lines'])
    commands = prepared.get('displayed_commands', ['pip install markitdown', 'markitdown weekly.html -o weekly.md'])
    assert all(command in text for command in commands)
    pictured = [s for s in compiled['scenes'] if s['component'] in {'object_showcase', 'material_collage'}]
    input_id, output_id = 'slide_image_1', 'slide_image_2'
    ids = lambda s: {image['image_asset_id'] for image in s['props']['images']}
    inputs = [s for s in pictured if ids(s) == {input_id}]
    outputs = [s for s in pictured if ids(s) == {output_id}]
    pairs = [s for s in pictured if ids(s) == {input_id, output_id}]
    assert inputs and outputs and pairs, 'actual input, actual output and their comparison must all appear'
    assert inputs[0]['start_frame'] < codes[0]['start_frame'] < outputs[0]['start_frame'] <= pairs[0]['start_frame']
    has_regions = lambda s: any(target.startswith('region:') for cue in s['props'].get('shot_plan', {}).get('cues', [])
                               for target in cue['targets'])
    assert any(has_regions(s) for s in inputs) and any(has_regions(s) for s in outputs)
    assets = {a['id']: a for a in ir['assets'] if a['type'] == 'image'}
    for asset_id, image, name in zip([input_id, output_id], prepared['image_records'], ['input', 'output']):
        asset = attempt / 'fixtures' / assets[asset_id]['path']
        source = out / 'state/user_materials' / image['id'] / 'image.png'
        assert pixels(asset) == pixels(source), 'the rendered asset must contain the original screenshot pixels'
        assert pixels(source) == pixels(out / 'actual-demo' / (name + '.png'))
    assert read(files / 'qa.json')['status'] == read(files / 'package-qa.json')['status'] == brand['status'] == 'PASS'
    assert brand['profile']['id'] == 'linthesis'
    assert sha(attempt / 'renderer/src/CodeDemo.tsx') == sha(ENGINE / 'g1/renderer/src/CodeDemo.tsx')
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
        video = next(s for s in probes[name]['streams'] if s['codec_name'] == 'h264')
        assert (video['width'], video['height'], video['r_frame_rate']) == (1920, 1080, '30/1')
    mid = lambda s: (s['start_frame'] + s['end_frame']) / 60
    samples = [('greeting', 1.5), ('agenda', mid(navigation['agenda']))]
    samples.extend((f'step-{i+1}', mid(s)) for i, s in enumerate(navigation['item_cards']))
    samples.extend([('input', mid(inputs[0])), ('code', mid(codes[-1])),
                    ('output', mid(outputs[0])), ('comparison', mid(pairs[0])),
                    ('outro', brand['duration_seconds'] - 2)])
    for name, scenes in [('input', inputs), ('output', outputs)]:
        seen = set()
        for scene in scenes:
            beats = {b['id']: b['frame'] for b in scene['beats']}
            for cue in scene['props'].get('shot_plan', {}).get('cues', []):
                targets = [target for target in cue['targets'] if target.startswith('region:') and target not in seen]
                if targets and len(seen) < 2:
                    seen.update(targets)
                    samples.append((f'{name}-focus-{len(seen)}', (beats[cue['beat_id']] + 15)/30))
    sheet = Image.new('RGB', (1280, 390*((len(samples)+1)//2)), 'white')
    draw = ImageDraw.Draw(sheet)
    for i, (name, second) in enumerate(samples):
        frame = evidence / f'frame-{name}.png'
        subprocess.run([str(FFMPEG), '-y', '-v', 'error', '-ss', str(second), '-i',
                        str(files / 'video.mp4'), '-frames:v', '1', str(frame)], check=True)
        with Image.open(frame) as picture:
            picture.thumbnail((640, 360))
            x, y = i % 2 * 640, i // 2 * 390
            sheet.paste(picture, (x, y))
            draw.text((x+8, y+364), f'{name}: {second:.2f}s', fill='black')
    sheet.save(evidence / 'contact.jpg', quality=92)
    for name in ['qa.json', 'package-qa.json', 'brand-qa.json', 'episode-navigation.json',
                 'model-cost.json', 'packaging-resolved.json', 'information-timing.json']:
        shutil.copy2(files / name, evidence / name)
    source_files = ['core/user_materials.py', 'core/video/writer.py', 'core/video/episodes.py',
                    'web/services/video_workflow.py', 'web/services/assistant_materials.py']
    result = {'status': 'PASS', 'run_id': receipt['run_id'], 'job_id': receipt['job_id'],
        'automatic_report_write_approve_register_match_render_download': True,
        'manual_paragraph_assignments': 0, 'manual_storyboard_edits': 0,
        'greeting_count': 1, 'steps': [i['title'] for i in navigation['items']],
        'input_scenes': [s['id'] for s in inputs], 'output_scenes': [s['id'] for s in outputs],
        'comparison_scenes': [s['id'] for s in pairs], 'code_scenes': [s['id'] for s in codes],
        'actual_input_code_output_order': True, 'original_screenshot_pixels_preserved': True,
        'registered_focus_regions': 4, 'returned_artifacts_verified': len(render['artifacts']),
        'rendered_input_and_output_region_cues': True,
        'writer_requests': version['meta']['usage']['requests'], 'model_calls': read(files / 'qa.json')['llm_call_count'],
        'probes': probes, 'brand_profile': brand['profile']['id'], 'sample_times': samples,
        'video': str(files / 'video.mp4'), 'video_sha256': sha(files / 'video.mp4'),
        'source_sha256': {name: sha(ROOT / name) for name in source_files}}
    (evidence / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': 'PASS', 'seconds': brand['duration_seconds'], 'samples': len(samples)}))


if __name__ == '__main__':
    verify(Path(sys.argv[1]).resolve())

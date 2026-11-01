"""Check the returned real MP4 and sample automatically applied tutorial effects."""
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


def read(path: Path):
    return json.loads(path.read_text())


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(out: Path) -> None:
    receipt, batch, render, version = (read(out / name) for name in
        ['receipt.json', 'batch.json', 'renders.json', 'version.json'])
    render = next(r for r in render if r['id'] == receipt['render_id'])
    assert batch['status'] == 'DONE' and batch['items'][0]['state'] == 'DONE'
    assert batch['items'][0]['gate']['approved'] == 'auto'
    assert render['status'] == 'COMPLETED'
    returned = Path(receipt['files_dir'])
    for artifact in render['artifacts']:
        assert sha(returned / artifact['name']) == artifact['sha256'], artifact['name']
    job = ENGINE / 'g3/data/jobs' / receipt['job_id']
    attempt = max((job / 'attempts').iterdir(), key=lambda p: int(p.name)) / 'g2-run'
    saved, ir, compiled, package, navigation = (read(p) for p in [job / 'input/input.json',
        returned / 'video-ir.json', attempt / 'artifacts/compiled-timeline.json',
        attempt / 'artifacts/package/package.json', attempt / 'artifacts/episode-navigation.json'])
    assert saved['production_recipe'] == 'mixed_tutorial'
    assert saved['production_mode'] == 'fast' and saved['video_form'] == 'information_cards'
    assert saved['visual_style'] == 'editorial_light' and saved['video_speed'] == 1.25
    assert saved['production_recipe_resolved']['recipe']['version'] == 2
    q = render['submission_request']
    assert q['production_recipe'] == 'mixed_tutorial' and 'video_form' not in q
    assert q['image_materials'] and render['visuals']['state'] == 'completed'
    assert any(m.get('focus', {}).get('regions') for m in q['image_materials'])
    greeting = '大家好，欢迎收看AI 开源发现。'
    expected = ''.join(p['text'] for section in version['sections'] for p in section['paragraphs'])
    spoken = ''.join(s['text'] for s in ir['narration']['segments'])
    assert ''.join(expected.split()) == ''.join(spoken.split())
    assert spoken.count(greeting) == expected.count(greeting) == 1
    assert '感谢收看' in spoken
    assert [i['title'] for i in navigation['items']] == [i['title'] for i in saved['episode']['items']]
    assert len(navigation['item_cards']) == len(navigation['items']) == 3
    ass = (attempt / 'artifacts/episode-navigation/navigation.ass').read_text()
    assert all(label in ass for label in ['操作步骤', '下一步', '步骤回顾'])
    code = [s for s in compiled['scenes'] if s['component'] == 'code_demo']
    displayed_code = '\n'.join(line['text'] for scene in code for line in scene['props']['lines'])
    assert 'pip install markitdown' in displayed_code
    assert 'markitdown weekly.html -o weekly.md' in displayed_code
    pictured = [s for s in compiled['scenes'] if s['component'] in {'object_showcase', 'material_collage'}]
    packaging = read(returned / 'packaging-resolved.json')
    brand = read(returned / 'brand-qa.json')
    assert package['status'] == 'PASS' and packaging['components']['label'] == 'AI 开源发现'
    assert brand['status'] == 'PASS' and brand['profile']['id'] == 'linthesis'
    assert read(returned / 'qa.json')['status'] == 'PASS'
    assert navigation['video_sha256'] == sha(returned / 'content.mp4')
    renderer = ENGINE / 'g1/renderer/src/CodeDemo.tsx'
    assert sha(attempt / 'renderer/src/CodeDemo.tsx') == sha(renderer)
    evidence = out / 'evidence'
    evidence.mkdir(exist_ok=True)
    probes = {}
    for name in ['video.mp4', 'content.mp4']:
        path = returned / name
        with (evidence / (name + '.decode.log')).open('w') as log:
            subprocess.run([str(FFMPEG), '-v', 'error', '-xerror', '-i', str(path), '-f', 'null', '-'],
                           check=True, stdout=log, stderr=subprocess.STDOUT)
        probes[name] = json.loads(subprocess.check_output([str(FFPROBE), '-v', 'error', '-count_frames',
            '-show_entries', 'stream=codec_name,width,height,r_frame_rate,nb_read_frames:format=duration',
            '-of', 'json', str(path)], text=True))
        video = next(s for s in probes[name]['streams'] if s['codec_name'] == 'h264')
        assert (video['width'], video['height'], video['r_frame_rate']) == (1920, 1080, '30/1')
    offset = package['content']['offset_ms'] / 1000
    samples = [('brand-opening', .8), ('greeting', offset + 1.5)]
    agenda = navigation.get('agenda')
    if agenda:
        samples.append(('agenda', offset + (agenda['start_frame'] + agenda['end_frame']) / 60))
    samples.extend((f'step-{i+1}', offset + (s['start_frame'] + s['end_frame']) / 60)
                   for i, s in enumerate(navigation['item_cards']))
    samples.extend((f'code-{i+1}', offset + (s['start_frame'] + s['end_frame']) / 60) for i, s in enumerate(code))
    samples.extend((f'image-{i+1}', offset + (s['start_frame'] + s['end_frame']) / 60) for i, s in enumerate(pictured))
    last = compiled['scenes'][-1]
    samples.extend([('recap', offset + (last['start_frame'] + last['end_frame']) / 60),
                    ('outro', brand['duration_seconds'] - 2)])
    sheet = Image.new('RGB', (1280, 390*((len(samples)+1)//2)), 'white')
    draw = ImageDraw.Draw(sheet)
    for i, (name, second) in enumerate(samples):
        frame = evidence / f'frame-{name}.png'
        subprocess.run([str(FFMPEG), '-y', '-v', 'error', '-ss', str(second), '-i',
                        str(returned / 'video.mp4'), '-frames:v', '1', str(frame)], check=True)
        with Image.open(frame) as picture:
            picture.thumbnail((640, 360))
            x, y = i % 2 * 640, i // 2 * 390
            sheet.paste(picture, (x, y))
            draw.text((x+8, y+364), f'{name}: {second:.2f}s', fill='black')
    sheet.save(evidence / 'contact.jpg', quality=92)
    for name, path in [('package-qa.json', attempt / 'artifacts/package/package-qa.json'),
                        ('episode-navigation.json', attempt / 'artifacts/episode-navigation.json'),
                        ('packaging-resolved.json', attempt / 'artifacts/packaging-resolved.json')]:
        shutil.copy2(path, evidence / name)
    shutil.copy2(returned / 'brand-qa.json', evidence / 'brand-qa.json')
    files = ['web/services/video_render.py', 'core/video/visual_materials.py', 'config/content_columns/ai_opensource.yaml']
    result = {'status': 'PASS', 'job_id': receipt['job_id'], 'run_id': receipt['run_id'],
        'production': receipt['production'], 'automatic_pick_write_approve_render_download': True,
        'greeting_count': 1, 'narration_matches_approved_script': True, 'tutorial_recipe_version': 2,
        'steps': [s['title'] for s in navigation['items']], 'code_scenes': len(code),
        'pictured_scenes': len(pictured), 'automatic_images': len(q['image_materials']),
        'registered_image_focus': True,
        'image_display_note': ('Screenshot scenes rendered' if pictured else
                              'The image is assigned to the command paragraph; native code_demo takes priority.'),
        'new_production_dependencies': 0,
        'writer_requests': version['meta']['usage']['requests'],
        'visual_model_calls': render['visuals']['model_calls'], 'final_render_model_calls': read(returned / 'qa.json')['llm_call_count'],
        'render_attempts': receipt['render_attempts'], 'probes': probes, 'sample_times': samples,
        'returned_artifacts_verified': len(render['artifacts']), 'video_sha256': sha(returned / 'video.mp4'),
        'current_code_renderer_used': True, 'code_renderer_sha256': sha(renderer),
        'brand_profile': brand['profile']['id'], 'brand_outro_seconds': brand['outro_seconds'],
        'source_sha256': {name: sha(ROOT / name) for name in files}}
    (evidence / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': 'PASS', 'seconds': brand['duration_seconds'],
                      'scenes': len(compiled['scenes']), 'samples': len(samples)}))


if __name__ == '__main__':
    verify(Path(sys.argv[1]).resolve())

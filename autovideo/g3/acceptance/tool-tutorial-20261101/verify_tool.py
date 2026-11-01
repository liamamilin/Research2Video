"""Check the unified tutorial's actual media, inherited navigation and voice."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
FFMPEG = ROOT / 'g0/darwin_arm64/ffmpeg'
FFPROBE = ROOT / 'g0/darwin_arm64/ffprobe'


def read(path: Path):
    return json.loads(path.read_text())


def pcm(path: Path) -> np.ndarray:
    return np.frombuffer(subprocess.check_output([str(FFMPEG), '-v', 'error', '-i', str(path),
        '-vn', '-f', 'f32le', '-acodec', 'pcm_f32le', '-ac', '1', '-ar', '24000', '-']), dtype=np.float32)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--parent', type=Path, required=True)
    parser.add_argument('--final', type=Path, required=True)
    args = parser.parse_args()
    out = args.final.resolve()
    receipt = read(out / 'receipt.json')
    attempt = Path(receipt['source_attempt'])
    original, ir = read(args.parent / 'video-ir.json'), read(out / 'video-ir.json')
    assert original['narration'] == ir['narration']
    assert [s['props'] for s in original['scenes']] == [s['props'] for s in ir['scenes']]
    greeting = read(out / 'preparation.json')['greeting']
    spoken = ''.join(s['text'] for s in ir['narration']['segments'])
    assert ''.join(spoken.split()).count(''.join(greeting.split())) == 1
    saved = read(attempt.parents[2] / 'input/input.json')
    from g2.episode_navigation import program_for
    parent_input = read(args.parent / 'input.json')
    expected_program = program_for(parent_input, original)
    assert program_for(saved, ir) == expected_program
    assert program_for({**saved, 'episode': None}, ir) == expected_program
    assert read(out / 'qa.json')['status'] == 'PASS'
    assert read(out / 'qa.json')['real_llm'] is False
    assert read(out / 'qa.json')['real_tts'] is False
    package = read(out / 'package.json')
    package_qa = read(out / 'package-qa.json')
    assert package['status'] == package_qa['status'] == 'PASS'
    navigation = read(out / 'episode-navigation.json')
    assert navigation['video_sha256'] == hashlib.sha256((out / 'content.mp4').read_bytes()).hexdigest()
    assert [item['title'] for item in navigation['items']] == ['准备输入', '执行转换', '查看结果']
    ass = (attempt / 'artifacts/episode-navigation/navigation.ass').read_text()
    assert all(label in ass for label in ['操作步骤', '下一步', '步骤回顾'])
    probes = {}
    for name, frames in [('content.mp4', read(out / 'compiled-timeline.json')['total_frames']),
                         ('video.mp4', package['total_frames'])]:
        path = out / name
        probe = json.loads(subprocess.check_output([str(FFPROBE), '-v', 'error', '-count_frames',
            '-show_entries', 'format=duration:stream=codec_name,width,height,r_frame_rate,nb_read_frames',
            '-of', 'json', str(path)], text=True))
        stream = next(s for s in probe['streams'] if s['codec_name'] == 'h264')
        assert int(stream['nb_read_frames']) == frames
        assert (stream['width'], stream['height'], stream['r_frame_rate']) == (1920, 1080, '30/1')
        decoded = subprocess.run([str(FFMPEG), '-v', 'error', '-xerror', '-i', str(path),
            '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'], capture_output=True, text=True, check=True)
        (out / (name + '.decode.log')).write_text(decoded.stderr)
        probes[name] = probe
    before, after = pcm(args.parent / 'content.mp4'), pcm(out / 'content.mp4')
    count = min(len(before), len(after))
    correlation = float(np.corrcoef(before[:count], after[:count])[0, 1])
    assert correlation > .999
    compiled = read(out / 'compiled-timeline.json')
    offset = package['offset_ms'] / 1000
    samples = [('intro', .5), ('greeting', 2.5), ('agenda', 10)]
    for item in navigation['items']:
        samples.append((f"step-{item['index']}", item['start_frame']/30 + offset + .8))
    for scene in compiled['scenes']:
        if scene['id'] in {'card_3', 'card_5', 'card_6', 'card_8', 'card_9', 'card_10', 'card_11'}:
            samples.append((scene['id'], (scene['start_frame']+scene['end_frame'])/60 + offset))
    samples += [('sources', package['total_seconds']-4), ('outro', package['total_seconds']-.6)]
    sheet = Image.new('RGB', (1280, 390*((len(samples)+1)//2)), 'white')
    draw = ImageDraw.Draw(sheet)
    for i, (name, second) in enumerate(samples):
        frame = out / f'frame-{name}.png'
        subprocess.run([str(FFMPEG), '-y', '-v', 'error', '-ss', str(second), '-i',
            str(out / 'video.mp4'), '-frames:v', '1', str(frame)], check=True)
        with Image.open(frame) as picture:
            picture.thumbnail((640, 360))
            x, y = i % 2 * 640, i // 2 * 390
            sheet.paste(picture, (x, y))
            draw.text((x+8, y+364), f'{name}: {second:.2f}s', fill='black')
    sheet.save(out / 'contact.jpg', quality=92)
    source_files = ['g1/renderer/src/CodeDemo.tsx', 'g2/explanation.py', 'g2/episode_navigation.py',
                    'g2/creative_library/catalog.json', 'g3/store.py', 'g3/revision_support.py']
    assert hashlib.sha256((attempt / 'renderer/src/CodeDemo.tsx').read_bytes()).hexdigest() == hashlib.sha256((ROOT / source_files[0]).read_bytes()).hexdigest()
    result = {'status': 'PASS', 'probes': probes, 'normal_parent_llm_calls': read(args.parent / 'qa.json')['llm_call_count'],
        'final_new_model_calls': 0, 'final_new_tts_calls': 0, 'greeting_count': 1,
        'narration_unchanged': True, 'scene_props_unchanged': True,
        'decoded_content_audio_correlation': correlation, 'episode_inherited': bool(saved.get('episode')),
        'tutorial_default_derives_same_steps': True,
        'step_titles': [i['title'] for i in navigation['items']], 'sample_times': samples,
        'source_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in source_files},
        'final_video_sha256': hashlib.sha256((out / 'video.mp4').read_bytes()).hexdigest()}
    (out / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': 'PASS', 'seconds': package['total_seconds'],
                      'greeting_count': 1, 'audio_correlation': correlation}), flush=True)


if __name__ == '__main__':
    main()

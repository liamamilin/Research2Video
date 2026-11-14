"""Check and present the original-audio comparisons; never an E2E substitute."""
import argparse
import json
from pathlib import Path
import subprocess

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / 'g3/data/validation/daily-effects-20261112/experiments'
FF = str(ROOT / 'g0/darwin_arm64/ffmpeg')
PROBE = str(ROOT / 'g0/darwin_arm64/ffprobe')

parser = argparse.ArgumentParser()
parser.add_argument('--suffix', default='')
args = parser.parse_args()

for name in ['grouped', 'holdout-branch', 'holdout-return']:
    out = BASE / (name + args.suffix)
    before, after = BASE / (name + '-before') / 'video.mp4', out / 'video.mp4'
    videos = [before, after]
    info, audio = [], []
    for video in videos:
        subprocess.run([FF, '-v', 'error', '-xerror', '-i', str(video), '-f', 'null', '-'], check=True)
        info.append(json.loads(subprocess.check_output([PROBE, '-v', 'error', '-show_streams', '-of', 'json', str(video)])))
        audio.append(np.frombuffer(subprocess.check_output([FF, '-v', 'error', '-i', str(video), '-vn', '-ac', '1', '-ar', '16000', '-f', 'f32le', '-']), dtype=np.float32))
    frames = [next(s for s in data['streams'] if s['codec_type'] == 'video')['nb_frames'] for data in info]
    assert frames[0] == frames[1]
    count = min(map(len, audio))
    correlation = float(np.corrcoef(audio[0][:count], audio[1][:count])[0, 1])
    assert abs(len(audio[0]) - len(audio[1])) < 1600 and correlation > .999
    assert (out / 'props.json').read_bytes() == (BASE / (name + '-before') / 'props.json').read_bytes()
    subprocess.run([FF, '-v', 'error', '-y', '-i', str(before), '-i', str(after), '-filter_complex', '[0:v][1:v]hstack=inputs=2[v]', '-map', '[v]', '-map', '1:a', '-c:v', 'libx264', '-preset', 'fast', '-crf', '20', '-c:a', 'copy', str(out / 'comparison.mp4')], check=True)
    seconds = int(frames[0]) / 30
    ticks = list(range(0, int(seconds)))
    for start in range(0, len(ticks), 6):
        sheet = Image.new('RGB', (1920, 900), 'white')
        draw = ImageDraw.Draw(sheet)
        for index, at in enumerate(ticks[start:start + 6]):
            target = out / f'comparison-{at:03d}.jpg'
            subprocess.run([FF, '-v', 'error', '-y', '-ss', str(at), '-i', str(out / 'comparison.mp4'), '-frames:v', '1', '-vf', 'scale=960:270', str(target)], check=True)
            x, y = index % 2 * 960, index // 2 * 300
            sheet.paste(Image.open(target), (x, y))
            draw.text((x + 8, y + 276), f'{at}s: before | after', fill='black')
        sheet.save(out / f'comparison-sheet-{start//6 + 1:02d}.jpg')
    (out / 'verification.json').write_text(json.dumps({'frames': frames, 'duration_seconds': seconds, 'decode_passed': True, 'same_props': True, 'audio_correlation': correlation, 'audio_sample_counts': list(map(len, audio)), 'inspection_method': 'Chronological original-video frames and waveform checks; not continuous human listening'}, ensure_ascii=False, indent=2) + '\n')
    print(name, seconds, correlation, flush=True)

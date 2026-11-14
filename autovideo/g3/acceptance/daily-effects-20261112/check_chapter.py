"""Compare the frozen renderer with a chapter cut from the actual full output."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[3]
JOB = '9fc7f436-650a-49f1-9ede-98cc5c078092'
RUN = ROOT / 'g3/data/jobs' / JOB / 'attempts/1/g2-run'
OUT = ROOT / 'g3/data/validation/daily-effects-20261112/e2e' / JOB / 'chapter-comparison'
FF = str(ROOT / 'g0/darwin_arm64/ffmpeg')
PROBE = str(ROOT / 'g0/darwin_arm64/ffprobe')


def execute(args):
    subprocess.run([FF, '-v', 'error', '-y', *args], check=True)


def main():
    record = json.loads((OUT / 'source.json').read_text())
    props = RUN / 'artifacts/render-props.json'
    source = RUN / 'artifacts/G2_自动生成.mp4'
    start = record['start_frame'] / 30
    count = record['end_frame_inclusive'] - record['start_frame'] + 1
    duration = count / 30
    # Use the real full workflow output, never another candidate renderer.
    execute(['-ss', str(start), '-i', str(source), '-t', str(duration),
             '-vf', 'scale=960:540', '-c:v', 'libx264', '-preset', 'fast', '-crf', '20',
             '-c:a', 'aac', '-b:a', '192k', str(OUT / 'after.mp4')])
    before = OUT / 'before.mp4'
    navigation = RUN / 'artifacts/episode-navigation/navigation.ass'
    if navigation.exists():
        escaped = str(navigation).replace('\\', '\\\\').replace(':', '\\:').replace("'", "'\\''")
        before = OUT / 'before-with-navigation.mp4'
        execute(['-i', str(OUT / 'before.mp4'), '-vf',
                 f"setpts=PTS+{start}/TB,ass=filename='{escaped}',setpts=PTS-STARTPTS",
                 '-r', '30', '-fps_mode', 'cfr', '-c:v', 'libx264', '-preset', 'fast', '-crf', '20', '-c:a', 'copy', str(before)])
    info, audio = [], []
    for file in (before, OUT / 'after.mp4'):
        execute(['-xerror', '-i', str(file), '-f', 'null', '-'])
        streams = json.loads(subprocess.check_output([PROBE, '-v', 'error', '-show_streams', '-of', 'json', str(file)]))['streams']
        info.append({'path': str(file), 'frames': int(next(s for s in streams if s['codec_type'] == 'video')['nb_frames'])})
        audio.append(np.frombuffer(subprocess.check_output([FF, '-v', 'error', '-i', str(file), '-vn', '-ac', '1', '-ar', '16000', '-f', 'f32le', '-']), dtype=np.float32))
    samples = min(map(len, audio))
    correlation = float(np.corrcoef(audio[0][:samples], audio[1][:samples])[0, 1])
    check = {'source_full_workflow_video': str(source.resolve()),
             'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
             'props_sha256': hashlib.sha256(props.read_bytes()).hexdigest(),
             'start_seconds': start, 'duration_seconds': duration, 'media': info,
             'decode_passed': True, 'audio_samples': list(map(len, audio)), 'audio_correlation': correlation,
             'same_automatic_navigation': navigation.exists(),
             'manual_changes_to_input_or_plan': False}
    (OUT / 'verification.json').write_text(json.dumps(check, ensure_ascii=False, indent=2) + '\n')
    assert all(item['frames'] == count for item in info)
    assert abs(len(audio[0]) - len(audio[1])) < 1600
    assert correlation > .999, f'Inspect audio alignment before acceptance: {correlation}'
    execute(['-i', str(before), '-i', str(OUT / 'after.mp4'), '-filter_complex', '[0:v][1:v]hstack=inputs=2[v]',
             '-map', '[v]', '-map', '1:a', '-c:v', 'libx264', '-preset', 'fast', '-crf', '20', '-c:a', 'copy', str(OUT / 'comparison.mp4')])
    frames = OUT / 'frames'
    frames.mkdir(exist_ok=True)
    execute(['-i', str(OUT / 'comparison.mp4'), '-vf', r'select=not(mod(n\,30)),scale=960:270',
             '-vsync', 'vfr', str(frames / '%04d.jpg')])
    images = sorted(frames.glob('*.jpg'))
    for page in range(0, len(images), 6):
        sheet = Image.new('RGB', (1920, 900), 'white')
        draw = ImageDraw.Draw(sheet)
        for offset, file in enumerate(images[page:page + 6]):
            x, y = offset % 2 * 960, offset // 2 * 300
            sheet.paste(Image.open(file), (x, y))
            draw.text((x + 8, y + 277), f'{start + page + offset:.2f}s original time; before | actual workflow', fill='black')
        sheet.save(OUT / f'sequence-{page // 6 + 1:02d}.jpg')
    print(json.dumps({'duration': duration, 'correlation': correlation, 'pages': (len(images) + 5) // 6}))


if __name__ == '__main__':
    main()

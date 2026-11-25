"""Inspect completed MP4s; keep full-resolution cue frames and contact sheets."""
import argparse
import json
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[3]
p = argparse.ArgumentParser()
p.add_argument('folder', type=Path)
p.add_argument('--spoken', action='store_true')
a = p.parse_args()
folder = a.folder.resolve()
out = folder / 'qa'
out.mkdir(exist_ok=False)
video = next((folder / 'artifacts').glob('*.mp4')) if a.spoken else folder / 'video.mp4'
ff = ROOT / 'g0/darwin_arm64/ffmpeg'
probe = ROOT / 'g0/darwin_arm64/ffprobe'
meta = json.loads(subprocess.check_output([str(probe), '-v', 'error', '-count_frames', '-show_streams', '-show_format', '-of', 'json', str(video)]))
with (out / 'full-decode.log').open('w') as log:
    subprocess.run([str(ff), '-v', 'error', '-xerror', '-i', str(video), '-f', 'null', '-'], stdout=log, stderr=log, check=True)
props = json.loads((folder / ('artifacts/render-props.json' if a.spoken else 'props.json')).read_text())
stream = next(s for s in meta['streams'] if s['codec_type'] == 'video')
assert int(stream['nb_read_frames']) == props['ir']['total_frames']
assert (stream['width'], stream['height'], stream['avg_frame_rate']) == (1920, 1080, '30/1')
if a.spoken:
    schedule = [{'id': b['id'], 'samples': [max(0, b['frame'] - 18), b['frame']]} for b in props['ir']['scenes'][0]['beats'] if b['target_id'].startswith('node:')]
else:
    schedule = json.loads((folder / 'schedule.json').read_text())
shots = []
for row in schedule:
    for index, frame in enumerate(row['samples']):
        path = out / f'{row["id"]}-{index}.png'
        subprocess.run([str(ff), '-v', 'error', '-ss', str(frame / 30), '-i', str(video), '-frames:v', '1', str(path)], check=True)
        shots.append({'id': row['id'], 'frame': frame, 'path': path.name})
font = ImageFont.truetype('/System/Library/Fonts/Hiragino Sans GB.ttc', 18)
for start in range(0, len(shots), 12):
    sheet = Image.new('RGB', (1440, 1200), 'white')
    draw = ImageDraw.Draw(sheet)
    for i, row in enumerate(shots[start:start + 12]):
        im = Image.open(out / row['path'])
        im.thumbnail((480, 270))
        x, y = i % 3 * 480, i // 3 * 300
        sheet.paste(im, (x, y))
        draw.text((x + 8, y + 272), f'{row["id"]} / {row["frame"]}', font=font, fill='black')
    sheet.save(out / f'contact-{start // 12 + 1}.jpg', quality=92)
result = {'video': str(video), 'full_decode': 'PASS', 'duration_seconds': float(meta['format']['duration']),
          'frames': int(stream['nb_read_frames']), 'streams': [{k: s[k] for k in ['codec_type', 'codec_name', 'width', 'height', 'sample_rate'] if k in s} for s in meta['streams']],
          'snapshots': shots, 'note': 'Use full-resolution frames for reading; contact sheets only inspect scene coverage and stable composition.'}
(out / 'results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({k: v for k, v in result.items() if k != 'snapshots'}))

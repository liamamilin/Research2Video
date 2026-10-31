"""Extract actual overview/focus frames and record the selected source/shot chain."""
import argparse
import json
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[3]
AUTO = ROOT.parents[1] / 'autovideo'
parser = argparse.ArgumentParser()
parser.add_argument('--batch', type=Path, required=True)
args = parser.parse_args()
batch = args.batch.resolve()
results = json.loads((batch / 'results.json').read_text())
for key, result in results.items():
    if result['status'] != 'COMPLETED':
        continue
    work = batch / key
    attempt = Path(result['engine_data']) / 'jobs' / result['engine_job_id'] / 'attempts'
    compiled = max(attempt.glob('*/g2-run/artifacts/compiled-timeline.json'), key=lambda p: int(p.parents[2].name))
    timeline = json.loads(compiled.read_text())
    fps = timeline['video']['fps']
    shots = []
    for scene in timeline['scenes']:
        if scene['component'] not in ('object_showcase', 'material_collage'):
            continue
        shots.append((scene['id'] + '-overview', min(scene['end_frame']-1, scene['start_frame']+18), []))
        beats = {beat['id']: beat['frame'] for beat in scene['beats']}
        for i, cue in enumerate(scene['props'].get('shot_plan', {}).get('cues', [])):
            frame = min(scene['end_frame']-1, beats[cue['beat_id']]+20)
            if not any(abs(frame-existing[1]) < 15 for existing in shots):
                shots.append((scene['id'] + '-focus-' + str(i+1), frame, cue['targets']))
    frames = work / 'frames'
    frames.mkdir(exist_ok=True)
    thumbnails = []
    evidence = []
    for name, frame, targets in shots:
        path = frames / (name + '.png')
        subprocess.run([str(AUTO / 'g0/darwin_arm64/ffmpeg'), '-v', 'error', '-y', '-ss', str(frame/fps),
                        '-i', str(work / 'content.mp4'), '-frames:v', '1', str(path)], check=True)
        with Image.open(path) as image:
            thumbnail = image.convert('RGB').resize((768, 432))
        panel = Image.new('RGB', (768, 462), '#ffffff')
        panel.paste(thumbnail, (0, 30))
        ImageDraw.Draw(panel).text((12, 8), f'{name}  {frame/fps:.2f}s  {" ".join(targets)}', fill='#263d31')
        thumbnails.append(panel)
        evidence.append({'name': name, 'seconds': round(frame/fps, 3), 'targets': targets, 'path': str(path)})
    sheet = Image.new('RGB', (1536, 462*((len(thumbnails)+1)//2)), '#e9eeeb')
    for i, thumb in enumerate(thumbnails):
        sheet.paste(thumb, ((i%2)*768, (i//2)*462))
    sheet.save(work / 'contact-sheet.png')
    render = json.loads((work / 'render.json').read_text())
    evidence = {'duration_seconds': timeline['total_frames']/fps, 'frames': evidence,
        'materials': render['submission_request']['image_materials'],
        'scenes': [{k: scene.get(k) for k in ('id', 'component', 'start_frame', 'end_frame')}
                   | {'shot_plan': scene['props'].get('shot_plan')} for scene in timeline['scenes']]}
    (work / 'visual-evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    print(json.dumps({'case': key, 'frames': len(thumbnails), 'sheet': str(work/'contact-sheet.png')}, ensure_ascii=False))

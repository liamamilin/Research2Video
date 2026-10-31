"""Extract actual completed-video frames for this fixed follow-up batch only."""
import argparse
import json
from pathlib import Path
import subprocess
from uuid import uuid4

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[3]
AUTO = ROOT.parents[1] / 'autovideo'
WATCH = ROOT / 'state/validation/static-screenshots-20261028/next-round-watch'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('render_id')
    args = parser.parse_args()
    evidence = WATCH / 'evidence' / args.render_id
    render = json.loads((evidence/'render.json').read_text())
    assert render['status'] == 'COMPLETED'
    attempts = AUTO / 'g3/data/jobs' / render['autovideo_job_id'] / 'attempts'
    attempt = max((p for p in attempts.iterdir() if p.name.isdigit()), key=lambda p: int(p.name))
    run = attempt/'g2-run'
    timeline = json.loads((run/'artifacts/compiled-timeline.json').read_text())
    folder = ROOT/'state/video_renders'/args.render_id
    package = json.loads((folder/'package.json').read_text())
    offset = float(package.get('offset_ms', 0))/1000
    fps = timeline['video']['fps']
    image_scenes = [s for s in timeline['scenes'] if s['component'] in ('object_showcase', 'material_collage')]
    out = evidence/('actual-review-'+uuid4().hex[:8])
    frames = out/'frames'
    frames.mkdir(parents=True)
    records = []
    scenes = image_scenes or timeline['scenes']
    for scene in scenes:
        start, end = scene['start_frame'], scene['end_frame']
        beats = {b['id']: b['frame'] for b in scene['beats']}
        samples = [('overview', start+18, [])]
        for i, cue in enumerate(scene['props'].get('shot_plan', {}).get('cues', [])):
            at = beats[cue['beat_id']]
            samples.extend([(f'cue{i+1}-before', at-6, cue['targets']), (f'cue{i+1}-after', at+24, cue['targets'])])
        summary = scene['props'].get('shot_plan', {}).get('summary_beat_id')
        if summary:
            samples.append(('summary', beats[summary]+24, []))
        samples.append(('ending', end-12, []))
        for label, frame, targets in samples:
            frame = min(end-1, max(start, frame))
            seconds = frame/fps+offset
            image = frames/f'{len(records)+1:05d}.png'
            subprocess.run([str(AUTO/'g0/darwin_arm64/ffmpeg'), '-nostdin', '-v', 'error', '-ss', str(seconds),
                            '-i', str(folder/'video.mp4'), '-frames:v', '1', str(image)], check=True)
            records.append({'scene_id': scene['id'], 'label': label, 'content_frame': frame,
                            'content_seconds': frame/fps, 'final_video_seconds': seconds,
                            'targets': targets, 'path': str(image)})
    sheets = []
    for begin in range(0, len(records), 6):
        chunk = records[begin:begin+6]
        sheet = Image.new('RGB', (1536, 462*((len(chunk)+1)//2)), '#e9eeeb')
        for i, record in enumerate(chunk):
            with Image.open(record['path']) as image:
                thumb = image.convert('RGB').resize((768, 432))
            panel = Image.new('RGB', (768, 462), 'white')
            panel.paste(thumb, (0, 30))
            ImageDraw.Draw(panel).text((10, 9), f"{begin+i+1:02d} {record['scene_id']} {record['label']} {record['final_video_seconds']:.3f}s", fill='#263d31')
            sheet.paste(panel, ((i%2)*768, (i//2)*462))
        path = out/f'sheet-{len(sheets)+1:02d}.jpg'
        sheet.save(path, quality=93)
        sheets.append(str(path))
    result = {'render_id': args.render_id, 'engine_job_id': render['autovideo_job_id'], 'attempt': int(attempt.name),
              'source_video': str(folder/'video.mp4'), 'content_video': str(folder/'content.mp4'),
              'offset_seconds': offset, 'duration_seconds': timeline['total_frames']/fps,
              'image_scenes': [s['id'] for s in image_scenes], 'frames': records, 'sheets': sheets,
              'run': str(run), 'actual_frames_reviewed': False}
    (out/'frames.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'render_id':args.render_id,'out':str(out),'frames':len(records),'sheets':sheets}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()

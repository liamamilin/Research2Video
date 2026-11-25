"""Decode complete delivered files and sample every content scene for visual review."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from core.video.presentation import snapshot
ENGINE = ROOT.parents[1] / 'autovideo'
FFMPEG = ENGINE / 'g0/darwin_arm64/ffmpeg'
FFPROBE = ENGINE / 'g0/darwin_arm64/ffprobe'


def main(out: Path):
    receipt = json.loads((out/'receipt.json').read_text())
    files = Path(receipt['files_dir'])
    frames = out / 'review'
    frames.mkdir(exist_ok=True)
    decode = {}
    for name in ('video.mp4', 'content.mp4'):
        video = files/name
        if not video.exists():
            continue
        result = subprocess.run([str(FFMPEG), '-nostdin', '-v', 'error', '-xerror', '-i', str(video),
                                 '-f', 'null', '-'], text=True, capture_output=True)
        probe = json.loads(subprocess.check_output([str(FFPROBE), '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(video)]))
        streams = [{k:s[k] for k in ('codec_type', 'codec_name', 'width', 'height', 'sample_rate', 'duration') if k in s}
                   for s in probe['streams']]
        decode[name] = {'exit_code': result.returncode, 'stderr': result.stderr, 'streams': streams,
                        'duration': float(probe['format']['duration'])}
        assert result.returncode == 0 and not result.stderr
        assert {'audio', 'video'} <= {s['codec_type'] for s in streams}
    ir = json.loads((files/'video-ir.json').read_text())
    board = json.loads((files/'storyboard.json').read_text())
    render = next(r for r in json.loads((out/'renders.json').read_text()) if r['id'] == receipt['render_id'])
    version = json.loads((out/'version.json').read_text())
    presentation = snapshot(version, render.get('submission_request') or render['request'], board)
    rows = []
    content = files/('content.mp4' if (files/'content.mp4').exists() else 'video.mp4')
    for i, scene in enumerate(ir['scenes']):
        at = (scene['start_ms'] + scene['duration_ms'] * .65)/1000
        path = frames/f'{i+1:02d}-{scene["id"]}.jpg'
        subprocess.run([str(FFMPEG), '-v', 'error', '-ss', str(at), '-i', str(content), '-frames:v', '1',
                        '-update', '1', '-y', '-vf', 'scale=960:-1', str(path)], check=True)
        texts = [s['text'] for s in ir['narration']['segments'] if s['id'] in scene['narration_segment_ids']]
        rows.append({'scene': scene['id'], 'component': scene['component'], 'at': at, 'image': str(path),
                     'narration': texts, 'props': scene['props']})
    # A sheet is a navigation aid. Full scene frames remain available for close inspection.
    from PIL import Image, ImageDraw
    cols, thumb_w, thumb_h = 3, 480, 300
    sheet = Image.new('RGB', (cols*thumb_w, ((len(rows)+cols-1)//cols)*thumb_h), 'white')
    draw = ImageDraw.Draw(sheet)
    for i, row in enumerate(rows):
        with Image.open(row['image']) as frame:
            frame.thumbnail((480,270)); x,y=(i%cols)*thumb_w,(i//cols)*thumb_h
            sheet.paste(frame,(x,y)); draw.text((x+8,y+274),f'{i+1:02d} {row["scene"]} / {row["at"]:.1f}s', fill='black')
    sheet.save(frames/'all-scenes.jpg')
    qa = {}
    for name in ('qa.json', 'brand-qa.json', 'package-qa.json', 'narration-check.json'):
        path = files/name
        if path.exists():
            report = json.loads(path.read_text())
            qa[name] = {'status': report.get('status'),
                        **({'audio': report['audio']} if 'audio' in report else {})}
            assert report.get('status') == 'PASS', f'{name}: {report.get("status")}'
    result = {'receipt': receipt, 'decode': decode, 'engine_qa': qa, 'presentation': presentation, 'scenes': rows,
              'audio_review': 'complete decode and stream checks; no claim of human listening',
              'visual_review': 'scene samples generated; inspected frames recorded separately'}
    (out/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'output': str(out), 'decode': decode, 'presentation': presentation},ensure_ascii=False),flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    main(parser.parse_args().output.resolve())

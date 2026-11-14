"""Compare a normal workflow MP4 with the frozen renderer on its identical plan.

The baseline is a controlled rendering comparison, never a second E2E run.
Full decode and chronological frame sheets supplement the playable comparison;
they do not constitute a listening test or an audience-retention measurement.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / 'g3/data/validation/daily-effects-20261111'
FFMPEG = str(ROOT / 'g0/darwin_arm64/ffmpeg')
FFPROBE = str(ROOT / 'g0/darwin_arm64/ffprobe')


def save(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--job', required=True)
    parser.add_argument('--baseline', action='store_true')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--delivery-only', action='store_true', help='Check a fresh final workflow run without repeating the controlled baseline experiment')
    args = parser.parse_args()
    run = ROOT / 'g3/data/jobs' / args.job / 'attempts/1/g2-run'
    props = run / 'artifacts/render-props.json'
    original = run / 'artifacts/G2_自动生成.mp4'
    delivered = run / 'artifacts/brand/video.mp4'
    out = BASE / 'e2e' / args.job
    out.mkdir(parents=True, exist_ok=True)
    ir = json.loads(props.read_text())['ir']
    if args.baseline:
        renderer = out / 'baseline-renderer'
        shutil.copytree(BASE / 'baseline-renderer/src', renderer / 'src')
        (renderer / 'node_modules').symlink_to(ROOT / 'g0/vendor/remotion-audiogram/node_modules', target_is_directory=True)
        command = ['node', str(ROOT / 'g0/vendor/remotion-audiogram/node_modules/@remotion/cli/remotion-cli.js'),
                   'render', 'src/index.ts', 'G1CommunicationNoise', str(out / 'before-renderer.mp4'),
                   '--codec=h264', '--crf=20', '--concurrency=2', '--scale=0.5',
                   f'--props={props}', f'--public-dir={run / "fixtures"}',
                   '--browser-executable=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
                   '--bundle-cache=false']
        save(out / 'baseline-command.json', command)
        with (out / 'baseline-render.log').open('w') as log:
            subprocess.run(command, cwd=renderer, stdout=log, stderr=log, check=True)
    if args.check:
        navigation = run / 'artifacts/episode-navigation/navigation.ass'
        if navigation.exists() and not args.delivery_only:
            escaped = str(navigation).replace('\\', '\\\\').replace(':', '\\:').replace("'", "'\\''")
            subprocess.run([FFMPEG, '-v', 'error', '-y', '-i', str(out / 'before-renderer.mp4'),
                '-vf', f"ass=filename='{escaped}'", '-c:v', 'libx264', '-preset', 'fast', '-crf', '20',
                '-c:a', 'copy', str(out / 'before.mp4')], check=True)
        elif not args.delivery_only:
            shutil.copy2(out / 'before-renderer.mp4', out / 'before.mp4')
        results = {}
        videos = [('after_content', original), ('delivered', delivered)]
        if not args.delivery_only:
            videos.insert(0, ('before', out / 'before.mp4'))
        for name, video in videos:
            result = json.loads(subprocess.check_output([FFPROBE, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(video)]))
            with (out / f'{name}-decode.log').open('w') as log:
                subprocess.run([FFMPEG, '-v', 'error', '-xerror', '-i', str(video), '-f', 'null', '-'], stderr=log, check=True)
            result['complete_decode_passed'] = True
            result['path'] = str(video.resolve())
            results[name] = result
        after_stream = next(s for s in results['after_content']['streams'] if s['codec_type'] == 'video')
        assert int(after_stream['nb_frames']) == ir['total_frames']
        if not args.delivery_only:
            audio = [np.frombuffer(subprocess.check_output([FFMPEG, '-v', 'error', '-i', str(video),
                     '-vn', '-ac', '1', '-ar', '16000', '-f', 'f32le', '-']), dtype=np.float32)
                     for video in (out / 'before.mp4', original)]
            count = min(map(len, audio))
            results['content_audio_comparison'] = {'sample_counts': list(map(len, audio)),
                'correlation': float(np.corrcoef(audio[0][:count], audio[1][:count])[0, 1])}
            before_stream = next(s for s in results['before']['streams'] if s['codec_type'] == 'video')
            assert before_stream['nb_frames'] == after_stream['nb_frames'], 'Comparison frame counts differ'
            assert abs(len(audio[0]) - len(audio[1])) < 1600, 'Comparison audio duration differs'
            assert results['content_audio_comparison']['correlation'] > .999, 'Comparison audio differs'
        save(out / 'media-check.json', results)
        scenes = []
        navigation_record = run / 'artifacts/episode-navigation.json'
        covered = json.loads(navigation_record.read_text())['covered_frames'] if navigation_record.exists() else []
        for scene in ir['scenes']:
            reveals = [b['frame'] for b in scene['beats'] if b['action'] == 'reveal' and b['target_id'].startswith('line:')]
            first = min(reveals) if reveals else scene['start_frame']
            covered_delay = sum(max(0, min(first, end) - max(scene['start_frame'], start)) for start, end in covered)
            scenes.append({'id': scene['id'], 'component': scene['component'], 'title': scene['props'].get('title'),
                           'story_role': scene['props'].get('story', {}).get('role'),
                           'recap_references': scene['props'].get('story', {}).get('recap', []),
                           'start_seconds': scene['start_frame'] / 30, 'end_seconds': scene['end_frame'] / 30,
                           'uncovered_first_object_delay': max(0, first - scene['start_frame'] - covered_delay) / 30,
                           'former_first_object_delay': (min(reveals) - scene['start_frame']) / 30 if reveals else None})
        save(out / 'source.json', {'job': args.job, 'actual_workflow_video': str(delivered.resolve()),
            'comparison_content_video': str(original.resolve()),
            'same_automatic_episode_navigation_applied': navigation.exists(),
            'props_sha256': hashlib.sha256(props.read_bytes()).hexdigest(),
            'comparison_uses_identical_plan_audio_and_timing': True if not args.delivery_only else None,
            'baseline_scale': 0.5 if not args.delivery_only else None, 'scenes': scenes})
        if args.delivery_only:
            assert not any(s['recap_references'] for s in scenes), 'Automatic recap references still override planned scenes'
        # Full chronological sequence, at two-second intervals. Retain every
        # sampled frame and its timestamp, not just flattering selected stills.
        frames = out / 'frames'
        frames.mkdir(exist_ok=True)
        subprocess.run([FFMPEG, '-v', 'error', '-y', '-i', str(delivered), '-vf', r'select=not(mod(n\,60)),scale=480:-1', '-vsync', 'vfr',
                        str(frames / '%04d.jpg')], check=True)
        images = sorted(frames.glob('*.jpg'))
        for page in range(0, len(images), 16):
            sheet = Image.new('RGB', (1920, 1200), 'white')
            draw = ImageDraw.Draw(sheet)
            for offset, file in enumerate(images[page:page + 16]):
                x, y = (offset % 4) * 480, (offset // 4) * 300
                sheet.paste(Image.open(file), (x, y))
                draw.text((x + 8, y + 278), f'{(page + offset) * 2:04d}s', fill='black')
            sheet.save(out / f'sequence-{page // 16 + 1:02d}.jpg')
        if args.delivery_only:
            print(out, flush=True)
            return
        # Keep a playable, full-duration side-by-side comparison with one audio.
        subprocess.run([FFMPEG, '-v', 'error', '-y', '-i', str(out / 'before.mp4'), '-i', str(original),
            '-filter_complex', '[0:v]scale=960:540[a];[1:v]scale=960:540[b];[a][b]hstack=inputs=2[v]',
            '-map', '[v]', '-map', '1:a:0', '-c:v', 'libx264', '-preset', 'fast', '-crf', '22',
            '-c:a', 'copy', str(out / 'comparison-left-before-right-after.mp4')], check=True)
        candidates = [s for s in scenes if s['component'] == 'concept_reveal' and s['former_first_object_delay'] is not None]
        if candidates:
            worst = max(candidates, key=lambda s: s['uncovered_first_object_delay'])
            start = max(0, worst['start_seconds'] - 3)
            stop = min(float(after_stream['duration']), worst['end_seconds'] + 3)
            save(out / 'focus.json', {**worst, 'clip_start': start, 'clip_end': stop,
                'selection': 'largest first-object cue delay outside existing agenda/chapter covers among new automatic concept scenes'})
            subprocess.run([FFMPEG, '-v', 'error', '-y', '-ss', str(start),
                '-i', str(out / 'comparison-left-before-right-after.mp4'), '-t', str(stop-start),
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '20', '-c:a', 'aac',
                str(out / 'focus-comparison.mp4')], check=True)
        print(out, flush=True)


if __name__ == '__main__':
    main()

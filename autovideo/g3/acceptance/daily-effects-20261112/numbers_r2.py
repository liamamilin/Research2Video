"""Controlled expressions on unchanged automatic plans and original narration."""
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
BASE = ROOT / 'g3/data/validation/daily-effects-20261112/numbers-r2'
OLD = "...motion('line:0'),...textStyle,fontSize:p.value.length"


def save(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def check(name: str, variants: list[str]) -> None:
    out = BASE / 'experiments' / name
    ff = str(ROOT / 'g0/darwin_arm64/ffmpeg')
    probe = str(ROOT / 'g0/darwin_arm64/ffprobe')
    source = json.loads((out / variants[0] / 'source.json').read_text())
    if 'production' in variants:
        navigation = ROOT / 'g3/data/jobs' / source['job'] / 'attempts/1/g2-run/artifacts/episode-navigation/navigation.ass'
        if navigation.exists():
            escaped = str(navigation).replace('\\', '\\\\').replace(':', '\\:').replace("'", "'\\''")
            for variant in variants:
                if variant == 'production':
                    continue
                folder = out / variant
                if (folder / 'navigation.json').exists():
                    continue
                raw = folder / 'renderer-only.mp4'
                # Retain the renderer-only comparison before applying the
                # same automatic chapter overlays as the workflow output.
                if not raw.exists():
                    (folder / 'video.mp4').rename(raw)
                command = [ff, '-v', 'error', '-y', '-i', str(raw), '-vf',
                    f"setpts=PTS+{source['start_frame']}/30/TB,ass=filename='{escaped}',setpts=PTS-STARTPTS",
                    '-r', '30', '-fps_mode', 'cfr', '-c:v', 'libx264', '-preset', 'fast', '-crf', '20', '-c:a', 'copy', str(folder / 'video.mp4')]
                subprocess.run(command, check=True)
                save(folder / 'navigation.json', {'source': str(navigation),
                    'sha256': hashlib.sha256(navigation.read_bytes()).hexdigest(), 'command': command})
    media, audio = [], []
    reference = (out / variants[0] / 'props.json').read_bytes()
    for variant in variants:
        video = out / variant / 'video.mp4'
        assert (out / variant / 'props.json').read_bytes() == reference
        subprocess.run([ff, '-v', 'error', '-xerror', '-i', str(video), '-f', 'null', '-'], check=True)
        data = json.loads(subprocess.check_output([probe, '-v', 'error', '-show_streams', '-of', 'json', str(video)]))
        stream = next(s for s in data['streams'] if s['codec_type'] == 'video')
        media.append({'variant': variant, 'frames': int(stream['nb_frames']), 'duration': float(stream['duration'])})
        audio.append(np.frombuffer(subprocess.check_output([ff, '-v', 'error', '-i', str(video),
            '-vn', '-ac', '1', '-ar', '16000', '-f', 'f32le', '-']), dtype=np.float32))
    expected = source['end_frame_exclusive'] - source['start_frame']
    assert all(m['frames'] == expected for m in media)
    correlations = []
    for samples in audio[1:]:
        assert abs(len(samples) - len(audio[0])) < 1600
        count = min(len(samples), len(audio[0]))
        correlations.append(float(np.corrcoef(audio[0][:count], samples[:count])[0, 1]))
    assert all(c > .999 for c in correlations)
    inputs = [part for v in variants for part in ['-i', str(out / v / 'video.mp4')]]
    graphs = [f'[{i}:v]scale=480:270[v{i}]' for i in range(len(variants))]
    graphs.append(''.join(f'[v{i}]' for i in range(len(variants))) + f'hstack=inputs={len(variants)}[v]')
    comparison = out / 'comparison.mp4'
    subprocess.run([ff, '-v', 'error', '-y', *inputs, '-filter_complex', ';'.join(graphs),
        '-map', '[v]', '-map', '0:a', '-c:v', 'libx264', '-preset', 'fast', '-crf', '18',
        '-c:a', 'copy', str(comparison)], check=True)
    frames = out / 'frames'
    frames.mkdir(exist_ok=True)
    subprocess.run([ff, '-v', 'error', '-y', '-i', str(comparison), '-vf', r'select=not(mod(n\,30))',
        '-fps_mode', 'vfr', str(frames / '%04d.jpg')], check=True)
    images = sorted(frames.glob('*.jpg'))
    for start in range(0, len(images), 4):
        sheet = Image.new('RGB', (480 * len(variants), 1200), 'white')
        draw = ImageDraw.Draw(sheet)
        for row, file in enumerate(images[start:start + 4]):
            sheet.paste(Image.open(file), (0, row * 300))
            draw.text((8, row * 300 + 275), f'{source["start_frame"]/30+start+row:.2f}s original | ' + ' | '.join(variants), fill='black')
        sheet.save(out / f'sequence-{start//4+1:02d}.jpg')
    save(out / 'verification.json', {'media': media, 'same_props': True, 'complete_decode_passed': True,
        'audio_correlations': correlations, 'audio_sample_counts': list(map(len, audio)),
        'inspection_method': 'Original-audio MP4 retained; chronological per-second frames, not continuous human listening'})
    print(json.dumps({'name': name, 'media': media, 'correlations': correlations}), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--job', required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--start-frame', required=True, type=int)
    parser.add_argument('--end-frame', required=True, type=int, help='Exclusive')
    parser.add_argument('--variants', nargs='+', default=['baseline', 'immediate', 'cue_emphasis'])
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.check:
        check(args.name, args.variants)
        return
    run = ROOT / 'g3/data/jobs' / args.job / 'attempts/1/g2-run'
    props = run / 'artifacts/render-props.json'
    for variant in args.variants:
        out = BASE / 'experiments' / args.name / variant
        out.mkdir(parents=True, exist_ok=False)
        if variant == 'production':
            # Extract the actual workflow output, never render an injected plan
            # and call it the production side of a comparison.
            shutil.copy2(props, out / 'props.json')
            video = run / 'artifacts/G2_自动生成.mp4'
            save(out / 'source.json', {'job': args.job, 'source_video': str(video),
                'props_sha256': hashlib.sha256(props.read_bytes()).hexdigest(), 'variant': variant,
                'start_frame': args.start_frame, 'end_frame_exclusive': args.end_frame,
                'manual_plan_script_audio_changes': False, 'type': 'actual_workflow_output_excerpt'})
            command = [str(ROOT / 'g0/darwin_arm64/ffmpeg'), '-v', 'error', '-y',
                '-ss', str(args.start_frame / 30), '-i', str(video),
                '-t', str((args.end_frame - args.start_frame) / 30),
                '-vf', 'scale=960:540', '-c:v', 'libx264', '-preset', 'fast', '-crf', '20',
                '-c:a', 'aac', str(out / 'video.mp4')]
            save(out / 'command.json', command)
            with (out / 'render.log').open('w') as log:
                subprocess.run(command, stdout=log, stderr=log, check=True)
            print(f'{args.name}/{variant} extracted from actual workflow', flush=True)
            continue
        renderer = out / 'renderer'
        shutil.copytree(BASE / 'baseline-renderer/src', renderer / 'src')
        deps = ROOT / 'g0/vendor/remotion-audiogram/node_modules'
        (renderer / 'node_modules').symlink_to(deps, target_is_directory=True)
        root = renderer / 'src/Root.tsx'
        source = root.read_text()
        assert source.count(OLD) == 1
        if variant in {'immediate', 'cue_emphasis'}:
            source = source.replace(OLD, 'opacity:ease((frame-scene.start_frame)/12),...textStyle,fontSize:p.value.length')
        if variant == 'cue_emphasis':
            old_color = 'letterSpacing:-2,color:paper.green}}><Tech text={p.value}'
            assert source.count(old_color) == 1
            source = source.replace(old_color, "letterSpacing:-2,color:frame>=(reveals.find(b=>b.target_id==='line:0')?.frame??scene.start_frame)?paper.green:paper.ink}}><Tech text={p.value}")
        assert variant in {'baseline', 'immediate', 'cue_emphasis'}
        root.write_text(source)
        shutil.copy2(props, out / 'props.json')
        save(out / 'source.json', {'job': args.job, 'source_video': str(run / 'artifacts/G2_自动生成.mp4'),
            'props_sha256': hashlib.sha256(props.read_bytes()).hexdigest(), 'variant': variant,
            'start_frame': args.start_frame, 'end_frame_exclusive': args.end_frame,
            'manual_plan_script_audio_changes': False, 'type': 'expression_experiment_not_e2e'})
        command = ['node', str(deps / '@remotion/cli/remotion-cli.js'), 'render', 'src/index.ts',
            'G1CommunicationNoise', str(out / 'video.mp4'), '--codec=h264', '--crf=20', '--scale=0.5',
            '--concurrency=2', f'--props={out / "props.json"}', f'--public-dir={run / "fixtures"}',
            '--browser-executable=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
            '--bundle-cache=false', f'--frames={args.start_frame}-{args.end_frame - 1}']
        save(out / 'command.json', command)
        with (out / 'render.log').open('w') as log:
            subprocess.run(command, cwd=renderer, stdout=log, stderr=log, check=True)
        print(f'{args.name}/{variant} rendered', flush=True)


if __name__ == '__main__':
    main()

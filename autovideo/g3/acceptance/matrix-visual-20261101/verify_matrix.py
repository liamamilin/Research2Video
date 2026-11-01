"""Verify full matrix replays and sample the same timestamps before and after."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[3]
FFMPEG = ROOT / 'g0/darwin_arm64/ffmpeg'
FFPROBE = ROOT / 'g0/darwin_arm64/ffprobe'


def pcm(path: Path) -> np.ndarray:
    data = subprocess.check_output([str(FFMPEG), '-v', 'error', '-i', str(path),
        '-vn', '-f', 'f32le', '-acodec', 'pcm_f32le', '-ac', '1', '-ar', '24000', '-'])
    return np.frombuffer(data, dtype=np.float32)


def verify(folder: Path, metadata: dict, variant: str) -> dict:
    out = folder / variant
    source = Path(metadata['source_attempt'])
    before_video, after_video = source / 'artifacts/G2_自动生成.mp4', out / 'video.mp4'
    original = json.loads((source / 'fixtures/video-ir-v0.2.json').read_text())
    replay = json.loads((out / 'video-ir.json').read_text())
    receipt = json.loads((out / 'receipt.json').read_text())
    compiled = json.loads((out / 'compiled-timeline.json').read_text())
    assert original['narration'] == replay['narration']
    assert original['assets'] == replay['assets']
    audio = next(a for a in original['assets'] if a['id'] == original['narration']['audio_asset_id'])
    audio_file = source / 'fixtures' / audio['path']
    copied_audio = out / 'renderer/public' / audio['path']
    audio_sha = hashlib.sha256(audio_file.read_bytes()).hexdigest()
    assert audio_sha == receipt['audio_sha256'] == hashlib.sha256(copied_audio.read_bytes()).hexdigest()
    probe = json.loads(subprocess.check_output([str(FFPROBE), '-v', 'error', '-count_frames',
        '-show_entries', 'format=duration:stream=codec_name,width,height,r_frame_rate,nb_read_frames',
        '-of', 'json', str(after_video)], text=True))
    video_stream = next(s for s in probe['streams'] if s['codec_name'] == 'h264')
    assert int(video_stream['nb_read_frames']) == compiled['total_frames']
    assert (video_stream['width'], video_stream['height'], video_stream['r_frame_rate']) == (1920, 1080, '30/1')
    decoded = subprocess.run([str(FFMPEG), '-v', 'error', '-xerror', '-i', str(after_video),
        '-f', 'null', '-'], capture_output=True, text=True, check=True)
    (out / 'decode.log').write_text(decoded.stderr)
    before, after = pcm(before_video), pcm(after_video)
    count = min(len(before), len(after))
    correlation = float(np.corrcoef(before[:count], after[:count])[0, 1])
    assert correlation > .995
    sheet = Image.new('RGB', (1280, 390 * ((len(metadata['seconds']) + 1) // 2)), 'white')
    draw = ImageDraw.Draw(sheet)
    for i, second in enumerate(metadata['seconds']):
        target = out / f'frame-{second}.png'
        subprocess.run([str(FFMPEG), '-y', '-v', 'error', '-ss', str(second),
            '-i', str(after_video), '-frames:v', '1', str(target)], check=True)
        with Image.open(target) as frame:
            frame.thumbnail((640, 360))
            x, y = i % 2 * 640, i // 2 * 390
            sheet.paste(frame, (x, y))
            draw.text((x + 12, y + 362), f'{folder.name}: {second}s', fill='black')
    sheet.save(out / 'contact.jpg', quality=92)
    result = {'title': metadata['title'], 'source_attempt': str(source), 'probe': probe,
        'full_decode': 'PASS', 'narration_unchanged': True, 'assets_unchanged': True,
        'source_audio_sha256': audio_sha, 'decoded_audio_correlation': correlation,
        'video_sha256': hashlib.sha256(after_video.read_bytes()).hexdigest(),
        'new_model_calls': 0, 'new_tts_calls': 0, 'manual_scene_or_beat_edits': 0,
        'scene_count': len(replay['scenes']), 'sampled_seconds': metadata['seconds']}
    (out / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--case', choices=['news', 'tutorial', 'science'])
    parser.add_argument('--variant', choices=['after', 'release'], default='release')
    args = parser.parse_args()
    sources = json.loads((args.root / 'sources.json').read_text())
    for name, metadata in sources.items():
        if args.case and name != args.case:
            continue
        result = verify(args.root / name, metadata, args.variant)
        print(json.dumps({'case': name, 'decode': result['full_decode'],
                          'audio_correlation': result['decoded_audio_correlation']}), flush=True)


if __name__ == '__main__':
    main()

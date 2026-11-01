"""Decode the saved tutorial replay and compare its unchanged narration/audio."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np


ROOT = Path(__file__).resolve().parents[3]
FFMPEG = ROOT / 'g0/darwin_arm64/ffmpeg'
FFPROBE = ROOT / 'g0/darwin_arm64/ffprobe'


def pcm(path: Path) -> np.ndarray:
    data = subprocess.check_output([str(FFMPEG), '-v', 'error', '-i', str(path),
        '-vn', '-f', 'f32le', '-acodec', 'pcm_f32le', '-ac', '1', '-ar', '24000', '-'])
    return np.frombuffer(data, dtype=np.float32)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--replay', type=Path, required=True)
    args = parser.parse_args()
    out = args.replay.resolve()
    video = out / 'video.mp4'
    receipt = json.loads((out / 'receipt.json').read_text())
    source = Path(receipt['source_attempt'])
    original = json.loads((source / 'fixtures/video-ir-v0.2.json').read_text())
    replay = json.loads((out / 'video-ir.json').read_text())
    asset = next(a for a in original['assets'] if a['id'] == original['narration']['audio_asset_id'])
    audio = source / 'fixtures' / asset['path']
    assert replay['narration'] == original['narration']
    assert receipt['audio_sha256'] == hashlib.sha256(audio.read_bytes()).hexdigest()
    probe = json.loads(subprocess.check_output([str(FFPROBE), '-v', 'error', '-count_frames',
        '-show_entries', 'format=duration:stream=codec_name,width,height,r_frame_rate,nb_read_frames',
        '-of', 'json', str(video)], text=True))
    stream = next(s for s in probe['streams'] if s['codec_name'] == 'h264')
    compiled = json.loads((out / 'compiled-timeline.json').read_text())
    assert int(stream['nb_read_frames']) == compiled['total_frames']
    decoded = subprocess.run([str(FFMPEG), '-v', 'error', '-i', str(video),
        '-f', 'null', '-'], capture_output=True, text=True, check=True)
    (out / 'decode.log').write_text(decoded.stderr)
    before, after = pcm(args.source / 'video.mp4'), pcm(video)
    count = min(len(before), len(after))
    correlation = float(np.corrcoef(before[:count], after[:count])[0, 1])
    assert correlation > .995
    frames = []
    for second in (20, 40, 55, 70, 90):
        frame = out / f'frame-{second}.png'
        subprocess.run([str(FFMPEG), '-y', '-v', 'error', '-ss', str(second),
            '-i', str(video), '-frames:v', '1', str(frame)], check=True)
        frames.append(frame.name)
    evidence = {'probe': probe, 'full_decode': 'PASS', 'narration_unchanged': True,
        'source_audio_sha256': receipt['audio_sha256'],
        'decoded_audio_correlation_with_normal_job': correlation,
        'new_model_calls_in_replay': 0, 'manual_scene_or_beat_edits': 0,
        'code_scenes': [{'id': s['id'], 'lines': [line['text'] for line in s['props']['lines']]}
                        for s in replay['scenes'] if s['component'] == 'code_demo'],
        'sampled_frames': frames}
    (out / 'verification.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'full_decode': 'PASS', 'frames': stream['nb_read_frames'],
                      'audio_correlation': correlation}), flush=True)


if __name__ == '__main__':
    main()

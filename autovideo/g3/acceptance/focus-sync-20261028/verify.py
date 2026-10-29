"""Media and production-path evidence for the fixed clips and fresh workflow."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
FFMPEG = ROOT/'g0/darwin_arm64/ffmpeg'
FFPROBE = ROOT/'g0/darwin_arm64/ffprobe'


def media(path):
    decoded = subprocess.run([str(FFMPEG), '-nostdin', '-v', 'error', '-xerror',
        '-i', str(path), '-f', 'null', '-'], capture_output=True, text=True)
    assert decoded.returncode == 0 and not decoded.stderr, decoded.stderr
    probe = json.loads(subprocess.check_output([str(FFPROBE), '-v', 'error', '-show_entries',
        'format=duration,size:stream=codec_type,codec_name,width,height', '-of', 'json', str(path)]))
    return {**probe, 'decode_exit_code': decoded.returncode, 'decode_stderr': decoded.stderr,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workflow', type=Path, required=True)
    args = parser.parse_args()
    out = args.workflow.resolve()
    base = ROOT/'g3/data/acceptance-focus-sync-20261028'
    location = json.loads((out/'location.json').read_text())
    attempt = Path(location['engine_data'])/'jobs'/location['job_id']/'attempts/1/g2-run'
    ir = json.loads((out/'video-ir.json').read_text())
    payload = json.loads((out/'input.json').read_text())
    expected = ''.join(p for c in payload['chapters'] for p in c['paragraphs'])
    actual = ''.join(row['text'] for row in ir['narration']['segments'])
    assert ''.join(expected.split()) == ''.join(actual.split()), 'Author text changed'
    cost = json.loads((attempt/'artifacts/model-cost.json').read_text())
    assert cost['calls'], 'No real model planning recorded'
    shutil.copyfile(attempt/'artifacts/model-cost.json', out/'model-cost.json')
    renderer = attempt/'renderer/src'
    renderer_matches = {}
    for name in ['Root.tsx', 'ShotDirector.tsx', 'ScatterChart.tsx', 'DataChart.tsx']:
        current = (ROOT/'g1/renderer/src'/name).read_text()
        if name == 'Root.tsx':
            current = current.replace('AUTOVIDEO　/　G1 · Video IR 0.2', 'AUTOVIDEO　/　G2 · 真实模型 / Video IR 0.2')
            current = current.replace('G1CommunicationNoise', 'G2CommunicationNoise')
        renderer_matches[name] = (renderer/name).read_text() == current
    assert all(renderer_matches.values()), renderer_matches
    frozen = json.loads((base/'source-freeze.json').read_text())
    assert all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest() == sha for name, sha in frozen.items())
    execution = json.loads((out/'production-execution.json').read_text())
    assert execution['executor'] == 'hybrid_cards'
    result = {'job_id': location['job_id'], 'unchanged_author_text': True, 'real_model_calls': len(cost['calls']),
        'model': cost['model'], 'renderer_matches_current_source': renderer_matches,
        'source_unchanged_during_validation': True, 'video_speed': payload['video_speed'],
        'production_executor': execution['executor'], 'workflow_media': {name: media(out/name) for name in ['content.mp4', 'video.mp4']},
        'replays': {name: media(base/name/'after.mp4') for name in ['replay-models-final', 'replay-business-final']}}
    (base/'delivery.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'job_id': result['job_id'], 'real_model_calls': result['real_model_calls'],
        'content_seconds': result['workflow_media']['content.mp4']['format']['duration'],
        'decode': 'all passed', 'source_matches': True}), flush=True)


if __name__ == '__main__':
    main()

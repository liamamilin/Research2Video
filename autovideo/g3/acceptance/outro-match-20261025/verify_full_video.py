"""Repackage the screenshot's exact content locally; retain all existing outputs."""
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path('/Users/milin/2026/软件开发/视频workflow')
AUTO = ROOT / 'autovideo'
sys.path.insert(0, str(AUTO))
from g2 import package_stage
from g2.packaging_library import resolve
from g2.packaging_library.templates import visual_theme

EVIDENCE = AUTO / 'g3/acceptance/outro-match-20261025'
ORIGINAL = ROOT / 'AI_research/Base_CodingCLi/state/video_renders/cda392edba3e4c9490a465f0e78ad6f0'
RUN = AUTO / 'g3/data/jobs/9de6e93d-47cb-40c8-9a7e-a9a6b8b1f3ce/attempts/1/g2-run'
OUT = EVIDENCE / 'corrected'
OUT.mkdir(exist_ok=False)
FFMPEG, FFPROBE = AUTO / 'g0/darwin_arm64/ffmpeg', AUTO / 'g0/darwin_arm64/ffprobe'

def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

inputs = [ORIGINAL / 'video.mp4', ORIGINAL / 'content.mp4', ORIGINAL / 'captions.srt', ORIGINAL / 'package.json',
          RUN / 'fixtures/assets/communications_noise.wav']
original_hashes = {str(path): sha(path) for path in inputs}
old = json.loads((ORIGINAL / 'package.json').read_text())
new = resolve(old['packaging']['selection'])
assert new['package'] == old['config'], 'Only renderer version may change; preserve all configured text, duration and sound'
ir = json.loads((ORIGINAL / 'video-ir.json').read_text())
plan = json.loads((ORIGINAL / 'production-plan.json').read_text())
theme = visual_theme(ir, plan['style'])
for src, dest in [('content-captions.srt', 'captions.srt'), ('content-chapters.json', 'chapters.json')]:
    shutil.copy2(ORIGINAL / src, OUT / dest)
log = EVIDENCE / 'commands.log'

def runner(label, argv, timeout=900):
    start = time.monotonic()
    with log.open('a') as f:
        f.write(json.dumps({'label': label, 'argv': [str(x) for x in argv]}, ensure_ascii=False) + '\n')
    try:
        result = package_stage.default_runner(label, argv, timeout)
    except Exception as e:
        with log.open('a') as f: f.write(f'ERROR {label}: {e}\n')
        raise
    with log.open('a') as f: f.write(json.dumps({'label': label, 'elapsed_s': round(time.monotonic()-start, 3), 'result': 'PASS'}) + '\n')
    return result

start = time.monotonic()
record = package_stage.build_package(runner, FFMPEG, FFPROBE, config=new['package'], content_video=ORIGINAL/'content.mp4',
    voice_wav=RUN/'fixtures/assets/communications_noise.wav', artifacts=OUT, packaging_record=new, visual_theme=theme)
assert record['total_frames'] == old['total_frames']
assert record['config'] == old['config']
assert (OUT/'package/captions.srt').read_bytes() == (ORIGINAL/'captions.srt').read_bytes()
assert {str(path): sha(path) for path in inputs} == original_hashes
for name, at in [('after-outro',184.5), ('after-last-content',182.5)]:
    runner(name, [FFMPEG,'-nostdin','-v','error','-y','-ss',str(at),'-i',OUT/'package/video.mp4','-frames:v','1',EVIDENCE/f'{name}.png'])
proof = {'status': 'PASS', 'source_render_id': 'cda392edba3e4c9490a465f0e78ad6f0',
    'title': ir['video']['title'], 'visual_theme': theme, 'elapsed_seconds': round(time.monotonic()-start,2),
    'original_hashes_preserved': original_hashes, 'settings_unchanged': True, 'caption_timeline_identical': True,
    'total_frames_unchanged': True, 'model_and_tts_calls': 0, 'checks': record['checks'],
    'video': str(OUT/'package/video.mp4'), 'old_component_versions': old['packaging']['component_versions'],
    'new_component_versions': new['component_versions'], 'manual_visual_review': 'PENDING'}
(EVIDENCE/'verification.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'status': 'PASS','seconds':record['total_seconds'],'checks':record['checks'],'video':proof['video']},ensure_ascii=False))

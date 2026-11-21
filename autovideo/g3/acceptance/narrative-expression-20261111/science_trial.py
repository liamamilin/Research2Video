"""Local expression study; staged version is authored here, not model-generated."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import wave

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT/'g3/data/validation/narrative-expression-20261111'
sys.path.insert(0, str(ROOT))
from g1.video_ir import validate_video_ir
from g2 import explanation, run_g2

parser = argparse.ArgumentParser()
parser.add_argument('--variant', choices=['before', 'large_diagram', 'stages'], required=True)
args = parser.parse_args()
record = next(r for r in json.loads((BASE/'cases.json').read_text()) if r['name'] == 'science')
source = Path(record['props'])
original = json.loads(source.read_text())
fixtures = source.parents[1]/'fixtures'
raw = json.loads((fixtures/'video-ir-v0.2.json').read_text())
chapter = [s for s in raw['scenes'] if s['props']['chapter_index'] == 2]
start = chapter[0]['start_ms']
frames = round((chapter[-1]['start_ms'] + chapter[-1]['duration_ms'] - start)*.03)
duration = round(frames*1000/30)
ids = [sid for s in chapter for sid in s['narration_segment_ids']]
out = BASE/'science-study'/args.variant
out.mkdir(parents=True, exist_ok=False)
public = out/'fixtures'
assets = public/'assets'
assets.mkdir(parents=True)
shutil.copytree(fixtures/'assets/slideshow', assets/'slideshow')
with wave.open(str(fixtures/'assets/communications_noise.wav'), 'rb') as voice:
    params = voice.getparams()
    voice.setpos(round(start*params.framerate/1000))
    pcm = voice.readframes(round(frames*params.framerate/30))
with wave.open(str(assets/'communications_noise.wav'), 'wb') as voice:
    voice.setparams(params)
    voice.writeframes(pcm)
ir = copy.deepcopy(raw)
ir.pop('content_structure', None)
ir.pop('playback', None)  # Clip the already-retimed PCM; no second speed transform.
ir['video']['duration_ms'] = duration
ir['narration']['segments'] = [{**r, 'start_ms': r['start_ms']-start, 'end_ms': r['end_ms']-start}
                             for r in raw['narration']['segments'] if r['id'] in ids]
ir['narration']['text'] = ''.join(r['text'] for r in ir['narration']['segments'])
for a in ir['assets']:
    if a['id'] == ir['narration']['audio_asset_id']:
        a['sha256'] = hashlib.sha256((assets/a['path'].split('/')[-1]).read_bytes()).hexdigest()
ir['scenes'] = copy.deepcopy(chapter)
for s in ir['scenes']:
    s['start_ms'] -= start
ir['scenes'][-1]['duration_ms'] = duration-ir['scenes'][-1]['start_ms']
if args.variant == 'large_diagram':
    for s in ir['scenes']:
        s['props']['layout'] = 'diagram'
if args.variant == 'stages':
    def visual(target, icon, phrase):
        return {'target': target, 'icon': icon, 'reveal_phrase': phrase}
    pages = [
        {'id': 'study_1', 'component': 'comparison', 'headline': '多数物质凝固变紧，水不这样', 'props': {
            'left': {'label': '多数物质', 'items': ['分子排得更紧', '密度变大']},
            'right': {'label': '水', 'items': ['结冰反而变松', '接着看架子里的空腔']},
            'visuals': [visual('side:left', 'object', '大多数物质从液体变成固体'), visual('side:right', 'water', '水不这样')] }},
        {'id': 'study_2', 'component': 'flow_diagram', 'headline': '架子中间留出空腔', 'props': {
            'title': '氢键搭架子，架子中间留空腔', 'nodes': [{'id': 'frame', 'label': '六角形架子'}, {'id': 'space', 'label': '中间留空腔'}],
            'edges': [{'id': 'e1', 'from': 'frame', 'to': 'space', 'label': '留下', 'relation': 'flow'}],
            'paths': [], 'takeaway': '水结冰时，分子靠氢键搭架子',
            'visuals': [visual('node:frame', 'object', '分子靠氢键搭成一个六角形的架子'), visual('node:space', 'object', '架子中间留出空腔')]}},
        {'id': 'study_3', 'component': 'comparison', 'headline': '同样多的水结冰后：体积变大，密度变小', 'props': {
            'left': {'label': '水', 'items': ['每立方厘米约 1 克']},
            'right': {'label': '冰', 'items': ['体积胀大约 9%', '每立方厘米约 0.9 克']},
            'visuals': [visual('side:left', 'water', '而水是一克'), visual('side:right', 'object', '体积反而胀大约百分之九')]}},
        {'id': 'study_4', 'component': 'flow_diagram', 'headline': '把它想成同样重量的积木', 'props': {
            'title': '重量没变，占的地方大了', 'nodes': [{'id': 'weight', 'label': '重量没变'}, {'id': 'volume', 'label': '占更大体积'}, {'id': 'density', 'label': '密度更小'}],
            'edges': [{'id': 'e1', 'from': 'weight', 'to': 'volume', 'label': '排得松', 'relation': 'flow'}, {'id': 'e2', 'from': 'volume', 'to': 'density', 'label': '于是', 'relation': 'flow'}],
            'paths': [], 'takeaway': '同样重量，放得更松，密度就小',
            'visuals': [visual('node:weight', 'object', '同样重量'), visual('node:volume', 'object', '占的地方大了'), visual('node:density', 'object', '密度就小了')] }},
    ]
    # Vocabulary only, not new facts or a new renderer.
    for page in pages:
        for v in page['props'].get('visuals', []):
            v['icon'] = 'result'  # Trusted neutral glyph; objects are named by original text.
    rows = {r['id']: r for r in ir['narration']['segments']}
    parts = [ids[:3], ids[3:4], ids[4:6], ids[6:]]
    groups = [{'id': p['id'], 'title': p['headline'], 'chapter_id': 'c3', 'image_indices': [],
               'segment_ids': part, 'text': ''.join(rows[sid]['text'] for sid in part)} for p, part in zip(pages, parts)]
    artifacts = out/'artifacts';artifacts.mkdir()
    def write(path, data):
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    def command(label, argv, cwd, timeout=60):
        result = subprocess.run(list(map(str, argv)), cwd=cwd, capture_output=True, text=True, timeout=timeout, check=True)
        (out/f'{label}.log').write_text(result.stdout+result.stderr)
        return result.stdout
    pipeline = SimpleNamespace(ARTIFACTS=artifacts, PROJECT=ROOT, NODE='node', write_json=write, _run_fixed_command=command)
    plan = {'pages': pages, 'groups': groups}
    write(out/'manually-authored-study-plan.json', plan)
    explanation.validate({'pages': pages}, {'groups': groups})
    alignment = {'rows': ir['narration']['segments'], 'duration_ms': duration, 'audio_path': assets/'communications_noise.wav'}
    board = explanation.compile_storyboard(pipeline, plan, alignment, {}, [])
    run_g2.PROJECT_INPUT = source.parents[4]/'input/source.txt'
    run_g2.CONTENT_MODE = False
    compiled = run_g2.compile_ir({'title': ir['video']['title'], 'segments': ir['narration']['segments']}, board, alignment)
    ir['scenes'] = compiled['scenes']
    ir['presentation'] = {'preset': 'explanation'}
props = {**original, 'ir': {**validate_video_ir(ir, public), 'audio_public_file': 'assets/communications_noise.wav'}}
(out/'video-ir.json').write_text(json.dumps(ir, ensure_ascii=False, indent=2))
(out/'props.json').write_text(json.dumps(props, ensure_ascii=False))
(out/'source.json').write_text(json.dumps({'source': record, 'start_ms': start, 'duration_ms': duration,
    'study_only': args.variant != 'before', 'manual_stage_plan': args.variant == 'stages',
    'pcm_sha256': hashlib.sha256(pcm).hexdigest()}, ensure_ascii=False, indent=2))
renderer = out/'renderer'
shutil.copytree(BASE/'baseline-renderer/src', renderer/'src')
(renderer/'node_modules').symlink_to(ROOT/'g0/vendor/remotion-audiogram/node_modules', target_is_directory=True)
cli = ROOT/'g0/vendor/remotion-audiogram/node_modules/@remotion/cli/remotion-cli.js'
argv = ['node', str(cli), 'render', 'src/index.ts', 'G1CommunicationNoise', str(out/'video.mp4'), '--codec=h264', '--crf=20',
        '--concurrency=3', f'--props={out/"props.json"}', f'--public-dir={public}',
        '--browser-executable=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '--bundle-cache=false']
(out/'command.json').write_text(json.dumps(argv))
print(out, flush=True)

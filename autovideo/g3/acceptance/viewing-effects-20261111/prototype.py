"""Isolated same-content experiments. No production settings or original outputs changed."""
import argparse
import copy
import difflib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from g2.caption_alignment import word_spans

BASE = ROOT / 'g3/data/validation/viewing-effects-20261111'
parser = argparse.ArgumentParser()
parser.add_argument('--case', required=True)
parser.add_argument('--scene', required=True)
parser.add_argument('--method', choices=['larger', 'number', 'wide', 'focus', 'focus_exact', 'readable'], required=True)
parser.add_argument('--frame', type=int)
parser.add_argument('--video', action='store_true')
args = parser.parse_args()
record = next(r for r in json.loads((BASE / 'baseline.json').read_text()) if r['name'] == args.case)
original = Path(record['props'])
fixtures = original.parents[1] / 'fixtures'
out = BASE / f'{args.case}-{args.scene}-{args.method}{"-video" if args.video else ""}'
out.mkdir(exist_ok=False)
props = json.loads(original.read_text())
ir = props['ir']
scene = next(s for s in ir['scenes'] if s['id'] == args.scene)
renderer = out / 'renderer'
shutil.copytree(BASE / 'baseline-renderer/src', renderer / 'src')
(renderer / 'node_modules').symlink_to(ROOT / 'g0/vendor/remotion-audiogram/node_modules', target_is_directory=True)
rows = {r['id']: r for r in ir['narration']['segments']}
cp = json.loads((original.parent / 'caption-alignment.json').read_text())
words = {r['segment_id']: r['items'] for r in cp['word_evidence']}


def anchor(row, character):
    spans = word_spans(row['text'])
    items = words[row['id']]
    assert [s[0] for s in spans] == [i['text'] for i in items]
    i = next(i for i, (_, a, b) in enumerate(spans) if a <= character < b or a >= character)
    return round((row['start_ms'] + items[i]['start_ms']) * .03)


if args.method == 'larger':
    path = renderer / 'src/TextFit.ts'
    path.write_text(path.read_text().replace('body=34*scale', 'body=46*scale').replace('label=42*scale', 'label=48*scale'))
elif args.method == 'number':
    assert args.case == 'news' and args.scene == 'card_3'  # Explicit authored prototype, never a production case rule.
    scene['component'] = 'price_change'
    scene['props'] = {'title': '同样每天 50 万输出 token，月账单翻倍', 'unit': '美元 / 月',
                      'rows': [{'label': '同一个脚本', 'before': '150', 'after': '300'}], 'staged': True}
    before, after = rows['c1_n8'], rows['c1_n9']
    scene['beats'] = [{'id': 'title', 'action': 'reveal', 'target_id': 'title', 'frame': scene['start_frame'], 'order': 0},
                      {'id': 'before', 'action': 'reveal', 'target_id': 'line:0', 'frame': anchor(before, before['text'].index('150')), 'order': 1},
                      {'id': 'after', 'action': 'reveal', 'target_id': 'line:1', 'frame': anchor(after, after['text'].index('300')), 'order': 2}]
elif args.method == 'readable':
    path = renderer / 'src/Root.tsx'
    path.write_text(path.read_text().replace('.18+.82*', '.65+.35*'))
elif args.method == 'wide':
    path = renderer / 'src/DirectedMaterials.tsx'
    path.write_text(path.read_text().replace('const detailed=p.images.some((image:any)=>image.focus?.regions?.length);', 'const detailed=true;'))
else:
    binary = ROOT.parent / 'AI_research/Base_CodingCLi/state/visual_tools/screenshot-ocr-7666729d4897ce29'
    evidence, events = [], []
    for image_index, image in enumerate(scene['props']['images']):
        path = fixtures / image['image_path']
        lines = json.loads(subprocess.check_output([str(binary), str(path)], timeout=30))
        width, height = Image.open(path).size
        regions = []
        for sid in scene['narration_segment_ids']:
            row = rows[sid]
            candidates = []
            for line in lines:
                match = difflib.SequenceMatcher(None, row['text'], line['text'], autojunk=False).find_longest_match()
                if (match.size >= 4 and line['confidence'] >= .6) if args.method == 'focus' else (match.size >= 2 and match.size >= len(line['text'].strip('• ·')) and row['text'].count(line['text'].strip('• ·')) == 1):
                    candidates.append((match.size, match.a, line))
            if not candidates:
                continue
            selected = [max(candidates, key=lambda c: c[0])] if args.method == 'focus' else sorted(candidates, key=lambda c: c[1])
            for length, character, line in selected:
                x1, y1 = line['x'] * 1000, (1 - line['y'] - line['height']) * 1000
                x2, y2 = x1 + line['width'] * 1000, y1 + line['height'] * 1000
                region = {'x1': max(0, round(x1 - 30)), 'y1': max(0, round(y1 - 35)),
                          'x2': min(1000, round(x2 + 30)), 'y2': min(1000, round(y2 + 35)), 'label': line['text'][:32], 'style': 'box'}
                if region not in regions:
                    regions.append(region)
                target = f'region:{image_index}:{regions.index(region)}'
                frame = anchor(row, character)
                events.append((frame, target))
                evidence.append({'segment': sid, 'quote': row['text'][character:character + length], 'ocr': line, 'target': target, 'frame': frame})
        if regions:
            image['focus'] = {'width': width, 'height': height, 'regions': regions}
    if events:
        cues = []
        for i, (frame, target) in enumerate(sorted(set(events))):
            key = f'focus_{i}'
            scene['beats'].append({'id': key, 'action': 'reveal', 'target_id': 'scene', 'frame': frame, 'order': len(scene['beats'])})
            cues.append({'beat_id': key, 'targets': [target]})
        scene['props']['shot_plan'] = {'version': 1, 'profile': scene['props']['shot_plan']['profile'], 'cues': cues, 'closing': 'hold'}
    (out / 'focus-evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    print('Measured focus anchors:', len(evidence), flush=True)

(out / 'props.json').write_text(json.dumps(props, ensure_ascii=False))
cli = ROOT / 'g0/vendor/remotion-audiogram/node_modules/@remotion/cli/remotion-cli.js'
common = [f'--props={out / "props.json"}', f'--public-dir={fixtures}', '--browser-executable=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '--bundle-cache=false']
if args.video:
    command = ['node', str(cli), 'render', 'src/index.ts', 'G1CommunicationNoise', str(out / 'after.mp4'),
               f'--frames={scene["start_frame"]}-{scene["end_frame"] - 1}', '--codec=h264', '--crf=20', '--concurrency=3', *common]
else:
    frame = args.frame if args.frame is not None else (scene['start_frame'] + scene['end_frame']) // 2
    command = ['node', str(cli), 'still', 'src/index.ts', 'G1CommunicationNoise', str(out / 'after.png'), f'--frame={frame}', *common]
(out / 'command.json').write_text(json.dumps(command))
with (out / 'render.log').open('w') as log:
    subprocess.run(command, cwd=renderer, stdout=log, stderr=log, check=True, timeout=600)
if args.video:
    ff = ROOT / 'g0/darwin_arm64/ffmpeg'
    with (out / 'decode.log').open('w') as log:
        subprocess.run([str(ff), '-v', 'error', '-xerror', '-i', str(out / 'after.mp4'), '-f', 'null', '-'], stdout=log, stderr=log, check=True)
    subprocess.run([str(ff), '-v', 'error', '-ss', str(scene['start_frame'] / 30), '-i', record['video'],
                    '-t', str((scene['end_frame'] - scene['start_frame']) / 30), '-c:v', 'libx264', '-crf', '20', '-c:a', 'aac', str(out / 'before.mp4')], check=True)
print(str(out))

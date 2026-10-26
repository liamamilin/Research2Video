"""Use existing material registration and one normal model planning pass for a mixed video."""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient
from g3.app import create_app
from g3.config import Settings

parser = argparse.ArgumentParser()
parser.add_argument('--fixtures', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--case', choices=['flash','tutorial','deep'], required=True)
args = parser.parse_args()
fixtures, out = args.fixtures.resolve(), args.output.resolve()
out.mkdir(parents=True, exist_ok=False)
data = ROOT/'g3/data/acceptance-story-direction-v4'/uuid4().hex
data.mkdir(parents=True)
shutil.copyfile(ROOT/'g3/data/llm-settings.json', data/'llm-settings.json')
pages = json.loads((fixtures/'pages.json').read_text())
cases = {
 'flash': {'title': '本地方案和云端方案如何选择', 'recipe': 'ai_news', 'paragraphs': [
  '本地方案和云端方案如何选择？先认识它们的使用页面，再把成本与响应时间放在一起看。',
  '先看本地方案，页面上同时列出单次成本和响应时间。再看云端方案，相同位置展示相同指标。两张页面并排放，先认识这两个对象。',
  '把成本和响应时间放在同一张图里比较。本地方案单次成本20元，响应时间8秒。云端方案单次成本25元，响应时间3秒。本地方案更省，云端方案更快。',
  '最后回顾两个重点：页面让我们认识本地方案和云端方案，比较图让我们看清成本与速度的取舍。重视成本看本地方案，重视响应看云端方案。']},
 'tutorial': {'title': '沿着两个方案走完处理流程', 'recipe': 'how_to', 'paragraphs': [
  '同一份材料，怎样分别用本地方案和云端方案处理？我们先看页面，再沿着上传、处理和结果这条路径走一遍。',
  '先打开本地方案的页面，处理流程放在指标下方。再打开云端方案，相同位置也列出了处理流程和开始使用按钮。两个界面保持相同的阅读顺序。',
  '操作流程从上传材料开始，文件可以交给本地方案，也可以交给云端方案。两个方案各自开始处理，最后都返回结果。把这条分支流程连起来，就能看清材料怎样到达结果。',
  '再比较使用方式。本地方案在当前设备处理材料，适合围绕同一台设备操作。云端方案通过网络处理材料，适合从不同设备发起任务。两种方式都从材料出发，区别在处理位置。',
  '最后回顾，本地方案和云端方案共享上传材料、开始处理、查看结果这条路径。流程图帮助跟上操作顺序，对比画面帮助记住处理位置的差别。']},
 'deep': {'title': '把方案选择展开成一次完整比较', 'recipe': 'science_explainer', 'paragraphs': [
  '为什么本地方案和云端方案不能只看一个数字？我们先认识产品页面，再比较连续使用的成本，最后把成本和响应时间放在一起。',
  '先认识两张产品页面。本地方案把成本、响应时间和处理流程放在同一页，云端方案也用相同的栏目展示。讲解中的两个方案，始终对应这两张页面。',
  '再比较三轮处理成本。第一轮本地方案20元，云端方案25元。第二轮本地方案22元，云端方案28元。第三轮本地方案24元，云端方案30元。本地方案在这三轮中的成本都更低。',
  '还要把响应时间放进来。以第一轮为例，本地方案成本20元、响应时间8秒，云端方案成本25元、响应时间3秒。放在二维图上看，本地方案更省，云端方案更快，两个维度表达的是不同的取舍。',
  '最后回顾这次比较：本地方案在三轮处理中成本更低，云端方案在第一轮响应更快。把成本比较图和二维关系图一起看，再根据当前任务更重视的维度选择。']},
}
case=cases[args.case]
paragraphs=case['paragraphs']
with TestClient(create_app(Settings(data_dir=data, runner_timeout_seconds=1200), start_worker=True)) as engine:
    materials = []
    for name, caption, role, placements, targets in [
        ('local', '本地方案', 'comparison', [1], [('metrics', '成本和响应时间'), ('workflow', '处理流程')]),
        ('cloud', '云端方案', 'comparison', [1], [('metrics', '成本和响应时间'), ('workflow', '处理流程')]),
    ]:
        page = pages[name]
        response = engine.post('/api/creative-images', files={'file': (page['file'], (fixtures/page['file']).read_bytes(), 'image/png')})
        assert response.status_code == 201, response.text
        materials.append({'image_id': response.json()['image_id'], 'caption': caption, 'role': role,
            'placements': [{'chapter_index': 0, 'paragraph_index': p} for p in placements],
            'focus': {'width': page['width'], 'height': page['height'],
                      'regions': [{**page['regions'][key], 'label': label, 'style': 'box'} for key, label in targets]}})
    payload = {'title': case['title'], 'input_mode': 'narration', 'duration_mode': 'content',
        'chapters': [{'title': case['title'], 'paragraphs': paragraphs}], 'video_form': 'object_showcase',
        'visual_storytelling': True, 'voice_id': 'zf_xiaoxiao', 'use_draw_things': False,
        'production_recipe': case['recipe'], 'image_materials': materials}
    response = engine.post('/api/jobs', json=payload)
    assert response.status_code == 202, response.text
    job_id = response.json()['job_id']
    location = {'job_id': job_id, 'engine_data': str(data)}
    (out/'location.json').write_text(json.dumps(location, indent=2))
    (out/'input.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(json.dumps(location), flush=True)
    deadline = time.monotonic()+1200
    previous = None
    while time.monotonic()<deadline:
        job = engine.get(f'/api/jobs/{job_id}').json()
        current = (job['status'], job.get('active_stage'))
        if current != previous:
            print(json.dumps({'status': current[0], 'stage': current[1]}), flush=True)
            previous = current
        if job['status'] in {'COMPLETED', 'FAILED', 'CANCELLED', 'NEEDS_INPUT'}:
            break
        time.sleep(1)
    (out/'job.json').write_text(json.dumps(job, ensure_ascii=False, indent=2))
    if job['status'] != 'COMPLETED':
        raise RuntimeError({k: job.get(k) for k in ('status', 'error_code', 'error_message')})
    for name in ('video.mp4', 'content.mp4', 'video-ir.json', 'qa.json', 'package-qa.json', 'execution-log.txt'):
        response = engine.get(f'/api/jobs/{job_id}/artifacts/{name}')
        assert response.status_code == 200, name
        (out/name).write_bytes(response.content)
    ir = json.loads((out/'video-ir.json').read_text())
    result = {**location, 'components': [s['component'] for s in ir['scenes']], 'real_model': True, 'real_tts': True,
        'camera_scenes': [{'id': s['id'], 'component': s['component'], 'shot_plan': s['props'].get('shot_plan')} for s in ir['scenes']],
        'media': json.loads((out/'qa.json').read_text())['video']}
    (out/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    attempt = max((data/'jobs'/job_id/'attempts').iterdir(), key=lambda p: int(p.name))/'g2-run'
    assembly = json.loads((attempt/'artifacts/scene-assembly.json').read_text())
    result['assembly'] = assembly
    result['entries'] = [s['props'].get('scene_flow') for s in ir['scenes']]
    (out/'scene-assembly.json').write_text(json.dumps(assembly, ensure_ascii=False, indent=2))
    (out/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    direction = json.loads((attempt/'artifacts/story-direction.json').read_text())
    result['direction'] = direction
    (out/'story-direction.json').write_text(json.dumps(direction, ensure_ascii=False, indent=2))
    (out/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    assert all(s['props'].get('story') for s in ir['scenes']), result
    assert any(s['props']['story'].get('recap') for s in ir['scenes']), result
    print(json.dumps(result, ensure_ascii=False), flush=True)

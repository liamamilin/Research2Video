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
args = parser.parse_args()
fixtures, out = args.fixtures.resolve(), args.output.resolve()
out.mkdir(parents=True, exist_ok=False)
data = ROOT/'g3/data/acceptance-shot-director-v3'/uuid4().hex
data.mkdir(parents=True)
shutil.copyfile(ROOT/'g3/data/llm-settings.json', data/'llm-settings.json')
pages = json.loads((fixtures/'pages.json').read_text())
paragraphs = [
    '让画面跟着讲解走。',
    '先告诉观众此时该看哪里。',
    '先看左侧的文件区，研究报告和产品说明都放在这里，观众先认识这一组材料。'
    '每个文件各占一行，名称和上传状态放在一起，讲到哪份材料就能找到对应的位置。'
    '文件区保持在画面里，我们接着解释这些材料将怎样参与后面的提问。'
    '接着看下方的提问区，在输入框里写下想了解的问题，让材料和问题出现在同一页。'
    '问题不用离开当前工作台，可以围绕产品能力继续追问，也可以询问具体的使用方法。'
    '讲解停在输入框附近，观众能看到问题写在哪里，以及生成回答的按钮在哪里。'
    '再看右侧的结果区，生成的回答整理成材料摘要和下一步行动，阅读顺序清楚地排出来。'
    '摘要先概括产品能力和主要变化，行动建议接在后面，方便把刚才的问题继续推进。'
    '最后把文件、问题和回答连起来看，这张工作台就形成了一条完整的使用路径。',
    '接着看四个月的使用量。一月12万次，二月18万次，三月16万次，四月24万次。三月有一次回落，四月继续上升。四月是这四个月的最高点。',
]
with TestClient(create_app(Settings(data_dir=data, runner_timeout_seconds=1200), start_worker=True)) as engine:
    materials = []
    for name, caption, role, placements, targets in [
        ('output', 'FlowNote 工作台', 'overall', [2], [('files', '文件区'), ('question', '提问区'), ('result', '结果区')]),
    ]:
        page = pages[name]
        response = engine.post('/api/creative-images', files={'file': (page['file'], (fixtures/page['file']).read_bytes(), 'image/png')})
        assert response.status_code == 201, response.text
        materials.append({'image_id': response.json()['image_id'], 'caption': caption, 'role': role,
            'placements': [{'chapter_index': 0, 'paragraph_index': p} for p in placements],
            'focus': {'width': page['width'], 'height': page['height'],
                      'regions': [{**page['regions'][key], 'label': label, 'style': 'box'} for key, label in targets]}})
    payload = {'title': '长短段落自动拆合与同页镜头承接', 'input_mode': 'narration', 'duration_mode': 'content',
        'chapters': [{'title': '跟着讲解看工作台', 'paragraphs': paragraphs},
                     {'title': '把内容组织成观看路径', 'paragraphs': ['从认识材料到提出问题，再到看见回答，画面始终跟着当前的讲解重点走。换一个章节时，再建立新的讲解主题。']}], 'video_form': 'object_showcase',
        'visual_storytelling': True, 'voice_id': 'zf_xiaoxiao', 'use_draw_things': False,
        'production_recipe': 'science_explainer', 'image_materials': materials}
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
    assert {'merge', 'split'} <= {u['operation'] for u in assembly['units']}, result
    assert 'carry' in [s['props'].get('scene_flow', {}).get('entry') for s in ir['scenes']], result
    assert 'data_chart' in result['components'], result
    print(json.dumps(result, ensure_ascii=False), flush=True)

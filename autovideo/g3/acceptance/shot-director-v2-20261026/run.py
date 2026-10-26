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
data = ROOT/'g3/data/acceptance-shot-director-v2'/uuid4().hex
data.mkdir(parents=True)
shutil.copyfile(ROOT/'g3/data/llm-settings.json', data/'llm-settings.json')
pages = json.loads((fixtures/'pages.json').read_text())
paragraphs = [
    '先看这张工作台页面。左侧的文件区用来放入研究报告和产品说明，文件会集中显示在这里。接着看下方的提问区，我们可以围绕这些材料继续提问。两个区域放在同一页，讲解时先看文件，再看问题。',
    '把输入界面和输出结果并排看。先看输入问题，提问区保留我们刚才写好的问题。然后看输出结果，回答已经整理成材料摘要和下一步行动。最后回到两张图一起看，从问题到回答的变化就清楚了。',
    '接着看四个月的使用量。一月12万次，二月18万次，三月16万次，四月24万次。三月有一次回落，四月继续上升。四月是这四个月的最高点。',
    '最后回到工作台。文件区保留研究报告和产品说明，提问区承接我们想了解的问题。把页面、输入输出和趋势放在一起，就能跟着讲解看清重点。',
]
with TestClient(create_app(Settings(data_dir=data, runner_timeout_seconds=1200), start_worker=True)) as engine:
    materials = []
    for name, caption, role, placements, targets in [
        ('workspace', 'FlowNote 工作台', 'overall', [0, 3], [('files', '文件区'), ('question', '提问区')]),
        ('input', '输入界面', 'comparison', [1], [('question', '输入问题')]),
        ('output', '输出结果', 'comparison', [1], [('result', '输出结果')]),
    ]:
        page = pages[name]
        response = engine.post('/api/creative-images', files={'file': (page['file'], (fixtures/page['file']).read_bytes(), 'image/png')})
        assert response.status_code == 201, response.text
        materials.append({'image_id': response.json()['image_id'], 'caption': caption, 'role': role,
            'placements': [{'chapter_index': 0, 'paragraph_index': p} for p in placements],
            'focus': {'width': page['width'], 'height': page['height'],
                      'regions': [{**page['regions'][key], 'label': label, 'style': 'box'} for key, label in targets]}})
    payload = {'title': '页面、对比与数据跟着讲述走', 'input_mode': 'narration', 'duration_mode': 'content',
        'chapters': [{'title': '看清页面与数据', 'paragraphs': paragraphs}], 'video_form': 'object_showcase',
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
    assert result['components'] == ['object_showcase', 'material_collage', 'data_chart', 'object_showcase'], result
    assert all(s['shot_plan'] for s in result['camera_scenes'])
    print(json.dumps(result, ensure_ascii=False), flush=True)

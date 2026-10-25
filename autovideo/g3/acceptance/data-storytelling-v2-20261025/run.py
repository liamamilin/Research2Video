"""V2 real automatic scatter, spoken comparisons, screenshots and trend video."""
import argparse
import json
import sys
import shutil
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient
from g3.app import create_app
from g3.config import Settings

parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
out = args.output.resolve()
out.mkdir(parents=True, exist_ok=False)
data = ROOT / 'g3/data/acceptance-data-storytelling-v2' / uuid4().hex
data.mkdir(parents=True)
if (ROOT/'g3/data/llm-settings.json').is_file():
    shutil.copyfile(ROOT/'g3/data/llm-settings.json', data/'llm-settings.json')
source = ROOT.parent / 'AI_research/Base_CodingCLi/spec/acceptance/automatic-visuals-20261025/source-region.png'
paragraphs = [
    '先看页面中的截图功能说明，画面会放大对应区域，帮助我们找到正在介绍的功能。',
    '把四个方案的月成本和处理速度放到二维图上。方案A每月8美元、每秒40次；方案B每月12美元、每秒75次；方案C每月18美元、每秒90次；方案D每月10美元、每秒55次。先比较A和D，A更省钱。再看B和C，C速度更高。整体看，B在成本和速度之间更均衡。',
    '再把三个模型的单次成本和任务得分放到一起。轻量版每次0.002美元、任务得分72分；标准版每次0.006美元、任务得分86分；增强版每次0.01美元、任务得分91分。标准版兼顾成本和得分。',
    '接着看月度使用量，一月12万次，二月18万次，三月30万次。三月达到这几个月的最高点。',
    '同一条视频里，页面、二维关系和趋势可以跟着讲述自然切换。']
with TestClient(create_app(Settings(data_dir=data, runner_timeout_seconds=900), start_worker=True)) as engine:
    registered = engine.post('/api/creative-images', files={'file': ('page.png', source.read_bytes(), 'image/png')})
    assert registered.status_code == 201, registered.text
    payload = {'title': '看懂成本与效果的取舍', 'input_mode': 'narration', 'duration_mode': 'content',
               'chapters': [{'title': '二维关系与讲解重点', 'paragraphs': paragraphs}], 'video_form': 'object_showcase',
               'visual_storytelling': True, 'voice_id': 'zf_xiaoxiao', 'use_draw_things': False,
               'production_recipe': 'science_explainer',
               'image_materials': [{'image_id': registered.json()['image_id'], 'caption': '页面截图功能', 'role': 'detail',
                  'placements': [{'chapter_index': 0, 'paragraph_index': 0}],
                  'focus': {'width': 847, 'height': 262, 'regions': [{'x1': 8, 'y1': 35, 'x2': 570, 'y2': 410, 'label': '截图功能说明', 'style': 'box'}]}}]}
    response = engine.post('/api/jobs', json=payload)
    assert response.status_code == 202, response.text
    job_id = response.json()['job_id']
    print(json.dumps({'job_id': job_id, 'engine_data': str(data)}, ensure_ascii=False), flush=True)
    (out/'input.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    (out/'location.json').write_text(json.dumps({'job_id': job_id, 'engine_data': str(data)}, indent=2))
    deadline = time.monotonic()+900
    previous = None
    while time.monotonic()<deadline:
        job = engine.get(f'/api/jobs/{job_id}').json()
        current = (job['status'], job.get('active_stage'))
        if current != previous:
            print(json.dumps({'status': current[0], 'stage': current[1]}, ensure_ascii=False), flush=True)
            previous = current
        if job['status'] in {'COMPLETED', 'FAILED', 'CANCELLED', 'NEEDS_INPUT'}:
            break
        time.sleep(1)
    (out/'job.json').write_text(json.dumps(job, ensure_ascii=False, indent=2))
    if job['status'] != 'COMPLETED':
        raise RuntimeError(str({k:job.get(k) for k in ('status','error_code','error_message')}))
    for name in ('video.mp4', 'content.mp4', 'video-ir.json', 'qa.json', 'package-qa.json', 'execution-log.txt'):
        result = engine.get(f'/api/jobs/{job_id}/artifacts/{name}')
        assert result.status_code == 200, (name, result.text[:100])
        (out/name).write_bytes(result.content)
    ir = json.loads((out/'video-ir.json').read_text())
    components = [s['component'] for s in ir['scenes']]
    kinds = [s['props']['kind'] for s in ir['scenes'] if s['component']=='data_chart']
    result = {'job_id': job_id, 'engine_data': str(data), 'components': components, 'chart_kinds': kinds,
              'presentation': ir['presentation'], 'real_tts': True, 'real_llm': True,
              'scatter_scenes': [s for s in ir['scenes'] if s['component']=='scatter_chart'],
              'media': json.loads((out/'qa.json').read_text())['video']}
    (out/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    assert 'object_showcase' in components and 'data_chart' in components, result
    assert components.count('scatter_chart') >= 2, result
    assert any(s['props'].get('focus_steps') for s in result['scatter_scenes']), result
    print(json.dumps({k:v for k,v in result.items() if k!='scatter_scenes'}, ensure_ascii=False), flush=True)

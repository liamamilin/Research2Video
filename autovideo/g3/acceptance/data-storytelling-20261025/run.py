"""Real mixed-scene video: upstream copy → automatic layout → Kokoro → Remotion."""
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
data = ROOT / 'g3/data/acceptance-data-storytelling' / uuid4().hex
data.mkdir(parents=True)
if (ROOT/'g3/data/llm-settings.json').is_file():
    shutil.copyfile(ROOT/'g3/data/llm-settings.json', data/'llm-settings.json')
source = ROOT.parent / 'AI_research/Base_CodingCLi/spec/acceptance/automatic-visuals-20261025/source-region.png'
paragraphs = [
    '先看页面中的截图功能说明，画面会放大对应区域，帮助我们找到正在介绍的功能。',
    '订阅价格从20美元变成25美元，增加5美元。先看旧价格，再看新价格，最后突出增加的部分。',
    '接着看月度使用量的趋势，一月是12万次，二月18万次，三月16万次，四月30万次。四月达到这几个月的最高点。',
    '再比较两款工具在写作、编程和检索三种任务上的耗时。工具A分别是10秒、16秒和12秒，工具B分别是8秒、14秒和9秒。工具B在这三种任务上都更快。',
    '把页面、数字变化和数据图表放在一起，观众就能跟着讲述看见重点。']
with TestClient(create_app(Settings(data_dir=data, runner_timeout_seconds=600), start_worker=True)) as engine:
    registered = engine.post('/api/creative-images', files={'file': ('page.png', source.read_bytes(), 'image/png')})
    assert registered.status_code == 201, registered.text
    payload = {'title': '让数据跟着讲述展开', 'input_mode': 'narration', 'duration_mode': 'content',
               'chapters': [{'title': '画面表达示例', 'paragraphs': paragraphs}], 'video_form': 'object_showcase',
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
    deadline = time.monotonic()+600
    previous = None
    while time.monotonic()<deadline:
        job = engine.get(f'/api/jobs/{job_id}').json()
        current = (job['status'], job.get('current_stage'))
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
              'media': json.loads((out/'qa.json').read_text())['video']}
    (out/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    assert 'object_showcase' in components and 'price_change' in components, result
    assert set(kinds)=={'trend','grouped'}, result
    print(json.dumps(result, ensure_ascii=False), flush=True)

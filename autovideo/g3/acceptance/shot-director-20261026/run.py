"""Exercise automatic shot direction with real narration, TTS, and model planning."""
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
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
out = args.output.resolve()
out.mkdir(parents=True, exist_ok=False)
data = ROOT / 'g3/data/acceptance-shot-director' / uuid4().hex
data.mkdir(parents=True)
shutil.copyfile(ROOT/'g3/data/llm-settings.json', data/'llm-settings.json')
paragraphs = [
    '先看一次文件查询。用户上传文件，服务器接收请求，把文件交给解析器处理。解析器提取内容，再到数据库中检索。数据库找到相关记录，生成结果，最后把结果返回给用户。把这些对象连起来，就能看清一次请求怎样完成。',
    '再看四个月的使用量。一月是12万次，二月增加到18万次，三月回落到16万次，四月达到24万次。二月到三月出现一次回落，随后又继续上升。四月是这四个月中的最高点，整体趋势向上。',
    '再把两个方案放在相同月份比较。一月，本地方案的成本是20元，云端方案是30元。二月，本地方案是25元，云端方案是28元。三月，本地方案是22元，云端方案是32元。每个月并排看两组数据，可以看清方案之间的差异。',
    '最后把成本和速度放在一起看。甲方案成本20元，速度80次每秒。乙方案成本30元，速度120次每秒。丙方案成本25元，速度70次每秒。丁方案成本40元，速度150次每秒。先比较甲方案和乙方案，乙方案速度更高，成本也更高。再比较甲方案和丙方案，甲方案成本更低，速度更高。选择取决于团队更在意哪项指标。',
]
payload = {'title': '跟着讲述看清流程和数据', 'input_mode': 'narration', 'duration_mode': 'content',
           'chapters': [{'title': '对象、趋势与比较', 'paragraphs': paragraphs}],
           'video_form': 'information_motion', 'visual_storytelling': True,
           'voice_id': 'zf_xiaoxiao', 'use_draw_things': False, 'production_recipe': 'science_explainer'}
with TestClient(create_app(Settings(data_dir=data, runner_timeout_seconds=1200), start_worker=True)) as engine:
    response = engine.post('/api/jobs', json=payload)
    assert response.status_code == 202, response.text
    job_id = response.json()['job_id']
    location = {'job_id': job_id, 'engine_data': str(data)}
    print(json.dumps(location), flush=True)
    (out/'input.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    (out/'location.json').write_text(json.dumps(location, indent=2))
    previous = None
    deadline = time.monotonic()+1200
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
        result = engine.get(f'/api/jobs/{job_id}/artifacts/{name}')
        assert result.status_code == 200, (name, result.text[:100])
        (out/name).write_bytes(result.content)
    ir = json.loads((out/'video-ir.json').read_text())
    result = {**location, 'real_model': True, 'real_tts': True,
              'components': [s['component'] for s in ir['scenes']],
              'camera_scenes': [{'id': s['id'], 'component': s['component'], 'shot_plan': s['props']['shot_plan']}
                                for s in ir['scenes'] if s['props'].get('shot_plan')],
              'media': json.loads((out/'qa.json').read_text())['video']}
    (out/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    assert {'flow_diagram', 'data_chart', 'scatter_chart'} <= set(result['components']), result
    assert all(s['shot_plan']['profile'] == 'explain' for s in result['camera_scenes'])
    print(json.dumps(result, ensure_ascii=False), flush=True)

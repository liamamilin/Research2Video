"""Real automatic object diagrams, Lucide icons and semantic Lottie actions."""
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
data = ROOT / 'g3/data/acceptance-visual-language' / uuid4().hex
data.mkdir(parents=True)
if (ROOT/'g3/data/llm-settings.json').is_file():
    shutil.copyfile(ROOT/'g3/data/llm-settings.json', data/'llm-settings.json')
paragraphs = [
    '从一次文件查询看系统如何协作。用户上传文件，服务器接收请求，再到数据库中检索，最后把结果返回给用户。',
    '文件处理、结果生成和连接中断，是三个不同的状态。处理文件时，内容正在解析。生成结果时，输出已经准备好。连接中断时，请求停止，等待重新连接。',
    '再对比本地服务器和云端服务。本地服务器便于掌控运行环境，云端服务便于多端访问。按团队的工作方式选择即可。',
    '最后看价格变化：月费从20美元变成25美元，增加5美元。']
with TestClient(create_app(Settings(data_dir=data, runner_timeout_seconds=900), start_worker=True)) as engine:
    payload = {'title': '用对象和动作讲清一次查询', 'input_mode': 'narration', 'duration_mode': 'content',
               'chapters': [{'title': '对象、状态与选择', 'paragraphs': paragraphs}], 'video_form': 'information_motion',
               'visual_storytelling': True, 'voice_id': 'zf_xiaoxiao', 'use_draw_things': False,
               'production_recipe': 'science_explainer'}
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
    visuals = [{'scene_id':s['id'], 'component':s['component'], 'objects':s['props'].get('visuals', [])}
               for s in ir['scenes'] if s['props'].get('visuals')]
    result = {'job_id': job_id, 'engine_data': str(data), 'components': components,
              'presentation': ir['presentation'], 'real_tts': True, 'real_llm': True, 'visuals': visuals,
              'media': json.loads((out/'qa.json').read_text())['video']}
    (out/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    assert {'flow_diagram','concept_reveal','comparison'} <= set(components), result
    actions = {v.get('action') for row in visuals for v in row['objects']}
    assert {'process','complete','disconnect'} <= actions, result
    print(json.dumps(result, ensure_ascii=False), flush=True)

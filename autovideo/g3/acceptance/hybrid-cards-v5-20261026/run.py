"""Exercise ordinary API -> real model/TTS -> hybrid pictures -> shared packaging."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient
from g3.app import create_app
from g3.config import Settings

parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--pure', action='store_true')
args = parser.parse_args()
out = args.output.resolve()
out.mkdir(parents=True, exist_ok=False)
data = ROOT / 'g3/data/acceptance-hybrid-cards-v5' / uuid4().hex
data.mkdir(parents=True)
shutil.copyfile(ROOT/'g3/data/llm-settings.json', data/'llm-settings.json')
fixtures = ROOT / 'g3/data/story-direction-v4-20261026-pages'
page = json.loads((fixtures/'pages.json').read_text())['local']
paragraphs = [
    '价格上涨之后，这个工具还值不值得用？这期先看产品页面，再把价格变化和处理流程放到一起讲清楚。',
    '先约定观看顺序。我们从使用界面出发，接着讲费用，再沿着文件的处理路径看结果。普通说明只保留几个要点，重点画面再展开。',
    '先看本地方案的产品界面。上面是单次成本和响应时间，下面是上传材料、开始处理、查看结果这条路径。讲到哪个部分，就看页面里的对应位置。',
    '再看价格变化。旧价格是20元，新价格是25元，增加5元。先看到旧值，再出现新值，最后把增加的5元突出显示。',
    '把一次请求的过程串起来。用户上传文件，服务器开始处理，处理完成后生成结果，再把结果返回给用户。沿着这条路径看，就能跟上每一步发生了什么。',
    '使用量也在变化。一月处理12万次，二月处理18万次，三月处理16万次。二月更高，三月有所回落。趋势图按月份逐步出现，最后停在二月的高点。',
    '现在把视线收回来。看懂页面、费用和过程之后，就可以根据自己的使用方式作出选择。日常的说明保持简洁，关键差异单独讲清楚。',
    '最后回顾两个重点：价格从20元变成25元，增加5元；处理过程从上传文件开始，到返回结果结束。把价格变化和处理流程一起记住，就能理解这次介绍的重点。',
]
payload = {'title': '快速讲解也能看清重点', 'input_mode': 'narration', 'duration_mode': 'content',
    'chapters': [{'title': '快速讲解也能看清重点', 'paragraphs': paragraphs}],
    'production_mode': 'fast', 'visual_storytelling': not args.pure,
    'voice_id': 'zf_xiaoxiao', 'production_recipe': 'ai_news',
    'packaging': {'sound': {'enabled': False}}}
started = time.monotonic()
with TestClient(create_app(Settings(data_dir=data, runner_timeout_seconds=1800), start_worker=True)) as engine:
    if not args.pure:
        response = engine.post('/api/creative-images', files={'file': ('local.png', (fixtures/'local.png').read_bytes(), 'image/png')})
        assert response.status_code == 201, response.text
        payload['image_materials'] = [{'image_id': response.json()['image_id'], 'caption': '本地方案产品界面', 'role': 'overall',
            'placements': [{'chapter_index': 0, 'paragraph_index': 2}],
            'focus': {'width': page['width'], 'height': page['height'], 'regions': [
                {**page['regions']['metrics'], 'label': '成本和响应时间', 'style': 'box'},
                {**page['regions']['workflow'], 'label': '文件处理流程', 'style': 'box'}]}}]
    response = engine.post('/api/jobs', json=payload)
    assert response.status_code == 202, response.text
    jid = response.json()['job_id']
    (out/'input.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    (out/'location.json').write_text(json.dumps({'engine_data': str(data), 'job_id': jid}, indent=2))
    print(json.dumps({'engine_data': str(data), 'job_id': jid}), flush=True)
    previous = None
    while time.monotonic()-started < 1800:
        job = engine.get(f'/api/jobs/{jid}').json()
        status = (job['status'], job.get('active_stage'))
        if status != previous:
            print(json.dumps(status, ensure_ascii=False), flush=True)
            previous = status
        if status[0] in {'COMPLETED', 'FAILED', 'NEEDS_INPUT', 'CANCELLED'}:
            break
        time.sleep(1)
    (out/'job.json').write_text(json.dumps(job, ensure_ascii=False, indent=2))
    assert job['status'] == 'COMPLETED', {k: job.get(k) for k in ('status', 'error_code', 'error_message')}
    for name in ('video.mp4', 'content.mp4', 'video-ir.json', 'qa.json', 'package-qa.json'):
        response = engine.get(f'/api/jobs/{jid}/artifacts/{name}')
        assert response.status_code == 200, name
        (out/name).write_bytes(response.content)
    attempt = max((data/'jobs'/jid/'attempts').iterdir(), key=lambda p: int(p.name)) / 'g2-run'
    for name in ('production-execution.json', 'hybrid-card-plan.json', 'explanation-plan.json', 'scene-assembly.json', 'story-direction.json'):
        path = attempt/'artifacts'/name
        if path.exists():
            shutil.copyfile(path, out/name)
    ir = json.loads((out/'video-ir.json').read_text())
    execution = json.loads((out/'production-execution.json').read_text())
    result = {'job_id': jid, 'attempt': str(attempt), 'seconds': round(time.monotonic()-started, 3),
              'real_model': True, 'real_tts': True, 'components': [s['component'] for s in ir['scenes']],
              'execution': execution, 'media': json.loads((out/'qa.json').read_text())['video']}
    (out/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False), flush=True)

import json
import shutil
import sys
import time
from pathlib import Path

PROJECT = Path('/Users/milin/2026/软件开发/视频workflow/autovideo')
sys.path.insert(0, str(PROJECT))
from fastapi.testclient import TestClient
from g3.app import create_app
from g3.config import Settings

DATA = PROJECT / 'g3/data/validation/regression-fixes-20261114-final'
DATA.mkdir(parents=True, exist_ok=True)
settings = Settings(data_dir=DATA)
shutil.copy2(PROJECT / 'g3/data/llm-settings.json', settings.llm_settings_file)
request = {
    'title': '回归验证：任务重试与完成记录',
    'input_mode': 'narration',
    'chapters': [{'title': '任务如何恢复', 'paragraphs': [
        '一个视频任务先整理讲稿，再生成配音，最后把画面和声音合成视频。某一步暂时失败时，保留已有记录，继续处理后面的工作。',
        '完成后，我们可以查看成片，下载视频，也可以回到任务详情，检查每个阶段的状态。旧的尝试仍然保留，方便比较。'
    ]}],
    'duration_mode': 'content', 'target_duration_sec': 30,
    'video_form': 'process_diagram', 'voice_id': 'voxcpm_anchor',
    'use_draw_things': False, 'packaging': {'enabled': False},
}
(DATA / 'smoke-request.json').write_text(json.dumps(request, ensure_ascii=False, indent=2))
with TestClient(create_app(settings)) as client:
    response = client.post('/api/jobs', json=request)
    if response.status_code != 202:
        print(response.status_code, response.text, flush=True)
        sys.exit(1)
    created = response.json()
    job_id = created['job_id']
    (DATA / 'smoke-job.json').write_text(json.dumps({'job_id': job_id}))
    print('created', job_id, flush=True)
    last = None
    for _ in range(90):
        job = client.get('/api/jobs/' + job_id).json()
        status = (job['status'], job.get('stage'), job.get('error_code'))
        if status != last:
            print('status', status, flush=True)
            last = status
        if job['status'] in {'COMPLETED', 'FAILED', 'CANCELLED'}:
            (DATA / 'smoke-result.json').write_text(json.dumps(job, ensure_ascii=False, indent=2))
            print('finished', job['status'], job.get('error_code'), flush=True)
            sys.exit(0 if job['status'] == 'COMPLETED' else 1)
        time.sleep(10)
    print('timeout', flush=True)
    sys.exit(2)

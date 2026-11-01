"""Generate an authored tutorial through the normal API, model and worker."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def save(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    from fastapi.testclient import TestClient
    from g3.app import create_app
    from g3.config import Settings

    paragraphs = [
        '把一个 Python 项目在本机跑起来，可以分成建环境、激活环境、启动应用三步。把完整命令放在画面里，讲到哪一步就强调哪一行，前面的命令仍然留着，方便对照。',
        '第一步创建独立环境。命令是 `python -m venv .venv`。python 调用解释器，减 m 表示运行模块，venv 负责创建环境，最后的点 venv 是环境目录。先完成这一步，再安装项目依赖。',
        '第二步激活环境，命令是 `source .venv/bin/activate`。这一行指向刚才建立的环境。激活之后，后面的 Python 命令就使用这个环境。画面可以保留创建命令，同时把重点移到激活这一行。',
        '第三步启动应用，命令是 `python app.py --port 8011`。app.py 是入口文件，port 后面的 8011 是本次使用的端口。这里要留意两个连续的短横线，完整写法是减减 port。',
        '如果配置原来写的是 `PORT = 8010`，本次改成 `PORT = 8011`，画面中先标出原来这一行，再展示替换后的这一行。其他设置保持原样，就能一眼看出这次修改的位置。',
        '回顾一下，先创建环境，再激活环境，最后启动应用。代码不必一口气全部念完，关键是让命令、步骤和结果在画面上对应起来。讲到最后，保留已经完成的步骤，观众就能按顺序复现。',
    ]
    payload = {'title': '教程画面验证：环境、命令与端口修改', 'input_mode': 'narration',
        'duration_mode': 'content', 'chapters': [{'title': '运行本地项目', 'paragraphs': paragraphs}],
        'production_recipe': 'how_to', 'production_mode': 'fast', 'video_form': 'information_cards',
        'visual_style': 'editorial_light', 'visual_storytelling': True, 'voice_id': 'zf_xiaoxiao',
        'packaging': {'preset': 'common', 'intro': {'enabled': False}, 'outro': {'enabled': False},
                      'sound': {'enabled': False}, 'source_card': {'enabled': False}}}
    data = ROOT / 'g3/data/acceptance-visual-improvement-20261101' / uuid4().hex
    data.mkdir(parents=True)
    shutil.copyfile(ROOT / 'g3/data/llm-settings.json', data / 'llm-settings.json')
    (data / 'llm-settings.json').chmod(0o600)
    save(out / 'input.json', payload)
    with TestClient(create_app(Settings(data_dir=data, runner_timeout_seconds=3600), start_worker=True)) as client:
        response = client.post('/api/jobs', json=payload)
        assert response.status_code == 202, response.text
        job_id = response.json()['job_id']
        save(out / 'location.json', {'job_id': job_id, 'engine_data': str(data)})
        deadline, previous = time.monotonic() + 3600, None
        while time.monotonic() < deadline:
            job = client.get(f'/api/jobs/{job_id}').json()
            current = (job['status'], job.get('active_stage'))
            if current != previous:
                print(json.dumps({'job_id': job_id, 'status': current[0], 'stage': current[1]}), flush=True)
                save(out / 'progress.json', job)
                previous = current
            if job['status'] in {'COMPLETED', 'FAILED', 'CANCELLED', 'NEEDS_INPUT'}:
                break
            time.sleep(2)
        save(out / 'job.json', job)
        assert job['status'] == 'COMPLETED', {k: job.get(k) for k in ['status', 'error_code', 'error_message']}
        for name in ['content.mp4', 'video.mp4', 'video-ir.json', 'qa.json']:
            response = client.get(f'/api/jobs/{job_id}/artifacts/{name}')
            if response.status_code == 200:
                (out / name).write_bytes(response.content)
    attempt = max((data / 'jobs' / job_id / 'attempts').iterdir(), key=lambda path: int(path.name)) / 'g2-run'
    for name in ['compiled-timeline.json', 'caption-alignment.json', 'visual-cue-alignment.json',
                 'explanation-plan.json', 'production-execution.json', 'model-cost.json']:
        path = attempt / 'artifacts' / name
        if path.is_file():
            shutil.copyfile(path, out / name)
    ir = json.loads((out / 'video-ir.json').read_text())
    code = [scene['id'] for scene in ir['scenes'] if scene['component'] == 'code_demo']
    save(out / 'receipt.json', {'job_id': job_id, 'attempt': str(attempt), 'code_scene_ids': code,
        'normal_api_and_worker': True, 'manual_storyboard_or_beat_edits': 0})
    assert code, 'The normal planner did not select any code demo scenes'
    print(json.dumps({'completed': job_id, 'code_scenes': code, 'output': str(out)}), flush=True)


if __name__ == '__main__':
    main()

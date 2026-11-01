"""Prepare, then run one real tutorial through the existing API and worker."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
RESEARCH = ROOT.parent / 'AI_research/Base_CodingCLi'
sys.path[:0] = [str(ROOT), str(RESEARCH)]


def save(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def chapters() -> tuple[list[dict], str, dict]:
    from core.video import content_columns, episodes, screenshots, writer

    column = content_columns.load_all(str(RESEARCH / 'config'))['ai_opensource']
    context = {'column': column.key, 'column_name': column.name,
               'render': column.render, 'greeting': column.greeting}
    greeting = episodes.greeting({}, context)
    sections = [
        {'title': '这次做什么', 'paragraphs': [{'text':
            '今天用 MarkItDown，把这份项目周报从网页文件转成 Markdown。先准备输入，再运行转换，最后对照输出。屏幕里的结果来自这次实际运行。'}]},
        {'title': '准备输入', 'paragraphs': [
            {'text': '这份 weekly.html 是本地浏览器打开的项目周报。上半部分是标题和已完成清单，下面是一张任务与状态表。接下来就用这份文件做输入。'},
            {'text': '这里先记住两处：项目周报这个标题，以及表格里的转换文档和整理结果。等转换完成，我们会回到这些内容。'}]},
        {'title': '执行转换', 'paragraphs': [
            {'text': '第一条命令是 `pip install markitdown`。先把转换工具装进当前 Python 环境。'},
            {'text': '接着执行 `markitdown weekly.html -o weekly.md`。前一个文件名是输入，减 o 后面指定输出文件。运行后，Markdown 内容会写到 weekly.md。'},
            {'text': '这次实际运行返回正常退出，输出文件已经生成。下面直接打开这个文件看结果。'}]},
        {'title': '查看结果', 'paragraphs': [
            {'text': '现在看到的是刚才命令实际生成的 weekly.md。项目周报变成一级标题，已完成变成二级标题，两条清单也保留下来了。'},
            {'text': '再看下方的表格：任务和状态两列都在，转换文档对应进行中，整理结果对应待开始。输入里的这些内容，已经出现在 Markdown 文本里。'},
            {'text': '把输入和输出放在一起看：左边是网页中的周报，右边是同一份内容的 Markdown。我们改变的是文件的表示形式，任务和状态仍然对应。'}]},
        {'title': '回到这条任务', 'paragraphs': [{'text':
            '回顾这次操作：准备 weekly.html，运行转换命令，再取出 weekly.md。这个例子从输入到输出已经走完。下次再见。'}]},
    ]
    authored = writer._Script(title='MarkItDown 实操：把项目周报转成 Markdown', sections=sections)
    framed = writer._programme_frame(authored, greeting, False)
    assert writer._programme_frame(framed, greeting, False) == framed
    result = screenshots.chapters(framed.model_dump())
    assert [text for c in result for text in c['paragraphs']].count(greeting) == 1
    return result, greeting, context


def prepare(out: Path, demo: Path) -> None:
    from fastapi.testclient import TestClient
    from g3.app import create_app
    from g3.config import Settings

    out.mkdir(parents=True, exist_ok=False)
    data = out / 'engine-data'
    data.mkdir()
    shutil.copyfile(ROOT / 'g3/data/llm-settings.json', data / 'llm-settings.json')
    (data / 'llm-settings.json').chmod(0o600)
    chapter_list, greeting, context = chapters()
    positions = {'input': [(1, 0), (1, 1), (3, 2)],
                 'output': [(3, 0), (3, 1), (3, 2)]}
    labels = {'input': ['周报标题与已完成清单', '任务与状态表格'],
              'output': ['Markdown 标题与清单', 'Markdown 任务表格']}
    descriptions = {'input': '本地 weekly.html 项目周报：标题、两条已完成清单、任务与状态表格。',
                    'output': 'MarkItDown 实际生成的 weekly.md：原周报的 Markdown 标题、清单与任务状态表格。'}
    records = json.loads((demo / 'screenshots.json').read_text())
    materials = []
    with TestClient(create_app(Settings(data_dir=data), start_worker=False)) as client:
        for picture in records:
            name, width, height = picture['name'], picture['width'], picture['height']
            response = client.post('/api/creative-images', files={
                'file': (name + '.png', (demo / (name + '.png')).read_bytes(), 'image/png')})
            assert response.status_code == 201, response.text
            image = response.json()
            assert (image['width'], image['height']) == (width, height)
            regions = []
            for i, region in enumerate(picture['regions']):
                rect = {axis: max(0, min(1000, round(value))) for axis, value in {
                    'x1': (region['x']-10)/width*1000, 'y1': (region['y']-8)/height*1000,
                    'x2': (region['x']+region['width']+10)/width*1000,
                    'y2': (region['y']+region['height']+8)/height*1000}.items()}
                regions.append({**rect, 'label': labels[name][i], 'style': 'box'})
            materials.append({'image_id': image['image_id'], 'caption': descriptions[name], 'role': 'detail',
                'placements': [{'chapter_index': c, 'paragraph_index': p} for c, p in positions[name]],
                'focus': {'width': width, 'height': height, 'regions': regions}})
        payload = {'title': 'MarkItDown 实操：把项目周报转成 Markdown',
            'idempotency_key': 'tool-tutorial:' + uuid4().hex,
            'input_mode': 'narration', 'chapters': chapter_list, 'duration_mode': 'content',
            'production_recipe': 'mixed_tutorial', 'voice_id': 'zf_xiaoxiao',
            'slideshow_images': [m['image_id'] for m in materials], 'image_materials': materials,
            'sources': [{'title': 'MarkItDown 官方项目与命令用法',
                         'url': 'https://github.com/microsoft/markitdown', 'date': '2026-11-01'}],
            'packaging': {'label': 'AI 开源发现', 'intro': {'title': 'MarkItDown'},
                          'outro': {'lines': ['AI 开源发现']}}}
        preview = client.post('/api/production/resolve', json=payload)
        assert preview.status_code == 200, preview.text
        save(out / 'production-preview.json', preview.json())
    save(out / 'input.json', payload)
    save(out / 'preparation.json', {'engine_data': str(data), 'demo': str(demo),
        'greeting': greeting, 'greeting_owner': 'research episodes.greeting + writer._programme_frame',
        'column_context': context, 'model_calls': 0, 'manual_storyboard_or_beat_edits': 0})
    print(json.dumps({'prepared': str(out), 'greeting': greeting, 'images': len(materials)}, ensure_ascii=False))


def run(out: Path, parent_job_id: str | None = None) -> None:
    from fastapi.testclient import TestClient
    from g3.app import create_app
    from g3.config import Settings

    prepared = json.loads((out / 'preparation.json').read_text())
    payload = json.loads((out / 'input.json').read_text())
    data = Path(prepared['engine_data'])
    with TestClient(create_app(Settings(data_dir=data, runner_timeout_seconds=3600), start_worker=True)) as client:
        response = (client.post(f'/api/jobs/{parent_job_id}/media-revisions',
                               json={'voice_id': 'zf_xiaoxiao', 'video_speed': 1.0}) if parent_job_id
                    else client.post('/api/jobs', json=payload))
        assert response.status_code == 202, response.text
        job_id = response.json()['job_id']
        save(out / 'location.json', {'job_id': job_id, 'engine_data': str(data)})
        deadline, previous = time.monotonic()+3600, None
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
            assert response.status_code == 200, (name, response.status_code)
            (out / name).write_bytes(response.content)
    attempt = max((data / 'jobs' / job_id / 'attempts').iterdir(), key=lambda p: int(p.name)) / 'g2-run'
    for name in ['compiled-timeline.json', 'caption-alignment.json', 'visual-cue-alignment.json',
                 'explanation-plan.json', 'production-execution.json', 'model-cost.json',
                 'package.json', 'package-qa.json', 'packaging-resolved.json', 'episode-navigation.json',
                 'final-narration-plan.json', 'story-direction.json', 'scene-assembly.json']:
        path = attempt / 'artifacts' / name
        if path.is_file():
            shutil.copy2(path, out / name)
    for name in ['package.json', 'package-qa.json', 'chapters.json']:
        path = attempt / 'artifacts/package' / name
        if path.is_file():
            shutil.copy2(path, out / name)
    ir = json.loads((out / 'video-ir.json').read_text())
    save(out / 'receipt.json', {'job_id': job_id, 'source_attempt': str(attempt),
        'normal_api_and_worker': True, 'manual_storyboard_or_beat_edits': 0,
        'components': sorted({s['component'] for s in ir['scenes']}),
        'greeting_count': sum(s['text'] == prepared['greeting'] for s in ir['narration']['segments']),
        'parent_job_id': parent_job_id, 'replay_uses_same_voice_and_speed': bool(parent_job_id)})
    print(json.dumps({'completed': job_id, 'output': str(out)}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--demo', type=Path)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--replay-from', type=Path)
    args = parser.parse_args()
    if args.replay_from:
        out = args.output.resolve()
        out.mkdir(parents=True, exist_ok=False)
        for name in ['input.json', 'preparation.json']:
            shutil.copy2(args.replay_from / name, out / name)
        parent = json.loads((args.replay_from / 'location.json').read_text())['job_id']
        run(out, parent_job_id=parent)
    elif args.run:
        run(args.output.resolve())
    else:
        assert args.demo
        prepare(args.output.resolve(), args.demo.resolve())

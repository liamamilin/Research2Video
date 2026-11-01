"""One real matrix report through the existing unattended writer/render chain."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
from pathlib import Path
import shutil
import sys
import time

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
WORKSPACE = ROOT.parents[1]
PRODUCTION = 'ai_media_opensource_digest'


def save(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def prepare(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=False)
    for name in ['config', 'jobs']:
        shutil.copytree(ROOT / name, out / name)
    system_path = out / 'config/system.yaml'
    system = yaml.safe_load(system_path.read_text())
    system['video'].setdefault('storage', {})['dir'] = str(out / 'video-output')
    system['video']['archive'] = {'dir': str(out / 'archive'), 'keep_days': 7,
                                  'autovideo_data': str(out / 'unused-engine-data')}
    system_path.write_text(yaml.safe_dump(system, allow_unicode=True, sort_keys=False))
    demo = WORKSPACE / 'autovideo/g3/acceptance/tool-tutorial-20261101/demo'
    source, result = (demo / 'weekly.html').read_text(), (demo / 'weekly.md').read_text()
    report = '''# MarkItDown 实操：把项目周报转成 Markdown

## 本期任务
这是一次实际完成的本地工具操作，原稿和转换结果是本期确定输入。
为 AI 开源发现栏目制作一条清楚的工具教程，围绕同一份项目周报按准备输入、执行转换、查看结果三个阶段讲解。
三个阶段各自展开成内容章节，保留一个简短开场和收尾。具体任务足够讲清即可，不延长成项目百科。
展示原文中的实际命令、文件名和输出，让观众知道每步做什么、结果是什么；不念网页地址或代码标点。

## 工具与环境
本次实际使用 Microsoft MarkItDown 0.1.9，将本机 HTML 转成 Markdown。工具已安装在临时 Python 环境；HTML 转换没有调用大模型。
项目和命令用法：https://github.com/microsoft/markitdown

## 准备输入：weekly.html
在本地准备一份项目周报网页，打开它可以看到标题、已完成列表、下一步任务表。
标题是“项目周报”，已完成项是“整理项目资料”和“完成脚本联调”。下一步表格有任务和状态两列：转换文档进行中，整理结果待开始。
以下是本次实际输入文件，供作者理解内容；无需逐字朗读 HTML：

```html
''' + source + '''
```

## 执行转换
先安装工具，展示实际命令：`pip install markitdown`。
然后在 weekly.html 所在文件夹执行：`markitdown weekly.html -o weekly.md`。
weekly.html 是输入文件，-o 后的 weekly.md 指定输出文件。执行后，输出保存在同一文件夹。
安装与转换是连续两步，展示转换命令时保留安装命令的上下文。

## 查看结果：weekly.md
下面是本次命令实际生成的输出，保留原文，展示标题、列表与表格的对应变化：

```markdown
''' + result + '''
```

网页一级标题变成 Markdown 的一级标题，已完成变成二级标题，列表仍是两项；下一步任务和状态保留为 Markdown 表格。
用同一份输入和输出对照说明结果。本次只演示了本地 HTML 文件；不扩展其他格式或复杂依赖安装。

## 收尾
回到准备 weekly.html、运行转换命令、得到 weekly.md 的同一任务。现在有了一份可以继续编辑、放进笔记或交给后续文本流程的 Markdown 文件。

## 参考来源
1. MarkItDown 官方项目与命令用法 | 一手 | 2026-11-01 | https://github.com/microsoft/markitdown
'''
    report_path = out / 'output/radar/2026-11-01_ai_opensource_radar.md'
    report_path.parent.mkdir(parents=True)
    report_path.write_text(report)
    save(out / 'preparation.json', {'production': PRODUCTION, 'report': str(report_path),
        'report_sha256': hashlib.sha256(report.encode()).hexdigest(),
        'actual_conversion': json.loads((demo / 'conversion.json').read_text()),
        'config_snapshot': True, 'isolated_state': True, 'manual_script_or_storyboard': False,
        'automatic_visual_selection': True, 'model_calls': 0})
    print(json.dumps({'prepared': str(out), 'report': str(report_path)}, ensure_ascii=False))


def run(out: Path, *, job_name: str = 'ai_media/daily_opensource_radar') -> None:
    from web.settings import get_settings
    settings = get_settings()
    for name in ['config', 'jobs', 'output', 'state', 'logs']:
        setattr(settings.paths, name + '_dir', str(out / name))
    from core import activity, report_meta, search_health, telemetry
    activity.use_state_dir(settings.paths.state_dir)
    report_meta.META_FILE = str(out / 'state/report_meta.jsonl')
    search_health.use_state_dir(settings.paths.state_dir)
    telemetry.use_state_dir(settings.paths.state_dir)
    from core.video import auto, autopilot_store as ap, store
    from web.services import video_autopilot as pilot, video_render
    store.use_state_dir(settings.paths.state_dir)
    store.init_db()
    client = video_render.get_client()
    client.config_status()
    location = out / 'location.json'
    if location.exists():
        run_id = json.loads(location.read_text())['run_id']
    else:
        pending = auto.pending(settings.paths.config_dir, settings.paths.jobs_dir,
                               settings.paths.state_dir, str(out), time.time())
        spec = next(s for s in pending if s['job'] == job_name)
        spec['refresh'] = False
        batch = pilot.start_run(spec, started_by='autopilot')
        run_id = batch['id']
        save(location, {'run_id': run_id, 'spec': spec, 'state': settings.paths.state_dir})
    deadline, previous = time.monotonic() + 5400, None
    while time.monotonic() < deadline:
        batch = ap.get_run(run_id)
        pilot._tick_run(batch)
        batch = ap.get_run(run_id)
        scripts = [store.get_script(item['script_id']) for item in batch['items'] if item.get('script_id')]
        renders = [render for script in scripts for render in store.list_renders(script['id'])]
        state = (batch['status'], [(s['status'], s.get('stage')) for s in scripts],
                 [(r['status'], r.get('autovideo_status')) for r in renders])
        save(out / 'batch.json', batch)
        save(out / 'scripts.json', scripts)
        save(out / 'renders.json', renders)
        for script in scripts:
            if script.get('current_version'):
                save(out / 'version.json', store.get_version(script['id'], script['current_version']))
        if state != previous:
            print(json.dumps({'state': state}, ensure_ascii=False), flush=True)
            previous = state
        assert not any(s['status'] == 'FAILED' for s in scripts), scripts
        current = [r for r in renders if any(r['version'] == s.get('approved_version') for s in scripts)]
        assert not current or current[0]['status'] not in {'FAILED', 'CANCELLED'}, current
        assert not any(i['state'] in {'HELD', 'FAILED', 'CANCELLED'} for i in batch['items']), batch
        if batch['status'] == 'DONE':
            assert len(batch['items']) == 1 and batch['items'][0]['state'] == 'DONE', batch
            render = next(r for r in renders if r['id'] == batch['items'][0]['render_id'])
            assert render['status'] == 'COMPLETED'
            job = client.get_job(render['autovideo_job_id'])
            save(out / 'engine-job.json', job)
            save(out / 'receipt.json', {'status': 'PASS', 'run_id': run_id, 'script_id': scripts[0]['id'],
                'render_id': render['id'], 'job_id': job['job_id'], 'production': scripts[0]['production'],
                'content_context': scripts[0]['content_context'], 'files_dir': str(video_render.files_dir(render['id'])),
                'render_attempts': len(renders),
                'automatic_pick_write_approve_render_download': True,
                'manual_script_or_storyboard': False, 'automatic_visual_selection': True})
            print(json.dumps({'completed': job['job_id'], 'video': str(video_render.files_dir(render['id']) / 'video.mp4')}), flush=True)
            return
        time.sleep(2)
    raise TimeoutError('matrix tutorial acceptance exceeded its existing task deadline')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.run:
        run(args.output.resolve())
    else:
        prepare(args.output.resolve())

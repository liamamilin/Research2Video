"""Capture real tool input/output, then use the existing matrix production chain."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
WORKSPACE = ROOT.parents[1]
ENGINE = WORKSPACE / 'autovideo'
spec = importlib.util.spec_from_file_location('matrix_acceptance', ROOT / 'spec/acceptance/matrix-tutorial-20261101/run_matrix.py')
matrix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(matrix)


def prepare(out: Path, tool_python: Path) -> None:
    from core import report_meta, user_materials
    matrix.prepare(out)
    demo = out / 'actual-demo'
    subprocess.run([sys.executable, str(ENGINE / 'g3/acceptance/tool-tutorial-20261101/prepare_demo.py'),
                    '--tool-python', str(tool_python), '--output', str(demo)], check=True, capture_output=True, text=True)
    node = Path.home() / '.nvm/versions/node/v22.21.1/bin/node'
    subprocess.run([str(node), str(ENGINE / 'g3/acceptance/tool-tutorial-20261101/capture_demo.cjs'),
                    str(demo)], check=True, capture_output=True, text=True)
    descriptions = {
        'input': '实际输入：weekly.html 在本地浏览器打开的项目周报网页。上方是“项目周报”标题和“已完成”列表，列表有整理项目资料、完成脚本联调。下方“下一步”表格两列是任务与状态，两行是转换文档／进行中、整理结果／待开始。',
        'output': '实际输出：MarkItDown 命令本次生成的 weekly.md 原文。Markdown 一级标题“项目周报”，二级标题“已完成”和“下一步”，两项列表为整理项目资料、完成脚本联调；下方 Markdown 表格保留任务、状态两列与转换文档／进行中、整理结果／待开始两行。'}
    labels = {'input': ['项目周报与已完成列表', '下一步任务与状态'],
              'output': ['Markdown 标题和列表', 'Markdown 任务表格']}
    images = []
    owner = 'matrix:ai_media'
    for screenshot in json.loads((demo / 'screenshots.json').read_text()):
        name = screenshot['name']
        width, height = screenshot['width'], screenshot['height']
        regions = []
        for i, region in enumerate(screenshot['regions']):
            box = {'x1': (region['x']-10)/width*1000, 'y1': (region['y']-8)/height*1000,
                   'x2': (region['x']+region['width']+10)/width*1000,
                   'y2': (region['y']+region['height']+8)/height*1000}
            regions.append({**{key: max(0, min(1000, round(value))) for key, value in box.items()},
                            'label': labels[name][i], 'style': 'box'})
        images.append(user_materials.save(str(out / 'state'), owner,
            name=('周报网页实际输入.png' if name == 'input' else 'Markdown实际输出.png'),
            text=descriptions[name], raw=(demo / (name + '.png')).read_bytes(),
            focus={'width': width, 'height': height, 'regions': regions}))
    markdown = (demo / 'weekly.md').read_text()
    report = '''# MarkItDown 实操：把周报网页变成 Markdown

## 本期任务
围绕一份项目周报，做一条简洁的真实工具教程。实际输入、输出和配套截图都已准备好。
开场只介绍做什么；正文三个内容章节依次为准备输入、执行转换、查看结果；收尾回到这次任务，一句结束。
只讲完成这个任务所需的操作和看得见的结果，不扩展其他格式、历史背景或百科。
输入和输出截图用于展示实际内容，安装与转换使用代码画面；结果章节最后把两张图放在一起对照，不另加第四步。

## 准备输入
实际输入为本地 weekly.html，在浏览器打开就是项目周报。顶部是“项目周报”大标题；“已完成”列表有“整理项目资料”和“完成脚本联调”。
下面“下一步”表格有任务、状态两列；转换文档对应进行中，整理结果对应待开始。保留这一份输入供后面转换。

## 执行转换
本次实际使用 Microsoft MarkItDown 0.1.9，安装在临时 Python 环境。
先安装：`pip install markitdown`。
然后在 weekly.html 所在目录执行：`markitdown weekly.html -o weekly.md`。
weekly.html 是输入，-o 后的 weekly.md 是输出。本次命令成功生成 weekly.md，HTML 转 Markdown 在本地完成，没有调用模型。
展示转换命令时保留安装命令，两条代码连起来看。

## 查看结果
打开命令实际生成的 weekly.md。下面是它的真实原文：

```markdown
''' + markdown + '''
```

先看标题和列表：项目周报变成一级标题，已完成是二级标题，两项列表保留。
再看下方任务表格：任务、状态以及两行数据都还在，改为 Markdown 的表格写法。
最后对照两张实际截图：左边是原网页，右边是同一份内容的 Markdown。网页的层级、列表和任务状态都保留，输出可以继续编辑或交给后续文本流程。

## 收尾
准备 weekly.html，运行转换，拿到 weekly.md。本期这份任务已经完成。

## 来源
Microsoft MarkItDown 官方项目与命令：https://github.com/microsoft/markitdown
'''
    report_path = out / 'output/radar/2026-11-01_ai_opensource_radar.md'
    report_path.write_text(report)
    context = {'ids': [row['id'] for row in images], 'owner': owner, 'mode': 'adapt', 'revision': 1,
               'title': 'MarkItDown 真实输入与输出', 'audience': '开发者和工具使用者',
               'goal': '围绕同一份周报串起真实输入截图、安装与转换代码、实际输出截图和输入输出对照，沿用栏目问候、步骤转场及品牌包装。'}
    report_meta.META_DIR = str(out / 'state')
    report_meta.META_FILE = str(out / 'state/report_meta.jsonl')
    report_meta.append_record(str(report_path.relative_to(out)), 'ai_media/daily_opensource_radar', extra={'user_materials': context})
    matrix.save(out / 'preparation.json', {'actual_conversion': json.loads((demo / 'conversion.json').read_text()),
        'demo': str(demo), 'report': str(report_path), 'image_records': images, 'material_context': context,
        'automatic_capture_register_match_render': True, 'manual_paragraph_assignments': 0,
        'manual_script_or_storyboard': False, 'model_calls': 0})
    print(json.dumps({'prepared': str(out), 'actual_input_output_images': len(images)}, ensure_ascii=False))


def rewrite(out: Path) -> None:
    """Use the existing revision entry point; preserve the first automatic draft."""
    from web.settings import get_settings
    from core import report_meta, telemetry
    from core.video import autopilot_store as ap, store
    from web.services import video_autopilot, video_executor, video_render, video_workflow
    settings = get_settings()
    for name in ['config', 'jobs', 'output', 'state', 'logs']:
        setattr(settings.paths, name + '_dir', str(out / name))
    report_meta.META_FILE = str(out / 'state/report_meta.jsonl')
    telemetry.use_state_dir(settings.paths.state_dir)
    store.use_state_dir(settings.paths.state_dir)
    script = read_json(out / 'scripts.json')[0]
    render = read_json(out / 'renders.json')[0]
    assert video_render.process(render['id']) in {'CANCELLED', 'COMPLETED', 'FAILED'}
    version = store.get_version(script['id'], script['current_version'])
    matrix.save(out / ('version-v' + str(version['version']) + '.json'), version)
    assert video_workflow.run_rewrite(script['id'], started_by='autopilot', previous_status='APPROVED') == 'DRAFT'
    decision = video_executor.advance_auto(store.get_script(script['id']), {}, can_start_script=False, can_start_render=False)
    assert store.get_script(script['id'])['status'] == 'APPROVED'
    batch = ap.get_run(read_json(out / 'location.json')['run_id'])
    item = next(i for i in batch['items'] if i['script_id'] == script['id'])
    if decision['item']:
        ap.update_item(item['id'], **decision['item'])
    video_autopilot.reopen_approved()


def read_json(path: Path):
    return json.loads(path.read_text())


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--tool-python', type=Path)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--rewrite', action='store_true')
    args = parser.parse_args()
    if args.run:
        if args.rewrite:
            rewrite(args.output.resolve())
        matrix.run(args.output.resolve())
    else:
        assert args.tool_python
        prepare(args.output.resolve(), args.tool_python.absolute())

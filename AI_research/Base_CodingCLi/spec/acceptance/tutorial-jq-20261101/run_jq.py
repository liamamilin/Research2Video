"""A second actual tool fixture through the unchanged matrix tutorial pipeline."""
from __future__ import annotations

import argparse
import hashlib
import html
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
ENGINE = ROOT.parents[1] / 'autovideo'


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


matrix = module('matrix_acceptance', ROOT / 'spec/acceptance/matrix-tutorial-20261101/run_matrix.py')
verifier = module('tutorial_verifier', ROOT / 'spec/acceptance/tutorial-io-20261101/verify_tutorial.py')


def viewer(path: Path, filename: str, source: str, boundary: int) -> None:
    lines = source.splitlines(keepends=True)
    size = min(32, int(680 / max(1, len(lines)) / 1.3))
    page = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>{filename}</title>
<style>body{{margin:36px 54px;background:#fff;color:#20372d;font-family:"Hiragino Sans GB",sans-serif}}
header{{font-size:28px;color:#61756a;border-bottom:2px solid #dce6dc;padding-bottom:16px}}
pre{{font:{size}px/1.3 Menlo,"Hiragino Sans GB",monospace;white-space:pre-wrap;margin:20px 0 0}}
pre span{{display:block}}</style><header>{filename} · 本次实际文件内容</header>
<pre><span id="summary">{html.escape(''.join(lines[:boundary]))}</span><span id="tasks">{html.escape(''.join(lines[boundary:]))}</span></pre></html>'''
    path.write_text(page)


def prepare(out: Path):
    from core import report_meta, user_materials
    matrix.prepare(out)
    demo = out / 'actual-demo'
    demo.mkdir()
    jq = shutil.which('jq')
    assert jq
    version = subprocess.check_output([jq, '--version'], text=True).strip()
    tasks = [{'id': 'T01', 'task': '整理资料', 'done': True},
             {'id': 'T02', 'task': '生成草稿', 'done': False},
             {'id': 'T03', 'task': '检查输出', 'done': True}]
    source = json.dumps(tasks, ensure_ascii=False, indent=2) + '\n'
    (demo / 'tasks.json').write_text(source)
    displayed = ["jq '[.[]|select(.done)]' tasks.json > done.json", 'jq . done.json']
    query = '[.[]|select(.done)]'
    with (demo / 'done.json').open('w') as destination:
        result = subprocess.run([jq, query, 'tasks.json'], cwd=demo, stdout=destination,
                                stderr=subprocess.PIPE, text=True, check=True)
    inspected = subprocess.run([jq, '.', 'done.json'], cwd=demo, capture_output=True, text=True, check=True)
    output = (demo / 'done.json').read_text()
    assert json.loads(output) == [tasks[0], tasks[2]]
    assert json.loads(inspected.stdout) == json.loads(output)
    (demo / 'commands.log').write_text('\n'.join(displayed) + '\n' + result.stderr + inspected.stderr)
    matrix.save(demo / 'operation.json', {'tool': 'jq', 'version': version, 'commands': displayed,
        'exit_codes': [result.returncode, inspected.returncode], 'input_authored_for_tutorial': True,
        'output_from_actual_command': True, 'kept_task_ids': ['T01', 'T03'], 'excluded_task_ids': ['T02'],
        'model_calls': 0, 'source': 'https://jqlang.org/manual/v1.7/',
        'sha256': {name: hashlib.sha256((demo / name).read_bytes()).hexdigest() for name in ['tasks.json', 'done.json']}})
    viewer(demo / 'input-view.html', 'tasks.json', source, 6)
    viewer(demo / 'output-view.html', 'done.json', output, 6)
    subprocess.run([shutil.which('node'), str(ENGINE / 'g3/acceptance/tool-tutorial-20261101/capture_demo.cjs'),
                    str(demo), 'input-view.html', 'output-view.html'], check=True, capture_output=True, text=True)
    descriptions = {
        'input': '真实输入 tasks.json，JSON 数组包含三条任务。T01 整理资料的 done 为 true，T02 生成草稿的 done 为 false，T03 检查输出的 done 为 true。字段 id 是编号，task 是任务名，done 是完成标记；截图展示实际 JSON 原文。',
        'output': '真实输出 done.json，由本次 jq 筛选命令生成，仍是 JSON 数组。只留下 T01 整理资料和 T03 检查输出，两条 done 都是 true；T02 生成草稿没有留下。保留记录的 id、task、done 内容不变；截图展示实际输出原文。'}
    labels = {'input': ['输入任务 T01：已完成', '输入任务 T02 未完成与 T03 已完成'],
              'output': ['保留任务 T01：整理资料', '保留任务 T03：检查输出']}
    owner, images = 'matrix:ai_media', []
    for screenshot in json.loads((demo / 'screenshots.json').read_text()):
        name, width, height = screenshot['name'], screenshot['width'], screenshot['height']
        regions = []
        for i, rect in enumerate(screenshot['regions']):
            box = {'x1': (rect['x']-8)/width*1000, 'y1': (rect['y']-5)/height*1000,
                   'x2': (rect['x']+rect['width']+8)/width*1000,
                   'y2': (rect['y']+rect['height']+5)/height*1000}
            regions.append({**{key: max(0, min(1000, round(value))) for key, value in box.items()},
                            'label': labels[name][i], 'style': 'box'})
        images.append(user_materials.save(str(out / 'state'), owner,
            name=('任务清单实际输入.png' if name == 'input' else '已完成任务实际输出.png'),
            text=descriptions[name], raw=(demo / (name + '.png')).read_bytes(),
            focus={'width': width, 'height': height, 'regions': regions}))
    report = '''# jq 实操：从任务清单筛出已完成任务

## 本期任务
这次换用 jq 做真实 JSON 筛选，围绕同一份任务清单制作简洁教程。开场只说明要做什么，正文三章是准备输入、执行筛选、查看结果，结尾一句收住。
输入与输出都有本次实际截图，按对应段落展示；操作段展示实际代码；结果章节最后用两图同屏对照，不另加第四步。
只讲完成这件事所需的内容，不扩展安装、正则、聚合或其他 jq 用法；原始 JSON 留给截图，旁白用自然语言说明，不逐字符念括号或管道符。

## 准备输入
本次实际使用本机已有的 jq，版本为 ''' + version + '''。文件 tasks.json 是一个 JSON 数组。
里面三条任务：T01 整理资料，done 为 true；T02 生成草稿，done 为 false；T03 检查输出，done 为 true。
id 是编号，task 是任务名，done 表示是否完成。我们要保留完成的任务，结果仍存成 JSON 数组。

## 执行筛选
在 tasks.json 所在目录实际执行：`''' + displayed[0] + '''`。
这里 .[] 取出数组中的每个对象，select(.done) 只保留 done 为 true 的对象，外层方括号把结果重新收成数组；大于号把结果写进 done.json。
命令执行成功后，接着用 `jq . done.json` 打开查看结果。本次不用安装工具，也不需要模型参与筛选。

## 查看结果
done.json 是本次命令真实产生的文件，只留下 T01 整理资料、T03 检查输出；这两条 done 都为 true。编号、任务名、完成标记按原内容保留。
输入的 T02 生成草稿因为 done 为 false，没有进入输出。输入三条，输出两条，变化只在筛掉了未完成任务。
最后把实际输入与实际输出截图并排对照：左边三条任务，右边只保留已完成的两条。筛选结果仍是数组，可以继续交给后续脚本处理。

## 收尾
准备任务清单，执行 jq 筛选，得到已完成任务文件。这次任务完成。

## 来源
jq 1.7 官方手册：https://jqlang.org/manual/v1.7/
'''
    report_path = out / 'output/radar/2026-11-01_ai_opensource_radar.md'
    report_path.write_text(report)
    context = {'ids': [row['id'] for row in images], 'owner': owner, 'mode': 'adapt', 'revision': 1,
        'title': 'jq 实际任务筛选', 'audience': '开发者和工具使用者',
        'goal': '用真实任务清单串起输入截图、jq 筛选代码、真实输出截图和同屏对照，沿用栏目问候、三步转场及品牌包装。'}
    report_meta.META_DIR = str(out / 'state')
    report_meta.META_FILE = str(out / 'state/report_meta.jsonl')
    report_meta.append_record(str(report_path.relative_to(out)), 'ai_media/daily_opensource_radar', extra={'user_materials': context})
    previous = json.loads((ROOT / 'spec/acceptance/tutorial-io-20261101/evidence/verification.json').read_text())['source_sha256']
    current = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in previous}
    matrix.save(out / 'preparation.json', {'actual_operation': json.loads((demo / 'operation.json').read_text()),
        'demo': str(demo), 'report': str(report_path), 'image_records': images, 'material_context': context,
        'displayed_commands': displayed, 'automatic_capture_register_match_render': True,
        'pipeline_source_sha256': current, 'same_production_source_as_previous_tool': current == previous,
        'manual_paragraph_assignments': 0, 'manual_script_or_storyboard': False, 'model_calls': 0})
    print(json.dumps({'prepared': str(out), 'tool': version, 'images': len(images)}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    out = args.output.resolve()
    if args.run:
        matrix.run(out)
    elif args.verify:
        verifier.verify(out)
        proof_path = out / 'evidence/verification.json'
        proof = json.loads(proof_path.read_text())
        prepared = json.loads((out / 'preparation.json').read_text())
        assert proof['source_sha256'] == prepared['pipeline_source_sha256']
        assert prepared['same_production_source_as_previous_tool']
        proof.update(tool='jq', tool_version=prepared['actual_operation']['version'],
                     displayed_commands_checked=prepared['displayed_commands'],
                     same_production_source_as_previous_tool=True)
        matrix.save(proof_path, proof)
    else:
        prepare(out)

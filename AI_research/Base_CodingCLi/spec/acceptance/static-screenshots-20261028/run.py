"""Three real static-source samples through normal automatic preparation and rendering."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
AUTO = ROOT.parents[1] / "autovideo"
sys.path[:0] = [str(ROOT), str(AUTO)]
parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--case', choices=['product', 'opensource', 'documentation'], action='append')
parser.add_argument('--reuse-from', type=Path, help='Reuse only the isolated sample ledger and verified screenshot cache')
args = parser.parse_args()
out = args.output.resolve()
out.mkdir(parents=True, exist_ok=False)
for folder in ('config', 'state', 'jobs', 'output', 'logs'):
    (out / folder).mkdir()
state_dir = (args.reuse_from.resolve() / 'state') if args.reuse_from else out / 'state'
os.environ.update(AREC_NO_AUTOPILOT_LOOP='1', AI_RESEARCH_ENV='development',
                  AI_RESEARCH_WEB_CONFIG=str(out / 'web.yaml'))

import httpx
import yaml
from fastapi.testclient import TestClient
from core.config import load_system_config
from core.video import image_quality, length, store
from core.video.autovideo import AutoVideoClient
from tests.golden_video_cells import install_recipes
from g3.app import create_app
from g3.config import Settings

install_recipes(out / 'config')
system = load_system_config(str(ROOT / 'config'))
system['video'] = {'visual_materials': {
    'enabled': True, 'version': 3, 'max_pages': 3, 'max_images': 3, 'max_seconds': 180,
    'attempts_per_paragraph': 2, 'ocr_review': True, 'vision_section': 'visual_ai',
    'vision_required': True, 'vision_timeout': 60, 'max_model_calls': 3}, 'poll_seconds': .5}
config = out / 'config/system.yaml'
config.write_text(yaml.safe_dump(system, allow_unicode=True))
config.chmod(0o600)
(out / 'web.yaml').write_text(yaml.safe_dump({
    'auth': {'secret_key': uuid4().hex + uuid4().hex, 'cookie_secure': False},
    'paths': {name + '_dir': str(state_dir if name == 'state' else out / name)
              for name in ('config', 'state', 'jobs', 'output', 'logs')}}))
from web.services import video_render

store.use_state_dir(str(state_dir))
store.init_db()
image_quality.ocr_binary(timeout=90)
cases = {
    'product': {
        'title': '看清 n8n 的工作流画布',
        'url': 'https://docs.n8n.io/try-it-out/quickstart/',
        'paragraphs': [
            '介绍自动化工具时，先看一张真实的工作流界面，能更快理解它怎样把任务串起来。',
            '这是 n8n 官方入门教程里的工作流画布。先看整体：节点放在同一张画布上，连线把各个步骤串在一起。',
            '接着沿着 n8n 的工作流看。触发步骤让任务开始，后面的节点继续处理数据。讲到当前这一步，就把注意力放在相应的节点和连线上。',
            '最后回到这张画布。记住的重点是任务怎样从一个步骤走向下一个步骤；界面让这条路径有了具体的位置。',
        ]},
    'opensource': {
        'title': '从真实代码看懂 MarkItDown',
        'url': 'https://github.com/microsoft/markitdown',
        'paragraphs': [
            '想理解一个文件转换项目，可以先看它给出的最小调用。这里直接打开项目的 README。',
            '这是 MarkItDown 的 Python 示例。先导入 MarkItDown，再创建转换器。输入文件从 convert 这一行进入，接下来得到转换结果。',
            '再看 MarkItDown 示例的最后一行：它把结果中的 Markdown 文本打印出来。输入文件、转换调用和文本结果，在这几行代码里连成一条路径。',
            '回到完整示例，这次要记住的是输入怎样进入转换器，以及输出从哪里取得。项目页面中的真实代码，就能承担这段解释。',
        ]},
    'documentation': {
        'title': '用真实文档解释元素截图',
        'url': 'https://playwright.dev/python/docs/screenshots#element-screenshot',
        'paragraphs': [
            '网页截图可以用于保存页面中的一块具体内容。这个例子直接看官方文档里的调用方式。',
            '这是 Playwright 文档中的元素截图示例。先用 locator 定位页面里的 header 元素，再调用 screenshot，把这个元素保存成图片。',
            '接着看 Playwright 这行代码的两个位置：前面选择要截取的元素，后面的 path 指定保存文件。定位对象和保存结果，在同一行里接起来。',
            '最后回到这段示例。理解这个用法，只需要看清选择哪个元素，以及图片保存在哪里；旁白和框选一起引导阅读。',
        ]},
}
if args.case:
    cases = {key: cases[key] for key in args.case}
engine_data = AUTO / 'g3/data/acceptance-static-screenshots-20261028' / uuid4().hex
engine_data.mkdir(parents=True)
shutil.copyfile(AUTO / 'g3/data/llm-settings.json', engine_data / 'llm-settings.json')
(engine_data / 'llm-settings.json').chmod(0o600)
results = {}
with TestClient(create_app(Settings(data_dir=engine_data, runner_timeout_seconds=1800), start_worker=True)) as engine:
    def transport(request):
        response = engine.request(request.method, request.url.raw_path.decode(), content=request.content, headers=dict(request.headers))
        return httpx.Response(response.status_code, content=response.content, headers=response.headers)
    client = AutoVideoClient('http://127.0.0.1:8011', transport=httpx.MockTransport(transport))
    video_render.get_client = lambda: client
    for key, case in cases.items():
        work = out / key
        work.mkdir()
        report = out / 'output' / (key + '.md')
        report.write_text('# ' + case['title'] + '\n\n' + '\n\n'.join(case['paragraphs'])
                          + '\n\n## 参考来源\n\n1. ' + case['title'] + ' | 一手 | 持续更新 | ' + case['url'] + '\n')
        script = store.create_script('deep_dive', {'title': case['title'], 'fields': {}}, created_by='static-source-sample')
        sections = [{'title': case['title'], 'paragraphs': [
            {'text': text, 'citations': [case['url']] if index in (1, 2) else [], 'evidence': 'CITED' if index in (1, 2) else 'UNCITED'}
            for index, text in enumerate(case['paragraphs'])]}]
        store.add_version(script['id'], title=case['title'], sections=sections,
                          length_check=length.measure(sections, 1), edit_type='EDITED')
        store.update_script(script['id'], status='APPROVED', approved_version=1, report_path=str(report))
        render = video_render.create(store.get_script(script['id']), voice_id='zf_xiaoxiao', production_mode='fast')
        print(json.dumps({'case': key, 'render_id': render['id'], 'engine_data': str(engine_data), 'state': 'started'}, ensure_ascii=False), flush=True)
        started = time.monotonic()
        status = video_render.process(render['id'], client=client)
        final = store.get_render(render['id'])
        (work / 'render.json').write_text(json.dumps(final, ensure_ascii=False, indent=2))
        jid = final.get('autovideo_job_id')
        job = engine.get(f'/api/jobs/{jid}').json() if jid else {}
        (work / 'job.json').write_text(json.dumps(job, ensure_ascii=False, indent=2))
        if jid:
            for name in ('video.mp4', 'content.mp4', 'video-ir.json', 'qa.json', 'package-qa.json'):
                response = engine.get(f'/api/jobs/{jid}/artifacts/{name}')
                if response.status_code == 200:
                    (work / name).write_bytes(response.content)
            for name in ('explanation-plan.json', 'scene-assembly.json', 'shot-director.json', 'production-execution.json'):
                paths = sorted((engine_data / 'jobs' / jid / 'attempts').glob('*/g2-run/artifacts/' + name))
                if paths:
                    shutil.copyfile(paths[-1], work / name)
        mapped = []
        if (work / 'video-ir.json').exists():
            ir = json.loads((work / 'video-ir.json').read_text())
            mapped = [s for s in ir['scenes'] if s['component'] in ('object_showcase', 'material_collage')]
        decode = {}
        for name in ('video.mp4', 'content.mp4'):
            if (work / name).exists():
                with (work / (name + '.decode.log')).open('w') as log:
                    checked = subprocess.run([str(AUTO / 'g0/darwin_arm64/ffmpeg'), '-v', 'error', '-i', str(work / name), '-f', 'null', '-'], stdout=log, stderr=log)
                decode[name] = checked.returncode
        results[key] = {'status': status, 'render_id': render['id'], 'engine_job_id': jid,
            'engine_data': str(engine_data), 'images': len(final.get('visuals', {}).get('selection', {}).get('materials', [])),
            'mapped_scenes': len(mapped), 'decode': decode, 'seconds': round(time.monotonic()-started, 2),
            'error': final.get('error_message'), 'source_url': case['url']}
        (out / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
        print(json.dumps({'case': key, **results[key]}, ensure_ascii=False), flush=True)
assert all(r['status'] == 'COMPLETED' and r['images'] and r['mapped_scenes'] and all(v == 0 for v in r['decode'].values()) for r in results.values()), results

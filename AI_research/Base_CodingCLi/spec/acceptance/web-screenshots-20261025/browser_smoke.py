import json
import os
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

RESEARCH = Path(__file__).resolve().parents[3]
WORKSPACE = RESEARCH.parents[1]
sys.path[:0] = [str(RESEARCH), str(WORKSPACE / 'autovideo')]
import argparse
parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
OUT = parser.parse_args().output.resolve()
OUT.mkdir(exist_ok=False)
for name in ('jobs', 'config', 'output', 'state', 'logs'):
    (OUT / name).mkdir()
os.environ['AREC_NO_AUTOPILOT_LOOP'] = '1'
os.environ['AI_RESEARCH_ENV'] = 'development'
os.environ['AI_RESEARCH_WEB_CONFIG'] = str(OUT / 'web.yaml')
import yaml
(OUT / 'web.yaml').write_text(yaml.safe_dump({
    'server': {'host': '127.0.0.1', 'port': 18766},
    'auth': {'secret_key': uuid4().hex + uuid4().hex, 'cookie_secure': False},
    'paths': {name + '_dir': str(OUT / name) for name in ('jobs', 'config', 'output', 'state', 'logs')}
}))
from tests import golden_video_cells as golden
golden.install_recipes(OUT / 'config')
from core.video import length, screenshots, store
store.use_state_dir(str(OUT / 'state'))
store.init_db()
from web.auth import db as user_db
from web.auth.password import hash_password
from web.indexer import db as index_db
user_db._DB_PATH_OVERRIDE = str(OUT / 'state/users.db')
user_db.init_db()
password = uuid4().hex
user_db.create_user('acceptance', hash_password(password), 'admin')
index_db._DB_PATH_OVERRIDE = str(OUT / 'state/reports.db')
index_db.init_db()
script = store.create_script('deep_dive', {'title': '网页截图与段落配图验收', 'fields': {}})
sections = [{'title': '截图与讲解', 'paragraphs': [
    {'text': text, 'citations': ['https://playwright.dev/python/docs/screenshots'], 'evidence': 'CITED'}
    for text in ('先介绍来源与适用范围。', '现在展示官方网页中的截图功能。', '最后总结与核对来源。')]}]
store.add_version(script['id'], title='网页截图与段落配图验收', sections=sections,
                  length_check=length.measure(sections, 6), edit_type='EDITED', meta={'checks': {'mode': 'on_request'}})
report = OUT / 'output/source.md'
report.write_text('# 来源记录\n\n## 参考来源\n\n1. Playwright 官方截图说明 | 一手 | 持续更新 | https://playwright.dev/python/docs/screenshots\n')
store.update_script(script['id'], status='DRAFT', report_path=str(report))
import httpx
import uvicorn
from fastapi.testclient import TestClient
from g3.app import create_app as engine_app
from g3.config import Settings
from core.video.autovideo import AutoVideoClient
from web.server import create_app
from web.services import video_render
from playwright.sync_api import sync_playwright

with TestClient(engine_app(Settings(data_dir=OUT / 'engine-data'), start_worker=False,
                          preflight=lambda _: {'ready': True, 'checks': {}, 'blockers': []})) as engine:
    def transport(request):
        result = engine.request(request.method, request.url.raw_path.decode(), content=request.content, headers=dict(request.headers))
        return httpx.Response(result.status_code, content=result.content, headers=result.headers)
    engine_client = AutoVideoClient('http://127.0.0.1:8011', transport=httpx.MockTransport(transport))
    video_render.get_client = lambda: engine_client
    server = uvicorn.Server(uvicorn.Config(create_app(), host='127.0.0.1', port=18766, lifespan='off', log_level='warning'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started: break
        time.sleep(.05)
    assert server.started
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='chrome', headless=True)
            context = browser.new_context(viewport={'width': 1480, 'height': 1100}, locale='zh-CN')
            login = context.request.post('http://127.0.0.1:18766/api/auth/login', data={'username': 'acceptance', 'password': password})
            assert login.status == 200, login.text()
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:18766/video/{script['id']}?tab=draft")
            toggle = page.get_by_role('button', name='网页截图与段落配图', exact=False)
            toggle.click()
            page.get_by_label('截图来源网址').fill('https://playwright.dev/python/docs/screenshots')
            page.get_by_label('截图范围').select_option('crop')
            for key, value in {'x': 320, 'y': 120, 'width': 1040, 'height': 640}.items():
                page.get_by_label(f'裁切{key}', exact=True).fill(str(value))
            page.get_by_role('button', name='获取网页截图', exact=True).click()
            checkbox = page.get_by_label('使用截图：Screenshots | Playwright Python', exact=True)
            checkbox.wait_for(timeout=120000)
            checkbox.check()
            page.get_by_label('画面说明：Screenshots | Playwright Python', exact=True).fill('官方截图功能说明，2026年10月25日获取')
            page.get_by_label('截图与讲解 · 第2段', exact=False).check()
            page.get_by_label('段落配图形式').select_option('dynamic_collage')
            page.get_by_role('button', name='确认并保存段落配图', exact=True).click()
            page.get_by_text('已保存，下次标准制作用于此版本', exact=True).wait_for()
            page.reload()
            toggle = page.get_by_role('button', name='网页截图与段落配图', exact=False)
            toggle.click()
            checkbox = page.get_by_label('使用截图：Screenshots | Playwright Python', exact=True)
            assert checkbox.is_checked()
            assert page.get_by_label('截图与讲解 · 第2段', exact=False).is_checked()
            assert page.get_by_label('段落配图形式').input_value() == 'dynamic_collage'
            panel = toggle.locator('xpath=..')
            panel.screenshot(path=str(OUT / 'screenshot-panel.png'))
            page.screenshot(path=str(OUT / 'script-page.png'), full_page=True)
            selected = screenshots.selection(script['id'], store.get_version(script['id'], 1))
            assert selected['materials'][0]['placements'] == [{'chapter_index': 0, 'paragraph_index': 1}]
            assert not errors, errors
            (OUT / 'qa.json').write_text(json.dumps({'capture': 'PASS', 'register': 'PASS', 'save': 'PASS',
                'reload': 'PASS', 'page_errors': errors, 'selection': selected}, ensure_ascii=False, indent=2))
            browser.close()
            print('UI capture, registration, save, reload: PASS', flush=True)
    finally:
        server.should_exit = True
        thread.join(10)

"""Capture a local demonstration product and real DOM bounds with Playwright."""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
out = args.output.resolve()
out.mkdir(parents=True, exist_ok=False)
template = '''<!doctype html><html lang="zh"><meta charset="utf-8"><style>
*{box-sizing:border-box}body{margin:0;background:#f4f7f5;color:#203d31;font:28px 'PingFang SC',sans-serif}
header{height:108px;background:white;border-bottom:1px solid #dce6df;display:flex;align-items:center;justify-content:space-between;padding:0 58px}
header strong{font-size:38px}header span{font-size:24px;color:#789184}.badge{border:1px solid #b8d5c4;border-radius:25px;padding:8px 20px;color:#237b50}
main{padding:38px 54px;display:grid;grid-template-columns:770px 520px;gap:30px}h1{font-size:32px;margin:0 0 24px}h2{font-size:28px;margin:0 0 20px}
.panel{padding:28px;background:white;border:1px solid #dce6df;border-radius:18px;margin-bottom:24px}.file{padding:14px 20px;background:#f2f6f3;border-radius:12px;display:flex;justify-content:space-between;margin:12px 0;font-size:25px}
small{font-size:20px;color:#7b9285}.prompt{height:112px;background:#f7f9f7;border:1px solid #d4e1d8;border-radius:14px;padding:20px;line-height:1.7;font-size:27px}
button{border:0;border-radius:12px;background:#1b7b4a;color:white;padding:12px 28px;font-size:24px;margin-top:18px}aside .panel{height:630px}.empty{display:flex;height:450px;align-items:center;justify-content:center;color:#a2b5a9;line-height:1.8;text-align:center}
.answer{background:#eef6ef;padding:22px;border-left:4px solid #227b4c;line-height:1.7;font-size:27px;margin-top:20px}.answer b{display:block;margin-bottom:12px}p{line-height:1.7;font-size:25px;color:#637f6b}
</style><header><strong>FlowNote <span>研究工作台</span></strong><span class="badge">演示项目</span></header>
<main><section><h1>把材料变成下一步行动</h1><div class="panel" id="files"><h2>文件区</h2><div class="file">研究报告.pdf <small>已上传</small></div><div class="file">产品说明.md <small>已上传</small></div><small>在同一个项目中管理材料</small></div>
<div class="panel" id="question"><h2>提问区 · 输入问题</h2><div class="prompt">__QUESTION__</div><button>生成回答</button></div></section>
<aside><div class="panel" id="result"><h2>输出结果</h2>__RESULT__</div></aside></main></html>'''
states = {
    'workspace': ('围绕上传的材料，写下你想了解的问题', '<div class="empty">回答将在这里展开<br>保留清楚的阅读顺序</div>'),
    'input': ('请总结这两份材料，并给出下一步行动。', '<div class="empty">准备好问题<br>开始生成回答</div>'),
    'output': ('请总结这两份材料，并给出下一步行动。', '<div class="answer"><b>材料摘要</b>把产品能力、使用场景和主要变化放在一起。</div><div class="answer"><b>下一步行动</b>先试用核心功能，再比较团队的使用方式。</div>'),
}
records = {}
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True, channel='chrome')
    page = browser.new_page(viewport={'width': 1440, 'height': 900}, device_scale_factor=1)
    for name, (question, result) in states.items():
        html = template.replace('__QUESTION__', question).replace('__RESULT__', result)
        (out/f'{name}.html').write_text(html)
        page.set_content(html)
        page.evaluate('document.fonts.ready')
        page.screenshot(path=str(out/f'{name}.png'))
        regions = {}
        for selector in ('files', 'question', 'result'):
            box = page.locator('#'+selector).bounding_box()
            regions[selector] = {'x1': round(box['x']/1440*1000), 'y1': round(box['y']/900*1000),
                'x2': round((box['x']+box['width'])/1440*1000), 'y2': round((box['y']+box['height'])/900*1000)}
        records[name] = {'file': f'{name}.png', 'width': 1440, 'height': 900, 'regions': regions}
    browser.close()
(out/'pages.json').write_text(json.dumps(records, ensure_ascii=False, indent=2))
print(json.dumps({'screenshots': list(records), 'bounds': 'Playwright locator.bounding_box'}))

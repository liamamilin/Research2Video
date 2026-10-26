"""Capture two local demonstration product pages and their DOM regions."""
import argparse, json
from pathlib import Path
from playwright.sync_api import sync_playwright

parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
template="""<!doctype html><html lang="zh"><meta charset="utf-8"><style>
*{box-sizing:border-box}body{margin:0;font:28px 'PingFang SC',sans-serif;background:#f3f6f3;color:#263d32}
header{padding:32px 55px;background:white;border-bottom:1px solid #dce7dc;display:flex;justify-content:space-between}strong{font-size:38px}small{font-size:22px;color:#7b8c81}main{padding:40px 55px}h1{font-size:32px;margin:0 0 30px}.row{display:flex;gap:30px}.card{background:white;border:1px solid #dce7dc;border-radius:20px;padding:30px;flex:1}h2{font-size:26px;color:#667f6d;margin:0 0 22px}.value{font-size:66px;font-weight:700;color:__COLOR__}p{line-height:1.7;font-size:26px;color:#718176}.workflow{margin-top:30px;background:white;padding:30px;border-radius:20px;border:1px solid #dce7dc}.steps{display:flex;gap:20px;align-items:center}.steps span{background:#f0f5ef;border-radius:12px;padding:20px 28px}button{background:__COLOR__;color:white;font:26px 'PingFang SC';border:0;border-radius:12px;padding:16px 40px;margin-top:28px}
</style><header><strong>__NAME__</strong><small>研映演示页面</small></header><main><h1>用相同指标理解处理方式</h1><div class="row" id="metrics"><div class="card"><h2>单次成本</h2><div class="value">__COST__<small> 元</small></div><p>按当前讲稿展示成本</p></div><div class="card"><h2>响应时间</h2><div class="value">__TIME__<small> 秒</small></div><p>从处理到返回结果</p></div></div><div class="workflow" id="workflow"><h2>处理流程</h2><div class="steps"><span>上传材料</span>→<span>开始处理</span>→<span>查看结果</span></div><button>开始使用</button></div></main></html>"""
records={}
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True,channel='chrome');page=browser.new_page(viewport={'width':1440,'height':900},device_scale_factor=1)
 for key,name,cost,seconds,color in [('local','本地方案','20','8','#17784B'),('cloud','云端方案','25','3','#557DB2')]:
  html=template.replace('__NAME__',name).replace('__COST__',cost).replace('__TIME__',seconds).replace('__COLOR__',color)
  (out/f'{key}.html').write_text(html);page.set_content(html);page.evaluate('document.fonts.ready');page.screenshot(path=str(out/f'{key}.png'))
  regions={}
  for selector in ['metrics','workflow']:
   box=page.locator('#'+selector).bounding_box();regions[selector]={'x1':round(box['x']/1440*1000),'y1':round(box['y']/900*1000),'x2':round((box['x']+box['width'])/1440*1000),'y2':round((box['y']+box['height'])/900*1000)}
  records[key]={'file':f'{key}.png','width':1440,'height':900,'regions':regions,'caption':name}
 browser.close()
(out/'pages.json').write_text(json.dumps(records,ensure_ascii=False,indent=2));print(json.dumps(records,ensure_ascii=False))

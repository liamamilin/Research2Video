"""Build escaped, local side-by-side reading artifact from preserved experiment outputs."""
from html import escape as e
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[4]
BASE = Path('/tmp/content-question-material-round4-20261115')
ARCHIVE = ROOT/'state/acceptance-content-value-20261115/round4'


def data(path):
    return json.loads(path.read_text()) if path.exists() else {}


def paragraphs(text):
    return ''.join('<p>'+e(p).replace('\n','<br>')+'</p>' for p in text.split('\n\n'))


def main():
    ARCHIVE.mkdir(parents=True,exist_ok=True)
    for name in ('inputs','results'):
        shutil.copytree(BASE/name,ARCHIVE/name,dirs_exist_ok=True)
    for name in ('source-manifest.json','input-summary.json','contribution-evidence.json','before.json','after.json','invariants.json'):
        if (BASE/name).exists():
            shutil.copy2(BASE/name,ARCHIVE/name)
    for path in BASE.glob('*.log'):
        shutil.copy2(path,ARCHIVE/path.name)
    sections = []
    for channel,label in [('t05','T05 新题：文案对比'),('ai_tutorial','AI 工具教程'),('company_daily','商业日报')]:
        out = ARCHIVE/'results'/channel
        selection,decision,enriched = (data(out/name) for name in ('selection.json','decision.json','enrichment.json'))
        if not decision:
            continue
        candidates = selection['candidates']
        choices = ''.join('<li><b>'+e(c['title'])+'</b><br>'+e(c['priority_reason'])+'</li>' for c in candidates)
        comparison = ''.join('<li>'+e(s)+'</li>' for s in decision['comparison'])
        gaps = ''.join('<li><b>'+e(g['question'])+'</b><br>'+e(g['missing'])+'<br>预期收益：'+e(g['viewer_change'])+'</li>' for g in decision['gaps'])
        gains = ''.join('<li>'+e(s)+'</li>' for s in enriched.get('contributions',[]))
        unfilled = ''.join('<li>'+e(s)+'</li>' for s in enriched.get('unfilled',[]))
        cards = []
        for arm,title in [('A','A · 同题基线＋原材料'),('C','C · 比较后选题＋原材料'),('B','B · 同题＋按问题补材')]:
            alias = data(out/f'{arm}-alias.json')
            if alias and arm=='C':
                continue
            if alias and arm=='B':
                cards.append('<article><h3>'+e(title)+'</h3><p>本次独立正文读取失败，搜索已返回的正文片段没有被本轮脚本复用。B 的实际输入条件与 '+e(alias['same_as'])+' 相同，未另行生成，不能判断补材是否改善文稿。读取原因与试验策略问题见上方结论。</p></article>')
                continue
            actual = alias.get('same_as',arm)
            path = out/f'{actual}-script.txt'
            text = path.read_text() if path.exists() else '本稿尚未生成成功。'
            note = '<small>与 '+e(actual)+' 共用同一稿，避免随机生成差异。</small>' if alias else ''
            cards.append('<article><h3>'+e(title)+'</h3>'+note+paragraphs(text)+'</article>')
        sources = []
        for source in data(out/'supplement.json').get('added',[]):
            url=source['url']
            link='<a href="'+e(url,quote=True)+'">'+e(source['title'])+'</a>' if url.startswith(('https://','http://')) else e(source['title'])
            sources.append('<li>'+e(source['id'])+' '+link+'<br>'+e(source['question'])+'</li>')
        review=data(out/'blind-reading.json'); mapping=data(out/'blind-labels.json')
        modeltext='本轮由主 Agent 在本地逐篇通读比较，未进行额外模型盲评或观众测试。'
        if review.get('status') in {'not_comparable','approval_blocked'}:
            modeltext=review['comparison']+'\n\n'+review['uncertainty']
        elif review:
            order=' > '.join(' = '.join(mapping.get(x,x) for x in group) for group in review['preference'])
            modeltext='辅助模型偏好：'+order+'\n\n'+review['comparison']+'\n\n局限：'+review['uncertainty']
        sections.append(f'<section id="{channel}"><h2>{label}</h2><p><b>本轮选题：</b>{e(candidates[decision["chosen_index"]-1]["title"])}</p><p>{e(decision["reason"])}</p><details><summary>选题与材料缺口</summary><ol>{choices}</ol><ul>{comparison}</ul></details><details open><summary>补什么、补到了什么</summary><ul>{gaps}</ul><h4>新增材料贡献（模型整理，需结合全文判断）</h4><ul>{gains}</ul><h4>仍未解决</h4><ul>{unfilled}</ul><ul>{"".join(sources)}</ul></details><div class="grid">{"".join(cards)}</div><details><summary>评价方式与限制</summary>{paragraphs(modeltext)}</details></section>')
    review_path=ROOT/'spec/acceptance/content-value-20261115/round4/REVIEW.txt'
    manual=paragraphs(review_path.read_text()) if review_path.exists() else '<p>编辑复核尚未完成。</p>'
    html='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>跨 Job 按问题补材 · 新旧文稿对照</title><style>body{font-family:system-ui,sans-serif;line-height:1.85;margin:0;background:#f5f4ef;color:#202b31}main{max-width:1700px;margin:auto;padding:28px}h1{font-size:30px}h2{margin-top:0}h3{font-size:17px}p{margin:0 0 1em}nav{position:sticky;top:0;background:#202b31;padding:12px 28px;z-index:1}nav a{color:white;margin-right:24px}section,.summary{background:white;padding:26px;border-radius:12px;margin:24px 0;scroll-margin-top:70px}details{background:#f4f6f6;padding:14px 20px;margin:16px 0}summary{cursor:pointer;font-weight:600}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:22px}article{padding:22px;border:1px solid #dce2e2;border-radius:8px}article h3{border-bottom:3px solid #427c77;padding-bottom:12px}small{display:block;color:#626b71;margin-bottom:16px}a{color:#24645e}li{margin-bottom:12px}@media(max-width:1000px){.grid{grid-template-columns:1fr}main{padding:14px}}</style><nav><a href="#summary">结论与局限</a><a href="#t05">T05 新题</a><a href="#ai_tutorial">AI 工具教程</a><a href="#company_daily">商业日报</a></nav><main><h1>换题与跨 Job 补材对照</h1><p>冻结同一批原材料；保持原写稿流程与受众范围修正。每个入口一个选题，隔离内容实验，未接入生产，未制作视频。</p>'''
    html=html.replace('grid-template-columns:repeat(3,minmax(0,1fr))','grid-template-columns:repeat(auto-fit,minmax(340px,1fr))')
    html+='<div class="summary" id="summary">'+manual+'</div>'+''.join(sections)+'</main></html>'
    (ARCHIVE/'comparison.html').write_text(html)
    print(ARCHIVE/'comparison.html')


if __name__=='__main__':
    main()

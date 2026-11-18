"""Export all six controlled manuscript comparisons, without omitting failures."""
import html
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
BASE = Path('/tmp/content-value-scope-20261115')
NAMES = {'flash': 'AI 快讯', 't05': 'T05 横向选择', 't06': 'T06 付费价值'}


def read(path):
    return json.loads(path.read_text())


def esc(value):
    return html.escape(str(value))


def manuscript(value):
    parts = ['<h3>'+esc(value['title'])+'</h3>']
    count = sum(len(p['text']) for s in value['sections'] for p in s['paragraphs'])
    parts.append(f'<p class="meta">正文 {count} 字符</p>')
    for section in value['sections']:
        parts.append('<h4>'+esc(section['title'])+'</h4>')
        parts.extend('<p>'+esc(p['text'])+'</p>' for p in section['paragraphs'])
    return ''.join(parts)


def main():
    judgments = read(HERE/'judgments.json')
    parts = ['''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>原写法＋受众范围修正：第二轮对照</title>
<style>body{font:17px/1.85 system-ui,sans-serif;color:#21352d;background:#f5f4ef;max-width:1440px;margin:auto;padding:30px}
h1,h2,h3,h4{line-height:1.4}h2{margin-top:52px}h3{font-size:21px}h4{font-size:17px;color:#496555}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:20px}.card,details,.verdict{background:#fff;border:1px solid #d9dfd9;border-radius:12px;padding:24px;margin:16px 0}
.verdict{border-left:5px solid #31765e}.meta{font-size:14px;color:#62736a}.label{font-weight:650;color:#27684f}a{color:#176953}p{overflow-wrap:anywhere}summary{cursor:pointer;font-weight:600}
@media(max-width:850px){.pair{grid-template-columns:1fr}body{padding:16px}}</style></head><body>
<h1>保留原写法，只修正受众收窄</h1>
<p>第二轮：AI 快讯、T05、T06 各两题，每题两篇新稿，共12篇。新选题与材料沿用首轮自动结果；两组输入相同，只调整“必须跟着一个人／任务走到底”及其受众范围要求。</p>
<p>标题、开头、口语风格、讲述者、结尾、栏目风格、模型与参考片长保持一致。模型生成有随机差异；这是编辑阅读对照，没有播放、完播或转化数据。未切换生产。</p>
<p><a href="#flash">AI 快讯</a> · <a href="#t05">T05</a> · <a href="#t06">T06</a> · <a href="REVIEW.txt">详细评审与边界</a></p>''']
    parts.append('<div class="verdict"><h2 style="margin-top:0">本轮判断</h2><p>'+esc(judgments['summary'])+'</p></div>')
    records = []
    for channel, label in NAMES.items():
        parts.append('<h2 id="'+channel+'">'+label+'</h2>')
        candidates = read(BASE/'results'/channel/'candidate/selection.json')['candidates']
        for index in (1, 2):
            candidate = candidates[index-1]
            key = f'{channel}-{index}'
            parts.append('<h3>'+str(index)+'. '+esc(candidate['title'])+'</h3><p><b>原定受众：</b>'+esc(candidate['audience'])+'</p>')
            parts.append('<p><b>观看承诺：</b>'+esc(candidate['promise'])+'</p>')
            parts.append('<div class="verdict">'+''.join('<p>'+esc(text)+'</p>' for text in judgments[key])+'</div>')
            parts.append('<div class="pair">')
            record = {'channel':channel,'index':index,'topic':candidate['title'],'judgment':judgments[key]}
            for variant, title in [('control','新选题＋整理材料＋原写法'), ('scoped','相同输入＋原写法＋受众范围修正')]:
                source = BASE/'results'/channel/variant/f'script-{index}.json'
                value = read(source)
                dest = HERE/'results'/channel/variant
                dest.mkdir(parents=True, exist_ok=True)
                for suffix in ('json','txt'):
                    shutil.copy2(source.with_suffix('.'+suffix),dest/source.with_suffix('.'+suffix).name)
                parts.append('<article class="card"><span class="label">'+title+'</span>'+manuscript(value)+'</article>')
                record[variant]={'title':value['title'],'meta':value['meta'],'length_check':value['length_check']}
            parts.append('</div>')
            old = read(HERE.parent/'results'/channel/'baseline'/f'script-{index}.json')
            parts.append('<details><summary>首轮旧选题＋旧材料＋原写法（额外参考，题目不同）</summary>'+manuscript(old)+'</details>')
            records.append(record)
    parts.append('</body></html>')
    (HERE/'comparison.html').write_text(''.join(parts))
    (HERE/'summary.json').write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n')


if __name__ == '__main__':
    main()

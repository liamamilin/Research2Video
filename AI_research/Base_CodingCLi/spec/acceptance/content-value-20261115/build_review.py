"""Export full, escaped comparison documents and compact receipts, without raw source dumps."""
from __future__ import annotations

import html
import json
from pathlib import Path
import shutil

BASE = Path('/tmp/content-value-20261115')
HERE = Path(__file__).resolve().parent
NAMES = {'flash': 'AI 快讯', 't05': 'T05 横向选择', 't06': 'T06 付费价值'}


def read(path):
    return json.loads(path.read_text()) if path.exists() else None


def escape(value):
    return html.escape(str(value))


def script_html(value):
    if value is None:
        return '<p>尚未生成</p>'
    parts = ['<h3>' + escape(value['title']) + '</h3>']
    chars = sum(len(p['text']) for s in value['sections'] for p in s['paragraphs'])
    parts.append(f'<p class="meta">正文 {chars} 字符 · 模型 {escape(value.get("meta", {}).get("model", ""))}</p>')
    for section in value['sections']:
        parts.append('<h4>' + escape(section['title']) + '</h4>')
        parts.extend('<p>' + escape(p['text']) + '</p>' for p in section['paragraphs'])
    return ''.join(parts)


def main():
    dest = HERE / 'results'
    dest.mkdir(exist_ok=True)
    parts = ['''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>选题与文稿对照</title>
<style>body{font:17px/1.85 system-ui,sans-serif;color:#20302a;background:#f5f4ef;margin:0;padding:32px;max-width:1500px;margin:auto}
a{color:#176953}h1,h2,h3,h4{line-height:1.4}h2{margin-top:56px}h3{font-size:21px}h4{font-size:17px;color:#466254}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:20px}.card,details{background:white;border:1px solid #ddd;border-radius:12px;padding:24px;margin:16px 0}
.meta{font-size:14px;color:#66736b}.tag{font-size:14px;background:#e6eee8;padding:5px 10px;border-radius:6px}
table{width:100%;border-collapse:collapse;background:white}th,td{border:1px solid #ddd;padding:12px;text-align:left;vertical-align:top}
summary{cursor:pointer;font-weight:600}p{overflow-wrap:anywhere}@media(max-width:850px){.pair{grid-template-columns:1fr}body{padding:16px}}
</style><h1>新旧选题与文稿对照</h1>
<p>范围：AI 快讯、T05、T06。使用冻结的真实研究材料与同一模型配置，保持既有讲述者。未出片、未发布、未切换生产。</p>
<p>旧版是原报告前两条候选重新写稿，新版从同一批原始材料重新选题并组织写法。每个入口另附一组同题、同材料、同参考时长对照，以观察写法差异。两组对照都不等同于观看数据。</p>
<p><a href="#flash">AI 快讯</a> · <a href="#t05">T05</a> · <a href="#t06">T06</a></p>
<div class="card"><h2 style="margin-top:0">阅读结论</h2>
<p>选题与材料组织值得继续；整套新写法暂不采用。以下是编辑判断，尚无播放或转化验证。</p>
<table><tr><th>入口</th><th>保留</th><th>本轮不足</th></tr>
<tr><td>AI 快讯</td><td>使用习惯发生什么变化</td><td>来源仍偏技术；API稿仍堆计费单位，部分判断扩大了材料结论</td></tr>
<tr><td>T05</td><td>普通用户如何搭配免费与付费工具</td><td>正文混入开发者价格；论文案例不能证明“只订一个不够”</td></tr>
<tr><td>T06</td><td>解锁新任务与增加使用额度的区别</td><td>仍是参数和功能清单，新写法没有稳定改善</td></tr></table>
<p>原版咖啡文案对比有具体差异，仍值得保留；覆盖面不能代替趣味和具体收获。完整采用与放弃理由见 <a href="REVIEW.txt">逐稿评审</a>。</p></div>''']
    summary = []
    for channel, name in NAMES.items():
        folder = BASE/'results'/channel
        selection = read(folder/'candidate/selection.json')
        old_topics = read(folder/'baseline/original-candidates.json') or []
        parts.append(f'<h2 id="{channel}">{escape(name)}</h2>')
        parts.append('<details><summary>原报告候选顺序</summary><ol>' + ''.join(
            '<li>'+escape(t['title'])+'</li>' for t in old_topics) + '</ol></details>')
        if selection:
            target = dest/channel
            target.mkdir(exist_ok=True)
            shutil.copy2(folder/'candidate/selection.json', target/'selection.json')
            parts.append('<h3>新版候选与取舍</h3><table><tr><th>选题</th><th>观众与观看理由</th><th>收获与商业关联</th></tr>')
            for item in selection['candidates']:
                parts.append('<tr><td>'+escape(item['title'])+'<p class="meta">'+escape(item['priority_reason'])+'</p></td><td>'
                             +escape(item['audience'])+'<p>'+escape(item['stake'])+'</p><p>'+escape(item['novelty'])+'</p></td><td>'
                             +escape(item['promise'])+'<p>'+escape(item['commercial'])+'</p></td></tr>')
            parts.append('</table><p>取舍：'+escape(selection['tradeoffs'])+'</p>')
        for index in (1, 2):
            pair = {}
            parts.append(f'<h3>第 {index} 组：选题与写法的整体变化</h3><div class="pair">')
            for variant, label in [('baseline', '旧候选＋原写法'), ('candidate', '新候选＋新写法')]:
                path = folder/variant/f'script-{index}.json'
                value = read(path)
                if value:
                    target = dest/channel/variant
                    target.mkdir(parents=True, exist_ok=True)
                    for extension in ('json','txt'):
                        shutil.copy2(path.with_suffix('.'+extension), target/path.with_suffix('.'+extension).name)
                    pair[variant] = {'title': value['title'], 'meta':value.get('meta',{}), 'length_check':value.get('length_check',{})}
                parts.append('<article class="card"><span class="tag">'+label+'</span>'+script_html(value)+'</article>')
            parts.append('</div>')
            summary.append({'channel':channel, 'index':index, **pair})
        matched_path = folder/'matched/script-1.json'
        matched = read(matched_path)
        if matched:
            target = dest/channel/'matched'; target.mkdir(parents=True,exist_ok=True)
            for ext in ('json','txt'):
                shutil.copy2(matched_path.with_suffix('.'+ext),target/matched_path.with_suffix('.'+ext).name)
            parts.append('<details><summary>同一新选题、同一材料，使用原写法的完整稿件</summary>'
                         +script_html(matched)+'<p class="meta">与上方新版第一条配对，输入主题、受众、材料和参考时长相同；模型生成仍有随机差异。</p></details>')
        first_attempt = folder/'candidate/attempt-1'
        if first_attempt.exists():
            parts.append('<details><summary>保留的首轮新稿：内部备忘进入口播、参考片长漏传</summary>')
            for index in (1, 2):
                path = first_attempt/f'script-{index}.json'
                value = read(path)
                if value:
                    target = dest/channel/'attempt-1';target.mkdir(parents=True,exist_ok=True)
                    for ext in ('json','txt'):
                        shutil.copy2(path.with_suffix('.'+ext),target/path.with_suffix('.'+ext).name)
                    parts.append(script_html(value))
            parts.append('</details>')
    parts.append('</html>')
    (HERE/'comparison.html').write_text(''.join(parts))
    (HERE/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()

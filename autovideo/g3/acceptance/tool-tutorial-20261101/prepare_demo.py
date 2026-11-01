"""Run the real tutorial command against an authored local HTML document."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
import subprocess


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--tool-python', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    source = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>项目周报</title><style>
body{margin:56px;color:#20372d;background:#fff;font:32px/1.5 "Hiragino Sans GB",sans-serif}
h1{font-size:62px;margin:0 0 24px;color:#17784b}h2{font-size:38px;margin:20px 0 12px}
ul{margin:0 0 28px;padding-left:42px}table{border-collapse:collapse;width:100%}
th,td{border:2px solid #cbd8ce;padding:14px 24px;text-align:left}th{background:#edf4ee}
</style><body><header id="summary"><h1>项目周报</h1><h2>已完成</h2>
<ul><li>整理项目资料</li><li>完成脚本联调</li></ul></header>
<section id="tasks"><h2>下一步</h2><table><thead><tr><th>任务</th><th>状态</th></tr></thead>
<tbody><tr><td>转换文档</td><td>进行中</td></tr><tr><td>整理结果</td><td>待开始</td></tr></tbody></table></section>
</body></html>'''
    (out / 'weekly.html').write_text(source)
    command = [str(args.tool_python.absolute()), '-m', 'markitdown', 'weekly.html', '-o', 'weekly.md']
    run = subprocess.run(command, cwd=out, capture_output=True, text=True)
    (out / 'conversion.log').write_text(run.stdout + run.stderr)
    run.check_returncode()
    markdown = (out / 'weekly.md').read_text()
    lines = markdown.splitlines(keepends=True)
    table_at = next(i for i, line in enumerate(lines) if line.lstrip().startswith('|'))
    before, table = ''.join(lines[:table_at]), ''.join(lines[table_at:])
    size = min(36, int(720 / max(1, len(lines)) / 1.35))
    view = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>weekly.md</title>
<style>body{{margin:42px 56px;background:#fff;color:#20372d;font-family:"Hiragino Sans GB",sans-serif}}
header{{font-size:28px;color:#61756a;border-bottom:2px solid #dce6dc;padding-bottom:20px}}
pre{{font:{size}px/1.35 Menlo,"Hiragino Sans GB",monospace;white-space:pre-wrap;margin:26px 0 0}}
pre span{{display:block}}</style><header>weekly.md · MarkItDown 实际输出</header>
<pre><span id="summary">{html.escape(before)}</span><span id="tasks">{html.escape(table)}</span></pre></html>'''
    (out / 'output-view.html').write_text(view)
    version = subprocess.check_output([str(args.tool_python), '-c',
        'import importlib.metadata; print(importlib.metadata.version("markitdown"))'], text=True).strip()
    receipt = {'tool': 'markitdown', 'version': version, 'command': 'markitdown weekly.html -o weekly.md',
        'exit_code': run.returncode, 'model_calls': 0, 'input_authored_for_tutorial': True,
        'output_from_actual_command': True, 'source': 'https://github.com/microsoft/markitdown',
        'sha256': {name: hashlib.sha256((out / name).read_bytes()).hexdigest()
                   for name in ['weekly.html', 'weekly.md']}}
    (out / 'conversion.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'version': version, 'output': str(out), 'markdown': markdown}, ensure_ascii=False))


if __name__ == '__main__':
    main()

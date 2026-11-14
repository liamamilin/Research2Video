"""Inspect actual automatic plans using the production browser font and layout.

This records which expression the normal renderer selects. MP4 review remains
separate; capacity checks alone do not prove an improvement in viewing.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / 'g3/data/validation/daily-effects-20261112'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--job', required=True)
    args = parser.parse_args()
    run = ROOT / 'g3/data/jobs' / args.job / 'attempts/1/g2-run'
    props_path = run / 'artifacts/render-props.json'
    props = json.loads(props_path.read_text())
    out = BASE / 'e2e' / args.job
    out.mkdir(parents=True, exist_ok=True)
    source = run / 'renderer/src'
    checks = {}
    for name in ('ObjectFlow.tsx', 'RelationIndex.tsx', 'RelationLayout.ts', 'TextFit.ts'):
        actual, trusted = (source / name).read_bytes(), (ROOT / 'g1/renderer/src' / name).read_bytes()
        checks[name] = {'matches_trusted_source': actual == trusted, 'sha256': hashlib.sha256(actual).hexdigest()}
        assert actual == trusted, f'Rendered source differs: {name}'
    entry = out / 'inspect.ts'
    entry.write_text(
        'import {flowPresentation} from ' + json.dumps(str(source / 'RelationLayout')) + ';\n'
        + 'import {namedRelationLayout} from ' + json.dumps(str(source / 'RelationIndex')) + ';\n'
        + 'const p=JSON.parse(document.getElementById("input").textContent);\n'
        + 'const rows=p.ir.scenes.filter(s=>s.component==="flow_diagram").map(s=>{'
        + 'const scale=p.fastCardBackgrounds ? (s.image_reading?531/610:531/590):1;'
        + 'const before=flowPresentation(s,scale), named=namedRelationLayout(s.props,scale);'
        + 'return {id:s.id,title:s.props.title,start_seconds:s.start_frame/p.ir.video.fps,end_seconds:s.end_frame/p.ir.video.fps,'
        + 'scale,before_mode:before.mode,selected:before.mode==="index"&&named.fits?"named":before.mode,'
        + 'nodes:s.props.nodes.length,edges:s.props.edges.length,groups:named.rows.length,'
        + 'height:named.height,named_fits:named.fits,footer_lines:named.footer.length,'
        + 'labels:s.props.nodes.map(n=>n.label),relations:s.props.edges.map(e=>({from:e.from,to:e.to,label:e.label,relation:e.relation}))};});'
        + 'document.getElementById("result").textContent=JSON.stringify(rows);'
    )
    deps = ROOT / 'g0/vendor/remotion-audiogram/node_modules'
    bundle = out / 'inspect.js'
    subprocess.run([str(deps / '.bin/esbuild'), str(entry), '--bundle', '--platform=browser', f'--outfile={bundle}'],
                   env={**os.environ, 'NODE_PATH': str(deps)}, check=True, capture_output=True)
    page = out / 'inspect.html'
    page.write_text('<meta charset="utf-8"><script type="application/json" id="input">'
                    + json.dumps(props, ensure_ascii=False).replace('<', '\\u003c')
                    + '</script><pre id="result"></pre><script>' + bundle.read_text().replace('</script', '<\\/script') + '</script>')
    with tempfile.TemporaryDirectory(prefix='video-relation-check-') as profile:
        with (out / 'inspect-browser.log').open('w') as log:
            dom = subprocess.check_output([
                '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '--headless', '--disable-gpu',
                '--no-first-run', '--no-default-browser-check', '--disable-background-networking',
                '--disable-component-update', f'--user-data-dir={profile}', '--dump-dom', page.as_uri()], stderr=log, timeout=60).decode()
    match = re.search(r'<pre id="result">(.*?)</pre>', dom, re.S)
    rows = json.loads(html.unescape(match[1]))
    record = {'job': args.job, 'source_checks': checks, 'props_sha256': hashlib.sha256(props_path.read_bytes()).hexdigest(),
              'scenes': rows, 'named_scenes': sum(r['selected'] == 'named' for r in rows)}
    (out / 'relations.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'job': args.job, 'named_scenes': record['named_scenes'], 'scenes': len(rows)}))


if __name__ == '__main__':
    main()

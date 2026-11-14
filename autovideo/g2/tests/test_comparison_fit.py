"""Measured text keeps schema-boundary comparisons complete and in their panel."""
import json
import os
from pathlib import Path
import subprocess
import pytest
from PIL import ImageFont
from g2.process_diagram import FONT
ROOT=Path(__file__).resolve().parents[2]

@pytest.fixture(scope='module')
def fit_runtime(tmp_path_factory):
    out=tmp_path_factory.mktemp('comparison-fit');source=out/'fit.ts';bundle=out/'fit.cjs'
    source.write_text("import {fitComparison} from "+json.dumps(str(ROOT/'g1/renderer/src/TextFit'))+";\n"
        "const x=JSON.parse(process.argv[2]);process.stdout.write(JSON.stringify(fitComparison(x.props,(s,n)=>Array.from(s).reduce((a,c)=>a+x.widths[c]*n,0),x.scale)));\n")
    deps=ROOT/'g0/vendor/remotion-audiogram/node_modules'
    subprocess.run([str(deps/'.bin/esbuild'),str(source),'--bundle','--platform=node',f'--outfile={bundle}'],check=True,capture_output=True,env={**os.environ,'NODE_PATH':str(deps)})
    def run(props,scale):
        chars=set(''.join(p['label']+''.join(p['items']) for p in props.values()));font=ImageFont.truetype(FONT,100)
        return json.loads(subprocess.check_output(['node',str(bundle),json.dumps({'props':props,'scale':scale,'widths':{c:font.getlength(c)/100 for c in chars}})]))
    return run

@pytest.mark.parametrize('text',['完整保留不同输入中的每一个文字','WWWW WWWW --models v2.0 Markdown','中英混排 weekly.html 输出完整文档'])
@pytest.mark.parametrize('scale',[1,1.3])
def test_full_contract_capacity_keeps_every_character_at_readable_size(fit_runtime,text,scale):
    label=(text*5)[:40];item=(text*6)[:58]
    props={'left':{'label':label,'items':[item]*4},'right':{'label':'简短对照','items':['一个结果']}}
    fit=fit_runtime(props,scale)
    assert fit['fits'] and fit['body']>=28 and fit['label']>=30
    for side,data in props.items():
        result=fit['sides'][side]
        assert ''.join(result['labels'])==data['label']
        assert [''.join(lines) for lines in result['items']]==data['items']
        assert result['height']<=fit['height']

"""Direct relationship rows must preserve topology and readable full labels."""
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope='module')
def layout(tmp_path_factory):
    out = tmp_path_factory.mktemp('named-relations')
    deps = ROOT / 'g0/vendor/remotion-audiogram/node_modules'
    source = out / 'probe.ts'
    source.write_text(
        'import {namedRelationLayout} from ' + json.dumps(str(ROOT / 'g1/renderer/src/RelationIndex'))
        + '; const x=JSON.parse(process.argv[2]); console.log(JSON.stringify('
        + 'namedRelationLayout(x.props,x.scale,(s,n)=>Array.from(s).length*n)));'
    )
    bundle = out / 'probe.cjs'
    subprocess.run([str(deps / '.bin/esbuild'), str(source), '--bundle', '--platform=node',
                    f'--outfile={bundle}'], env={**os.environ, 'NODE_PATH': str(deps)},
                   check=True, capture_output=True)

    def run(nodes, edges, scale=1, takeaway='保留方向与条件'):
        props = {'nodes': [{'id': key, 'label': value} for key, value in nodes.items()],
                 'edges': edges, 'takeaway': takeaway}
        return json.loads(subprocess.check_output(
            ['node', str(bundle), json.dumps({'props': props, 'scale': scale})]))
    return run


def edge(key, source, target, label='处理', relation='flow'):
    return {'id': key, 'from': source, 'to': target, 'label': label, 'relation': relation}


def test_shared_endpoints_do_not_invent_a_cross_product(layout):
    edges = [edge('ab', 'a', 'b'), edge('ac', 'a', 'c'), edge('db', 'd', 'b')]
    result = layout(dict(a='来源甲', b='结果乙', c='结果丙', d='来源丁'), edges)
    expanded = {(a['id'], b['id']) for row in result['rows']
                for a in row['left']['items'] for b in row['right']['items']}
    assert expanded == {('a', 'b'), ('a', 'c'), ('d', 'b')}
    assert result['fits']


def test_merge_keeps_every_source_and_single_common_target(layout):
    nodes = {f'n{i}': f'参与方{i}' for i in range(7)} | {'end': '共同结果'}
    edges = [edge(str(i), f'n{i}', 'end', '共同参与') for i in range(7)]
    result = layout(nodes, edges)
    assert result['fits'] and len(result['rows']) == 1
    row = result['rows'][0]
    assert {n['id'] for n in row['left']['items']} == set(nodes) - {'end'}
    assert [n['id'] for n in row['right']['items']] == ['end']
    assert {e['id'] for e in row['edges']} == {e['id'] for e in edges}


def test_return_parallel_and_blocked_conditions_remain_distinct(layout):
    edges = [edge('ab', 'a', 'b', '开始'), edge('ba', 'b', 'a', '返回'),
             edge('blocked', 'a', 'b', '不得重启', 'blocked'),
             edge('retry', 'a', 'b', '可以重试')]
    result = layout({'a': '提交', 'b': '检查'}, edges)
    assert len(result['rows']) == 4
    assert [row['blocked'] for row in result['rows']] == [False, False, True, False]
    assert [e for row in result['rows'] for e in row['edges']] == edges


@pytest.mark.parametrize('scale', [1, .9])
def test_labels_are_complete_and_font_is_not_reduced_to_fit(layout, scale):
    nodes = {'a': '原始输入与附带条件保持完整', 'b': '处理以后保留的实际结果'}
    result = layout(nodes, [edge('ab', 'a', 'b', '通过条件后')], scale)
    assert result['fits'] and result['font'] * scale == pytest.approx(40)
    assert result['height'] <= result['bodyHeight']
    assert result['footerTop'] + len(result['footer']) * 24 / scale * 1.25 <= 890
    shown = {item['id']: ''.join(item['lines']) for row in result['rows']
             for side in ('left', 'right') for item in row[side]['items']}
    assert shown == nodes


def test_capacity_and_isolated_objects_keep_existing_expression(layout):
    nodes = {str(i): '完整保留这个对象的全部条件说明而不截断或缩小' * 3 for i in range(8)}
    result = layout(nodes, [edge(str(i), '0', str(i)) for i in range(1, 8)])
    assert not result['fits'] and result['font'] == 40
    assert not layout({'a': '输入', 'b': '结果', 'alone': '未连接对象'}, [edge('ab', 'a', 'b')])['fits']


def test_normal_render_copies_and_bundles_the_updated_trusted_component(tmp_path, monkeypatch):
    from g2 import render_retry, run_g2

    artifacts = tmp_path / 'artifacts'
    artifacts.mkdir()
    monkeypatch.setattr(run_g2, 'ARTIFACTS', artifacts)
    monkeypatch.setattr(run_g2, 'ROOT', tmp_path)
    monkeypatch.setattr(run_g2, 'PACKAGING_INPUT', None)
    monkeypatch.setattr(run_g2, 'package_request_from_intent', lambda: None)
    monkeypatch.setattr(run_g2, 'packaging_record', lambda: None)
    monkeypatch.setattr(render_retry, 'run', lambda *args, **kwargs: None)
    run_g2.render({'total_frames': 30})
    renderer = tmp_path / 'renderer'
    assert (renderer / 'src/RelationIndex.tsx').read_bytes() == (
        ROOT / 'g1/renderer/src/RelationIndex.tsx').read_bytes()
    deps = ROOT / 'g0/vendor/remotion-audiogram/node_modules'
    subprocess.run([str(deps / '.bin/esbuild'), str(renderer / 'src/index.ts'),
                    '--bundle', '--platform=browser', f'--outfile={tmp_path / "bundle.js"}'],
                   check=True, capture_output=True)


def test_two_line_footer_stays_inside_the_existing_hybrid_picture_area(layout):
    result = layout({'a': '输入', 'b': '结果'}, [edge('ab', 'a', 'b')], .9, takeaway='完整保留结论和适用条件' * 10)
    assert result['fits'] and len(result['footer']) == 2
    assert result['footerTop'] + 2 * 24 / .9 * 1.25 <= 890
    assert result['font'] * .9 == pytest.approx(40)

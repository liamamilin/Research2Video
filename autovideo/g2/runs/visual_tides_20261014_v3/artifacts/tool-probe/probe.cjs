const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const ELK = require('elkjs/lib/elk.bundled.js');
const elk = new ELK();
const fixtures = [
  {id: 'linear', title: '顺序：订单履约', nodes: [['submit', '提交订单'], ['pay', '确认支付'], ['pick', '仓库拣货'], ['pack', '打包出库'], ['deliver', '送达']], edges: [['submit', 'pay'], ['pay', 'pick'], ['pick', 'pack'], ['pack', 'deliver']]},
  {id: 'branch', title: '分支：缓存命中与未命中', nodes: [['request', '请求到达'], ['cache', '查询缓存'], ['read', '读取数据库'], ['write', '写入缓存'], ['return', '返回数据']], edges: [['request', 'cache'], ['cache', 'return', '命中'], ['cache', 'read', '未命中'], ['read', 'write'], ['write', 'return']]},
  {id: 'merge', title: '汇合：音画合成', nodes: [['source', '输入素材'], ['script', '旁白讲稿'], ['plan', '视觉规划'], ['voice', '生成旁白'], ['visual', '生成画面'], ['output', '合成视频']], edges: [['source', 'script'], ['script', 'voice'], ['script', 'plan'], ['plan', 'visual'], ['voice', 'output'], ['visual', 'output']]},
];
const escape = (s) => String(s).replace(/[&<>"']/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;'}[c]));
function graph(fixture) {
  return {
    id: fixture.id,
    layoutOptions: {'elk.algorithm': 'layered', 'elk.direction': 'RIGHT', 'elk.edgeRouting': 'ORTHOGONAL', 'elk.spacing.nodeNode': '60', 'elk.layered.spacing.nodeNodeBetweenLayers': '70', 'elk.padding': '[top=30,left=30,bottom=30,right=30]'},
    children: fixture.nodes.map(([id, text]) => ({id, width: 220, height: 80, displayText: text})),
    edges: fixture.edges.map(([source, target, label], i) => ({id: `edge_${i}`, sources: [source], targets: [target], ...(label ? {labels: [{id: `label_${i}`, text: label, width: 72, height: 28}]} : {})})),
  };
}
function geometry(layout) {
  return {width: layout.width, height: layout.height, nodes: layout.children.map(({id, x, y, width, height}) => ({id, x, y, width, height})), edges: layout.edges.map(({id, sections, labels}) => ({id, sections, labels}))};
}
function svg(layout, title) {
  const paths = layout.edges.flatMap((edge) => (edge.sections || []).map((section) => {
    const pts = [section.startPoint, ...(section.bendPoints || []), section.endPoint];
    return `<polyline points="${pts.map((p) => `${p.x},${p.y}`).join(' ')}" fill="none" stroke="#1bac9a" stroke-width="3" marker-end="url(#arrow)"/>`;
  })).join('');
  const labels = layout.edges.flatMap((e) => (e.labels || []).map((l) => `<rect x="${l.x}" y="${l.y}" width="${l.width}" height="${l.height}" fill="#102536"/><text x="${l.x+l.width/2}" y="${l.y+l.height/2}" text-anchor="middle" dominant-baseline="middle" fill="#9ce5d9" font-size="22">${escape(l.text)}</text>`)).join('');
  const nodes = layout.children.map((n) => `<rect x="${n.x}" y="${n.y}" width="${n.width}" height="${n.height}" rx="14" fill="#18394f" stroke="#507286"/><text x="${n.x+n.width/2}" y="${n.y+n.height/2}" text-anchor="middle" dominant-baseline="middle" fill="#f3f6fa" font-size="26">${escape(n.displayText)}</text>`).join('');
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${layout.width}" height="${layout.height+76}" viewBox="0 0 ${layout.width} ${layout.height+76}" font-family="PingFang SC, sans-serif"><rect width="100%" height="100%" fill="#102536"/><text x="30" y="40" fill="white" font-size="28">${escape(title)}</text><defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#1bac9a"/></marker></defs><g transform="translate(0 76)">${paths}${labels}${nodes}</g></svg>`;
}
(async () => {
  const report = {kind: 'layout feasibility probe, not product/video acceptance', package: 'elkjs', version: require('elkjs/package.json').version, fixtures_hand_authored: true, checks: []};
  for (const fixture of fixtures) {
    const started = performance.now();
    const layout = await elk.layout(graph(fixture));
    const again = await elk.layout(graph(fixture));
    assert.deepEqual(geometry(layout), geometry(again));
    const stageScale = Math.min(1, 1560 / layout.width, 580 / layout.height);
    for (const node of layout.children) {
      for (const k of ['x', 'y', 'width', 'height']) assert(Number.isFinite(node[k]));
      assert(node.x >= 0 && node.y >= 0 && node.x + node.width <= layout.width && node.y + node.height <= layout.height);
    }
    for (let i = 0; i < layout.children.length; i++) {
      for (let j = i + 1; j < layout.children.length; j++) {
        const a = layout.children[i], b = layout.children[j];
        assert(!(a.x < b.x+b.width && a.x+a.width > b.x && a.y < b.y+b.height && a.y+a.height > b.y), 'node overlap');
      }
    }
    for (const edge of layout.edges) {
      assert(edge.sections?.length > 0, 'edge route missing');
      for (const section of edge.sections) {
        for (const point of [section.startPoint, ...(section.bendPoints || []), section.endPoint]) assert(Number.isFinite(point.x) && Number.isFinite(point.y));
      }
    }
    fs.writeFileSync(path.join(__dirname, `${fixture.id}.layout.json`), JSON.stringify(layout, null, 2));
    fs.writeFileSync(path.join(__dirname, `${fixture.id}.svg`), svg(layout, fixture.title));
    report.checks.push({fixture: fixture.id, nodes: layout.children.length, edges: layout.edges.length, width: layout.width, height: layout.height, fit_to_stage_scale: stageScale, node_text_px_after_fit: 26*stageScale, repeated_layout_identical: true, node_overlap: false, all_edges_routed: true, elapsed_ms_two_layouts: Math.round(performance.now()-started), status: 'PASS'});
  }
  fs.writeFileSync(path.join(__dirname, 'probe-result.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
})().catch((error) => {console.error(error); process.exitCode=1;});

// Trusted offline layout compiler. Model data never becomes executable code.
const fs = require('node:fs');
const ELK = require('elkjs/lib/elk.bundled.js');
const elk = new ELK();
const WIDTH = 1560, HEIGHT = 500;

async function layoutScene(scene) {
  const candidates = [];
  for (const direction of ['RIGHT', 'DOWN']) {
    const graph = {
      id: scene.id,
      layoutOptions: {'elk.algorithm': 'layered', 'elk.direction': direction,
        'elk.edgeRouting': 'ORTHOGONAL', 'elk.spacing.nodeNode': '50',
        'elk.layered.spacing.nodeNodeBetweenLayers': '72',
        'elk.padding': '[top=30,left=30,bottom=30,right=30]', 'elk.randomSeed': '1'},
      children: scene.nodes.map((n) => ({id: n.id, width: n.width, height: n.height})),
      edges: scene.edges.map((e) => ({id: e.id, sources: [e.from], targets: [e.to],
        ...(e.label ? {labels: [{id: `${e.id}_label`, text: e.label, width: e.label_width, height: 34}]} : {})})),
    };
    const laid = await elk.layout(graph);
    candidates.push({laid, direction, scale: Math.min(1, WIDTH/laid.width, HEIGHT/laid.height)});
  }
  candidates.sort((a, b) => b.scale-a.scale);
  const {laid, scale, direction} = candidates[0];
  const dx=(WIDTH-laid.width*scale)/2, dy=(HEIGHT-laid.height*scale)/2;
  const point = (p) => ({x: p.x*scale+dx, y: p.y*scale+dy});
  return {width: WIDTH, height: HEIGHT, font_size: 36*scale, edge_font_size: 28*scale,
    direction, scale, engine: `elkjs-${require('elkjs/package.json').version}`,
    nodes: laid.children.map((n) => ({id: n.id, ...point(n), width: n.width*scale,
      height: n.height*scale, label_lines: scene.nodes.find((source) => source.id===n.id).label_lines})),
    edges: laid.edges.map((e) => {
      if (!e.sections?.length) throw new Error(`No route for ${e.id}`);
      const section=e.sections[0], label=e.labels?.[0];
      return {id: e.id, points: [section.startPoint, ...(section.bendPoints || []), section.endPoint].map(point),
        ...(label ? {label_x: (label.x+label.width/2)*scale+dx, label_y: (label.y+label.height/2)*scale+dy} : {})};
    }),
  };
}

(async () => {
  const request=JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const scenes=[];
  for (const scene of request.scenes) scenes.push({id: scene.id, layout: await layoutScene(scene)});
  process.stdout.write(JSON.stringify({scenes}));
})().catch((error) => {process.stderr.write(String(error)); process.exitCode=1;});

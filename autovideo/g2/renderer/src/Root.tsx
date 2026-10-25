import React from 'react';
import {AbsoluteFill, Audio, Composition, Img, interpolate, staticFile, useCurrentFrame} from 'remotion';

type Beat = {id: string; action: string; target_id: string; frame: number; order: number};
type Scene = {id: string; component: string; purpose: string; visual_intent: object; props: any; start_frame: number; end_frame: number; beats: Beat[]; narration_segment_ids: string[]};
type Segment = {id: string; text: string; start_ms: number; end_ms: number};
type Timeline = {schema_version: string; video: {title: string; width: number; height: number; fps: number; duration_ms: number}; narration: {audio_asset_id: string; segments: Segment[]}; scenes: Scene[]; total_frames: number; audio_public_file: string};

const colors = {bg: '#0B1020', panel: '#121B2E', border: '#293750', text: '#F3F6FC', muted: '#A9B7CD', cyan: '#4ED5E6', blue: '#6798FF', orange: '#FFAA55', red: '#FF6175', green: '#64D8A5'};
const font = '"Hiragino Sans GB", "PingFang SC", sans-serif';

const activeBeats = (scene: Scene, frame: number) => scene.beats.filter((b) => frame >= b.frame);
const has = (beats: Beat[], action: string, target?: string) => beats.some((b) => b.action === action && (target === undefined || b.target_id === target));
const targetVisible = (beats: Beat[], target: string, sceneId: string) => {
  let visible = has(beats, 'reveal', 'scene') || has(beats, 'reveal', target) || has(beats, 'activate_path', target);
  for (const beat of beats) if (beat.target_id === target || beat.target_id === 'scene') {
    if (beat.action === 'hide') visible = false;
    if (beat.action === 'reveal') visible = true;
  }
  return visible || sceneId === '__default_visible__';
};

const Text = ({children, x, y, size = 34, color = colors.text, anchor = 'middle', weight = 500}: any) => <text x={x} y={y} textAnchor={anchor} dominantBaseline="middle" fill={color} fontFamily={font} fontSize={size} fontWeight={weight}>{children}</text>;
const Panel = ({x, y, w, h, fill = colors.panel, stroke = colors.border, r = 24, opacity = 1}: any) => <rect x={x} y={y} width={w} height={h} rx={r} fill={fill} stroke={stroke} strokeWidth="2" opacity={opacity}/>;

function ProcessDiagram({scene, frame}: {scene: Scene; frame: number}) {
  const {layout, nodes, edges, title, takeaway} = scene.props;
  const beats = activeBeats(scene, frame);
  const highlights = beats.filter((b) => b.action === 'highlight');
  const focusFrame = highlights[highlights.length-1]?.frame;
  const focused = new Set(highlights.filter((b) => b.frame === focusFrame).map((b) => b.target_id));
  const revealed = (target: string) => beats.find((b) => b.action === 'reveal' && b.target_id === target);
  const alpha = (target: string) => {
    const beat = revealed(target);
    return beat ? interpolate(frame-beat.frame, [0, 10], [.18, 1], {extrapolateLeft:'clamp', extrapolateRight:'clamp'}) : .18;
  };
  const complete = nodes.every((n: any) => revealed(`node:${n.id}`)) && edges.every((e: any) => revealed(`edge:${e.id}`));
  const nodeMap = Object.fromEntries(nodes.map((n: any) => [n.id, n]));
  const edgeMap = Object.fromEntries(edges.map((e: any) => [e.id, e]));
  const markerId = `${scene.id}_layout_arrow`;
  const positionAlong = (points: any[], t: number) => {
    const lengths = points.slice(1).map((p: any, i: number) => Math.hypot(p.x-points[i].x, p.y-points[i].y));
    let remaining = lengths.reduce((a: number, b: number) => a+b, 0)*t;
    for (let i=0; i<lengths.length; i++) {
      if (remaining <= lengths[i]) {
        const p=points[i], q=points[i+1], f=remaining/Math.max(.001, lengths[i]);
        return {x:p.x+(q.x-p.x)*f, y:p.y+(q.y-p.y)*f};
      }
      remaining-=lengths[i];
    }
    return points[points.length-1];
  };
  return <svg width="1920" height="1080" viewBox="0 0 1920 1080">
    <Text x={960} y={220} size={48} weight={650}>{title}</Text>
    <g transform="translate(180 300)">
      <defs><marker id={markerId} viewBox="0 0 12 12" refX="11" refY="6" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L12,6 L0,12 z" fill={colors.cyan}/></marker></defs>
      {layout.edges.map((route: any) => {
        const edge=edgeMap[route.id], target=`edge:${route.id}`;
        const active=focused.has(target), color=active ? colors.cyan : colors.muted;
        const point=positionAlong(route.points, ((frame-(focusFrame || 0))%55)/55);
        return <g key={route.id} opacity={alpha(target)}>
          <polyline points={route.points.map((p: any) => `${p.x},${p.y}`).join(' ')} fill="none" stroke={color} strokeWidth={active ? 5 : 3} strokeLinejoin="round" markerEnd={`url(#${markerId})`}/>
          {active && <circle cx={point.x} cy={point.y} r={8} fill="#FFFFFF"/>}
          {edge.label && <><rect x={route.label_x-(edge.label.length*layout.edge_font_size+18)/2} y={route.label_y-layout.edge_font_size/2-4} width={edge.label.length*layout.edge_font_size+18} height={layout.edge_font_size+8} rx={6} fill={colors.bg}/><Text x={route.label_x} y={route.label_y} size={layout.edge_font_size} color={color}>{edge.label}</Text></>}
        </g>;
      })}
      {layout.nodes.map((node: any) => {
        const target=`node:${node.id}`, active=focused.has(target);
        return <g key={node.id} opacity={alpha(target)}>
          <Panel x={node.x} y={node.y} w={node.width} h={node.height} fill={active ? '#183C50' : colors.panel} stroke={active ? colors.cyan : colors.border} r={18}/>
          {active && <rect x={node.x+8} y={node.y+8} width={node.width-16} height={node.height-16} rx={12} fill="none" stroke={colors.cyan} opacity={.12+.09*Math.sin(frame/7)}/>}
          {node.label_lines.map((line: string, i: number) => <Text key={i} x={node.x+node.width/2} y={node.y+node.height/2+(i-(node.label_lines.length-1)/2)*layout.font_size*1.28} size={layout.font_size} weight={active ? 650 : 500}>{line}</Text>)}
        </g>;
      })}
    </g>
    {complete && <Text x={960} y={852} size={30} color={colors.muted}>{takeaway}</Text>}
  </svg>;
}

function FlowDiagram({scene, frame}: {scene: Scene; frame: number}) {
  if (scene.props.layout) return <ProcessDiagram scene={scene} frame={frame}/>;
  const {nodes, edges, paths} = scene.props;
  const beats = activeBeats(scene, frame);
  const positions: Record<string, [number, number]> = {};
  nodes.forEach((n: any, i: number) => { positions[n.id] = [[390, 960, 1530, 680, 1240, 960, 390, 1530][i], [440, 440, 440, 680, 680, 800, 800, 800][i]]; });
  const activatedPathIds = paths.filter((p: any) => has(beats, 'activate_path', `path:${p.id}`));
  const activatedEdges = new Set(activatedPathIds.flatMap((p: any) => p.edge_ids));
  const shownNodeIds = new Set(nodes.filter((n: any) => targetVisible(beats, `node:${n.id}`, scene.id)).map((n: any) => n.id));
  const shownEdges = edges.filter((e: any) => targetVisible(beats, `edge:${e.id}`, scene.id) && shownNodeIds.has(e.from) && shownNodeIds.has(e.to));
  const contentVisible = shownNodeIds.size > 0 || shownEdges.length > 0;
  return <svg width="1920" height="1080" viewBox="0 0 1920 1080">
    <defs><marker id="arrow" markerWidth="12" markerHeight="12" refX="9" refY="6" orient="auto"><path d="M0,0 L12,6 L0,12 z" fill={colors.muted}/></marker><marker id="arrowActive" markerWidth="12" markerHeight="12" refX="9" refY="6" orient="auto"><path d="M0,0 L12,6 L0,12 z" fill={colors.cyan}/></marker></defs>
    {contentVisible && <><Text x={960} y={218} size={45} weight={650}>信息如何穿过信道？</Text><Text x={960} y={280} size={25} color={colors.muted}>发送端　→　传输路径　→　接收端</Text></>}
    {shownEdges.map((edge: any) => {
      const from = positions[edge.from], to = positions[edge.to];
      const disrupted = has(beats, 'mark_disrupted', `edge:${edge.id}`);
      const active = activatedEdges.has(edge.id);
      const highlighted = has(beats, 'highlight', `edge:${edge.id}`);
      const stroke = disrupted ? colors.red : highlighted ? colors.orange : active ? colors.cyan : colors.muted;
      const y = from[1] === to[1] ? from[1] : (from[1] + to[1]) / 2;
      const x1 = from[0] + (to[0] > from[0] ? 130 : 0), x2 = to[0] - (to[0] > from[0] ? 130 : 0);
      const pulse = frame % 38;
      const dotX = x1 + (x2 - x1) * (pulse / 38);
      return <g key={edge.id}>
        <line x1={x1} y1={y} x2={x2} y2={y} stroke={stroke} strokeWidth={active || highlighted ? 12 : 6} strokeDasharray={disrupted ? '22 12' : undefined} markerEnd={`url(#${active && !disrupted ? 'arrowActive' : 'arrow'})`} opacity={disrupted ? 0.95 : 0.75}/>
        {active && !disrupted && <circle cx={dotX} cy={y} r="16" fill="#FFFFFF" stroke={colors.cyan} strokeWidth="7"/>}
        {disrupted && <g><path d={`M${dotX-20},${y-35} l18,25 -14,8 20,25 18,-36 -15,5 12,-27`} fill={colors.orange} stroke="#FFF0CD" strokeWidth="3"/><circle cx={dotX} cy={y} r="26" fill="none" stroke={colors.red} strokeWidth="3" opacity="0.65"/></g>}
      </g>;
    })}
    {nodes.map((node: any) => {
      const point = positions[node.id];
      const shown = targetVisible(beats, `node:${node.id}`, scene.id);
      if (!shown) return null;
      const highlighted = has(beats, 'highlight', `node:${node.id}`);
      const active = edges.some((e: any) => activatedEdges.has(e.id) && (e.from === node.id || e.to === node.id));
      return <g key={node.id}>
        <rect x={point[0]-130} y={point[1]-66} width="260" height="132" rx="28" fill={highlighted ? '#243856' : colors.panel} stroke={highlighted ? colors.orange : active ? colors.cyan : colors.border} strokeWidth={highlighted ? 6 : 4}/>
        <Text x={point[0]} y={point[1]} size={node.label.length > 7 ? 30 : 38} weight={650}>{node.label}</Text>
      </g>;
    })}
    {has(beats, 'mark_disrupted') && <g><Panel x={660} y={565} w={600} h={76} fill="#321D2A" stroke={colors.red} r={18}/><Text x={960} y={603} size={27} color={colors.orange}>噪声正在干扰传输</Text></g>}
  </svg>;
}

function TitleCard({scene, frame}: {scene: Scene; frame: number}) {
  const beats = activeBeats(scene, frame), p = scene.props;
  const showTitle = targetVisible(beats, 'title', scene.id);
  const showSubtitle = Boolean(p.subtitle) && targetVisible(beats, 'subtitle', scene.id);
  const emphasize = has(beats, 'emphasize', 'title');
  const emphasizeSubtitle = has(beats, 'emphasize', 'subtitle');
  return <svg width="1920" height="1080">{(showTitle || showSubtitle) && <Panel x={220} y={310} w={1480} h={380} />}{showTitle && <Text x={960} y={450} size={emphasize ? 78 : 68} weight={700}>{p.title}</Text>}{showSubtitle && <Text x={960} y={565} size={emphasizeSubtitle ? 42 : 34} color={emphasizeSubtitle ? colors.orange : colors.muted}>{p.subtitle}</Text>}</svg>;
}

function ConceptReveal({scene, frame}: {scene: Scene; frame: number}) {
  const beats = activeBeats(scene, frame), p = scene.props;
  const anyLineShown = p.lines.some((_: string, i: number) => targetVisible(beats, `line:${i}`, scene.id));
  const titleVisible = targetVisible(beats, 'scene', scene.id) || anyLineShown;
  const titleHighlighted = has(beats, 'highlight', 'title');
  return <svg width="1920" height="1080">{titleVisible && <Text x={960} y={250} size={58} color={titleHighlighted ? colors.orange : colors.text} weight={700}>{p.title}</Text>}{p.lines.map((line: string, i: number) => {
    const id = `line:${i}`, shown = targetVisible(beats, id, scene.id) || targetVisible(beats, 'title', scene.id);
    if (!shown) return null;
    const high = has(beats, 'highlight', id);
    return <g key={i}><Panel x={330} y={350+i*130} w={1260} h={92} stroke={high ? colors.orange : colors.border}/><Text x={960} y={396+i*130} size={34} color={high ? colors.orange : colors.text}>{line}</Text></g>;
  })}</svg>;
}

function Comparison({scene, frame}: {scene: Scene; frame: number}) {
  const beats = activeBeats(scene, frame), p = scene.props, diff = has(beats, 'emphasize_difference', 'diff');
  return <svg width="1920" height="1080">{(['left', 'right'] as const).map((side, idx) => {
    if (!targetVisible(beats, `side:${side}`, scene.id)) return null;
    const x = idx ? 1010 : 220, data = p[side], highlighted = has(beats, 'highlight', `side:${side}`) || diff;
    return <g key={side}><Panel x={x} y={310} w={690} h={500} stroke={highlighted ? colors.orange : colors.border}/><Text x={x+345} y={385} size={42} weight={700}>{data.label}</Text>{data.items.map((item: string, i: number) => <Text key={i} x={x+345} y={485+i*76} size={31} color={highlighted ? colors.orange : colors.text}>{item}</Text>)}</g>;
  })}{diff && <Text x={960} y={870} size={30} color={colors.orange}>两种状态的差异</Text>}</svg>;
}

function Timeline({scene, frame}: {scene: Scene; frame: number}) {
  const beats = activeBeats(scene, frame), p = scene.props, spacing = 1120 / (p.events.length - 1);
  return <svg width="1920" height="1080"><line x1="400" y1="520" x2="1520" y2="520" stroke={colors.border} strokeWidth="8"/>{p.events.map((event: any, i: number) => {
    if (!targetVisible(beats, `event:${event.id}`, scene.id)) return null;
    const x = 400+i*spacing, highlighted = has(beats, 'highlight', `event:${event.id}`);
    return <g key={event.id}><circle cx={x} cy="520" r="24" fill={highlighted ? colors.orange : colors.cyan}/><Text x={x} y="590" size={26} color={highlighted ? colors.orange : colors.text}>{event.label}</Text></g>;
  })}</svg>;
}

function BarChart({scene, frame}: {scene: Scene; frame: number}) {
  const beats = activeBeats(scene, frame), p = scene.props, max = Math.max(...p.values, 1), chartTop = 360, chartBottom = 770, slot = 1200 / p.values.length;
  return <svg width="1920" height="1080"><line x1="360" y1={chartBottom} x2="1560" y2={chartBottom} stroke={colors.muted} strokeWidth="3"/><Text x={960} y={275} size={30} color={colors.muted}>单位：{p.unit}</Text>{p.values.map((v: number, i: number) => {
    if (!targetVisible(beats, `bar:${i}`, scene.id)) return null;
    const h = (chartBottom-chartTop)*(v/max), x = 360+i*slot+20, highlighted = has(beats, 'highlight', `bar:${i}`);
    return <g key={i}><rect x={x} y={chartBottom-h} width={slot-40} height={h} rx="14" fill={highlighted ? colors.orange : colors.blue}/><Text x={x+(slot-40)/2} y={chartBottom-h-32} size={26}>{v}</Text><Text x={x+(slot-40)/2} y={chartBottom+48} size={24}>{p.categories[i]}</Text></g>;
  })}</svg>;
}

function SceneVisual({scene, frame}: {scene: Scene; frame: number}) {
  switch (scene.component) {
    case 'title_card': return <TitleCard scene={scene} frame={frame}/>;
    case 'concept_reveal': return <ConceptReveal scene={scene} frame={frame}/>;
    case 'flow_diagram': return <FlowDiagram scene={scene} frame={frame}/>;
    case 'comparison': return <Comparison scene={scene} frame={frame}/>;
    case 'timeline': return <Timeline scene={scene} frame={frame}/>;
    case 'bar_chart': return <BarChart scene={scene} frame={frame}/>;
    default: return null;
  }
}

function Video({ir, sceneBackdrops = {}}: {ir: Timeline; sceneBackdrops?: Record<string, string>}) {
  const frame = useCurrentFrame();
  const scene = ir.scenes.find((candidate) => frame >= candidate.start_frame && frame < candidate.end_frame) ?? ir.scenes[ir.scenes.length-1];
  const backdropPath = sceneBackdrops[scene.id];
  const safeBackdrop = typeof backdropPath === 'string' && /^assets\/scene-backgrounds\/scene-\d{2}\.png$/.test(backdropPath)
    ? staticFile(backdropPath) : null;
  const sceneProgress = (frame - scene.start_frame) / Math.max(1, scene.end_frame - scene.start_frame - 1);
  const zoom = interpolate(sceneProgress, [0, 1], [1.035, 1.095], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const panX = interpolate(sceneProgress, [0, 1], [10, -10], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const caption = ir.narration.segments.find((s) => {
    const start = Math.floor((s.start_ms*ir.video.fps+500)/1000), end = Math.floor((s.end_ms*ir.video.fps+500)/1000);
    return frame >= start && frame < end;
  });
  return <AbsoluteFill style={{backgroundColor: colors.bg, color: colors.text, fontFamily: font}}>
    {safeBackdrop && <Img src={safeBackdrop} style={{position: 'absolute', zIndex: 0, width: '100%', height: '100%', objectFit: 'cover', opacity: 0.58, filter: 'blur(18px)', transformOrigin: 'center', transform: `translateX(${panX}px) scale(${zoom * 1.08})`}}/>}
    {safeBackdrop && <AbsoluteFill style={{zIndex: 1, background: 'linear-gradient(90deg, rgba(5,10,19,.50), rgba(5,10,19,.36) 50%, rgba(5,10,19,.50)), linear-gradient(0deg, rgba(5,10,19,.72), rgba(5,10,19,.22) 48%, rgba(5,10,19,.46)'}}/>}
    <AbsoluteFill style={{zIndex: 2}}><div style={{position: 'absolute', left: 80, top: 54, color: colors.cyan, fontSize: 24, letterSpacing: 2}}>AUTOVIDEO　/　G2 · 真实模型 / Video IR 0.2</div><div style={{position: 'absolute', right: 82, top: 56, color: colors.muted, fontSize: 22}}>知识动画　·　真实中文旁白</div><SceneVisual scene={scene} frame={frame}/></AbsoluteFill>
    <AbsoluteFill style={{zIndex: 3, justifyContent: 'flex-end', alignItems: 'center', paddingBottom: 92, pointerEvents: 'none'}}>{caption && <div style={{minWidth: 320, maxWidth: 1680, minHeight: 72, padding: '13px 28px', borderRadius: 18, background: 'rgba(5,10,19,.88)', border: `1px solid ${colors.border}`, color: colors.text, textAlign: 'center', fontSize: caption.text.length > 32 ? 30 : 38, lineHeight: 1.4, overflowWrap: 'anywhere', boxSizing: 'border-box'}}>{caption.text}</div>}</AbsoluteFill>
    <Audio src={staticFile(ir.audio_public_file)} volume={Math.pow(10, 0/20)}/>
  </AbsoluteFill>;
}

const defaults: Timeline = {schema_version:'0.2', video:{title:'G1',width:1920,height:1080,fps:30,duration_ms:1000}, narration:{audio_asset_id:'audio',segments:[]}, scenes:[], total_frames:30, audio_public_file:'assets/communications_noise.wav'};

export const Root: React.FC = () => <Composition id="G2CommunicationNoise" component={Video} defaultProps={{ir: defaults, sceneBackdrops: {}}} width={1920} height={1080} fps={30} durationInFrames={30} calculateMetadata={({props}) => ({durationInFrames: (props as any).ir.total_frames, fps:30, width:1920, height:1080})}/>;

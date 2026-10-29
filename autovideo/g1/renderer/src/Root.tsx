import React from 'react';
import {ContentStyleContext, BrandHeaderContext, type ContentStyle, type BrandHeader} from './ContentStyle';
import {DataChart} from './DataChart';
import {ScatterChart} from './ScatterChart';
import {VisualObject, SvgVisualObject, visualFor} from './VisualObjects';
import {ObjectFlow} from './ObjectFlow';
import {cameraAt, carriesPicture, flowRects, linkScenes, transitionAt} from './ShotDirector';
import {DirectedMaterials} from './DirectedMaterials';
import {StoryObjects,objectColor,revealFrames} from './StoryDirection';
import {AbsoluteFill, Audio, Composition, Img, interpolate, staticFile, useCurrentFrame} from 'remotion';
import {CornerBadge, type Badge} from './Packaging';
import {CreativeVideo, type CreativePreset} from './CreativeForms';

type Beat = {id: string; action: string; target_id: string; frame: number; order: number};
type Scene = {id: string; component: string; purpose: string; visual_intent: object; props: any; start_frame: number; end_frame: number; beats: Beat[]; narration_segment_ids: string[]; previous_scene?: Scene; price_layout?: {number_size: number; side_size: number; label_size: number; label_width: number; badge_size: number; unit_size: number; row_gap: number; unit_gap: number; stacked_label: boolean}};
type Segment = {id: string; text: string; start_ms: number; end_ms: number};
type Timeline = {schema_version: string; video: {title: string; width: number; height: number; fps: number; duration_ms: number}; narration: {audio_asset_id: string; segments: Segment[]}; scenes: Scene[]; total_frames: number; audio_public_file: string; caption_cues?: Pick<Segment, 'text' | 'start_ms' | 'end_ms'>[]; presentation?: {preset: 'information_motion' | 'explanation' | CreativePreset; density?: 'large'}; content_structure?: {chapters: {id: string; title: string; narration_segment_ids: string[]}[]}};

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

// Commands, flags, code and versions must read exactly as written. These fonts draw "-" edge to edge,
// so "--models" looked like "–models": keep a small gap between consecutive hyphens, and keep each
// ASCII token whole on one line when it fits. Must match g2/technical_text.py.
const HYPHEN_GAP_EM = 0.12;
const TECH_RUN = /`[^`\n]{1,40}`|[!-~]{2,40}/g;
const hyphenPieces = (text: string) => {
  const pieces: string[] = [];
  let start = 0;
  for (let i = 0; i < text.length-1; i++) if (text[i] === '-' && text[i+1] === '-') { pieces.push(text.slice(start, i+1)); start = i+1; }
  return [...pieces, text.slice(start)];
};
const Tech = ({text}: {text: string}) => {
  const parts: React.ReactNode[] = [], pattern = new RegExp(TECH_RUN.source, 'g');
  let last = 0, match: RegExpExecArray | null;
  while ((match = pattern.exec(text))) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    const pieces = hyphenPieces(match[0]);
    parts.push(/^[A-Za-z0-9]+$/.test(match[0]) ? match[0] : <span key={match.index} style={{display:'inline-block', maxWidth:'100%'}}>{pieces.map((piece, i) => i < pieces.length-1 ? <span key={i} style={{marginRight:`${HYPHEN_GAP_EM}em`}}>{piece}</span> : piece)}</span>);
    last = match.index + match[0].length;
  }
  parts.push(text.slice(last));
  return <>{parts}</>;
};
const svgTech = (text: string, size: number) => hyphenPieces(text).map((piece, i) => <tspan key={i} dx={i ? size*HYPHEN_GAP_EM : undefined}>{piece}</tspan>);
const Text = ({children, x, y, size = 34, color = colors.text, anchor = 'middle', weight = 500}: any) => <text x={x} y={y} textAnchor={anchor} dominantBaseline="middle" fill={color} fontFamily={font} fontSize={size} fontWeight={weight}>{typeof children === 'string' ? svgTech(children, size) : children}</text>;
const Panel = ({x, y, w, h, fill = colors.panel, stroke = colors.border, r = 24, opacity = 1}: any) => <rect x={x} y={y} width={w} height={h} rx={r} fill={fill} stroke={stroke} strokeWidth="2" opacity={opacity}/>;

function ProcessDiagram({scene, frame}: {scene: Scene; frame: number}) {
  const {layout, nodes, edges, title, takeaway} = scene.props;
  const beats = activeBeats(scene, frame);
  const highlights = beats.filter((b) => b.action === 'highlight');
  const focusFrame = highlights[highlights.length-1]?.frame;
  const focused = new Set(highlights.filter((b) => b.frame === focusFrame).map((b) => b.target_id));
  const camera = cameraAt(scene, frame, flowRects(scene.props), {x:0,y:0,width:1560,height:500});
  if (camera.shot.kind === 'focus') camera.shot.targets.forEach(target => focused.add(target));
  const revealed = (target: string) => beats.find((b) => b.action === 'reveal' && b.target_id === target);
  const alpha = (target: string) => {
    const beat = revealed(target);
    return beat ? interpolate(frame-beat.frame, [0, 10], [.18, 1], {extrapolateLeft:'clamp', extrapolateRight:'clamp'}) : .18;
  };
  const complete = nodes.every((n: any) => revealed(`node:${n.id}`)) && edges.every((e: any) => revealed(`edge:${e.id}`));
  const edgeMap = Object.fromEntries(edges.map((e: any) => [e.id, e]));
  const blockedTargets = new Set(edges.filter((e: any) => e.relation === 'blocked' && focused.has(`edge:${e.id}`)).map((e: any) => e.to));
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
      <defs><clipPath id={`${scene.id}_camera_clip`}><rect width={1560} height={500}/></clipPath></defs>
      <g clipPath={`url(#${scene.id}_camera_clip)`}><g transform={camera.transform}>
      {layout.edges.map((route: any) => {
        const edge=edgeMap[route.id], target=`edge:${route.id}`;
        const active=focused.has(target), blocked=edge.relation === 'blocked', color=blocked ? colors.red : active ? colors.cyan : colors.muted;
        const point=positionAlong(route.points, ((frame-(focusFrame || 0))%55)/55);
        return <g key={route.id} opacity={alpha(target)}>
          <polyline points={route.points.map((p: any) => `${p.x},${p.y}`).join(' ')} fill="none" stroke={color} strokeWidth={active ? 5 : 3} strokeLinejoin="round" strokeDasharray={blocked ? '14 9' : undefined} markerEnd={blocked ? undefined : `url(#${markerId})`}/>
          {active && !blocked && <circle cx={point.x} cy={point.y} r={8} fill="#FFFFFF"/>}
          {blocked && <g transform={`translate(${route.points[route.points.length-1].x} ${route.points[route.points.length-1].y})`} stroke={colors.red} strokeWidth={4}><line x1={-8} y1={-8} x2={8} y2={8}/><line x1={-8} y1={8} x2={8} y2={-8}/></g>}
          {edge.label && <><rect x={route.label_x-(edge.label.length*layout.edge_font_size+18)/2} y={route.label_y-layout.edge_font_size/2-4} width={edge.label.length*layout.edge_font_size+18} height={layout.edge_font_size+8} rx={6} fill={colors.bg}/><Text x={route.label_x} y={route.label_y} size={layout.edge_font_size} color={color}>{edge.label}</Text></>}
        </g>;
      })}
      {layout.nodes.map((node: any) => {
        const target=`node:${node.id}`, active=focused.has(target), blocked=blockedTargets.has(node.id), icon=visualFor(scene,target);
        const glyphSize=54*layout.scale;
        return <g key={node.id} opacity={alpha(target)}>
          <Panel x={node.x} y={node.y} w={node.width} h={node.height} fill={blocked ? '#372331' : active ? '#183C50' : colors.panel} stroke={blocked ? colors.red : active ? colors.cyan : colors.border} r={18}/>
          {active && <rect x={node.x+8} y={node.y+8} width={node.width-16} height={node.height-16} rx={12} fill="none" stroke={blocked ? colors.red : colors.cyan} opacity={.12+.09*Math.sin(frame/7)}/>}
          {icon && <SvgVisualObject scene={scene} target={target} frame={frame} x={node.x+(node.width-glyphSize)/2} y={node.y+22*layout.scale} size={glyphSize} color={colors.cyan}/>}
          {node.label_lines.map((line: string, i: number) => <Text key={i} x={node.x+node.width/2} y={icon ? node.y+(112+i*46)*layout.scale : node.y+node.height/2+(i-(node.label_lines.length-1)/2)*layout.font_size*1.28} size={layout.font_size} weight={active ? 650 : 500}>{line}</Text>)}
        </g>;
      })}
      </g></g>
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

function LegacyVideo({ir, sceneBackdrops = {}, badge}: {ir: Timeline; sceneBackdrops?: Record<string, string>; badge?: Badge}) {
  const frame = useCurrentFrame();
  const scene = ir.scenes.find((candidate) => frame >= candidate.start_frame && frame < candidate.end_frame) ?? ir.scenes[ir.scenes.length-1];
  const chapters = ir.content_structure?.chapters || [];
  const chapterIndex = chapters.findIndex((c) => c.narration_segment_ids.includes(scene.narration_segment_ids[0]));
  const backdropPath = sceneBackdrops[scene.id];
  const safeBackdrop = typeof backdropPath === 'string' && /^assets\/scene-backgrounds\/scene-\d{2}\.png$/.test(backdropPath)
    ? staticFile(backdropPath) : null;
  const sceneProgress = (frame - scene.start_frame) / Math.max(1, scene.end_frame - scene.start_frame - 1);
  const zoom = interpolate(sceneProgress, [0, 1], [1.035, 1.095], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const panX = interpolate(sceneProgress, [0, 1], [10, -10], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const caption = (ir.caption_cues ?? ir.narration.segments).find((s) => {
    const start = Math.floor((s.start_ms*ir.video.fps+500)/1000), end = Math.floor((s.end_ms*ir.video.fps+500)/1000);
    return frame >= start && frame < end;
  });
  return <AbsoluteFill style={{backgroundColor: colors.bg, color: colors.text, fontFamily: font}}>
    {safeBackdrop && <Img src={safeBackdrop} style={{position: 'absolute', zIndex: 0, width: '100%', height: '100%', objectFit: 'cover', opacity: 0.58, filter: 'blur(18px)', transformOrigin: 'center', transform: `translateX(${panX}px) scale(${zoom * 1.08})`}}/>}
    {safeBackdrop && <AbsoluteFill style={{zIndex: 1, background: 'linear-gradient(90deg, rgba(5,10,19,.50), rgba(5,10,19,.36) 50%, rgba(5,10,19,.50)), linear-gradient(0deg, rgba(5,10,19,.72), rgba(5,10,19,.22) 48%, rgba(5,10,19,.46)'}}/>}
    <AbsoluteFill style={{zIndex: 2}}>{badge ? <CornerBadge badge={badge} dark/> : <div style={{position: 'absolute', left: 80, top: 54, color: colors.cyan, fontSize: 24, letterSpacing: 2}}>AUTOVIDEO　/　G1 · Video IR 0.2</div>}<div style={{position: 'absolute', right: 82, top: 56, color: colors.muted, fontSize: 22}}>知识动画　·　真实中文旁白</div><SceneVisual scene={scene} frame={frame}/>{chapterIndex >= 0 && <div style={{position: 'absolute', left: 180, top: 124, color: colors.muted, fontSize: 26}}>第 {chapterIndex+1} / {chapters.length} 章 · <Tech text={chapters[chapterIndex].title}/></div>}</AbsoluteFill>
    <AbsoluteFill style={{zIndex: 3, justifyContent: 'flex-end', alignItems: 'center', paddingBottom: 92, pointerEvents: 'none'}}>{caption && <div style={{minWidth: 320, maxWidth: 1680, minHeight: 72, padding: '13px 28px', borderRadius: 18, background: 'rgba(5,10,19,.88)', border: `1px solid ${colors.border}`, color: colors.text, textAlign: 'center', fontSize: caption.text.length > 32 ? 30 : 38, lineHeight: 1.4, overflowWrap: 'anywhere', boxSizing: 'border-box'}}><Tech text={caption.text}/></div>}</AbsoluteFill>
    <Audio src={staticFile(ir.audio_public_file)} volume={Math.pow(10, 0/20)}/>
  </AbsoluteFill>;
}

// A separate presentation for the same validated, registered content components.
const paper = {bg:'#F7F8F5', ink:'#202D28', muted:'#69756E', green:'#17784B', line:'#E1E8E1'};
const defaultPaper = paper;
const ease = (value: number) => 1-Math.pow(1-Math.max(0, Math.min(1, value)), 3);
function InformationVideo({ir, badge, sceneOverride, frameOverride, stageOnly=false, hideStage=false}:
  {ir: Timeline; badge?: Badge; sceneOverride?:Scene; frameOverride?:number; stageOnly?:boolean; hideStage?:boolean}) {
  const contentStyle = React.useContext(ContentStyleContext);
  const brandHeader = React.useContext(BrandHeaderContext);
  const paper = contentStyle ? {bg:contentStyle.background, ink:contentStyle.text, muted:contentStyle.muted,
    green:contentStyle.accent, line:contentStyle.line} : defaultPaper;
  const currentFrame = useCurrentFrame(), frame=frameOverride??currentFrame;
  const sceneIndex = sceneOverride?ir.scenes.indexOf(sceneOverride):Math.max(0, ir.scenes.findIndex((s) => frame >= s.start_frame && frame < s.end_frame));
  const scene = sceneOverride??ir.scenes[sceneIndex], p = scene.props;
  // Short videos (presentation.density 'large') scale every text size by 1.3; g2/information_motion.py::scaled() mirrors this for text fit.
  const large = ir.presentation?.density === 'large';
  const S = (size: number) => large ? Math.round(size * 1.3) : size;
  const caption = (ir.caption_cues ?? ir.narration.segments).find((s) => frame >= Math.floor((s.start_ms*30+500)/1000) && frame < Math.floor((s.end_ms*30+500)/1000));
  const chapters = ir.content_structure?.chapters || [];
  const chapterIndex = chapters.findIndex((c) => c.narration_segment_ids.includes(scene.narration_segment_ids[0]));
  const reveals = scene.beats.filter((b) => b.action === 'reveal');
  const shown = reveals.filter((b) => b.frame <= frame);
  const latest = shown[shown.length-1]?.target_id;
  const motion = (target: string): React.CSSProperties => {
    const beat = reveals.find((b) => b.target_id === target);
    const t = beat ? ease((frame-beat.frame)/revealFrames(scene)) : 0;
    return {opacity:t, transform:`translateY(${(1-t)*22}px)`, willChange:'transform, opacity'};
  };
  // 'pretty' keeps a line from ending on a single orphaned character ("美" / "分"). 'balance' was tried for headings and
  // split phrases worse ("账" / "从 10 美元…"), because Chinese text has no word gaps to balance on.
  const textStyle = {overflowWrap:'anywhere', whiteSpace:'normal', wordBreak:'normal', textWrap:'pretty'} as React.CSSProperties;
  const panelStyle: React.CSSProperties = {background:contentStyle?.surface??'#FFFFFF', border:`1px solid ${paper.line}`, borderRadius:22, boxShadow:'0 12px 32px rgba(26,50,35,.045)', boxSizing:'border-box'};
  const chartLike = scene.component === 'comparison' || scene.component === 'bar_chart';
  const heading = chartLike ? scene.purpose : p.title;
  const materialScene = ['object_showcase','material_collage'].includes(scene.component);
  const previous=scene.previous_scene;
  const recapSources=(p.story?.recap??[]).map((id:string)=>ir.scenes.find(s=>s.id===id)).filter(Boolean) as Scene[];
  const sameHeading=!!previous?.props.scene_flow && (chartLike?previous.purpose:previous.props.title)===heading;
  const headingMotion=carriesPicture(scene)||sameHeading?{}:scene.component==='comparison' && p.visuals?.length
    ? {opacity:ease((frame-scene.start_frame)/16)} : motion(scene.component === 'comparison' ? 'side:left' : scene.component === 'bar_chart' ? 'bar:0' : scene.component === 'flow_diagram'||materialScene ? 'scene' : 'title');
  return <AbsoluteFill style={{background:stageOnly?'transparent':paper.bg, color:paper.ink, fontFamily:font, backgroundImage:stageOnly||contentStyle?undefined:'radial-gradient(#DDE5DB .8px, transparent .8px)', backgroundSize:'26px 26px'}}>
    {!stageOnly&&<>{brandHeader ? <div data-brand-header="content" style={{position:'absolute',left:brandHeader.left,top:brandHeader.top,width:1920-brandHeader.left-brandHeader.right-brandHeader.width-brandHeader.gap,height:brandHeader.height,overflow:'hidden'}}>
      <div style={{position:'absolute',top:10,left:0,right:0,height:34,color:paper.muted,fontSize:24,lineHeight:'34px',overflow:'hidden',whiteSpace:'nowrap',textOverflow:'ellipsis'}}>{scene.props.story?.entities?.length ? <StoryObjects scene={scene} inline/> : badge?.text||ir.video.title}</div>
      <div style={{position:'absolute',top:46,left:0,right:0,fontSize:18,lineHeight:'24px',color:paper.muted,whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis'}}>{chapterIndex >= 0 && chapters.length > 1 ? `${String(chapterIndex+1).padStart(2,'0')} / ${String(chapters.length).padStart(2,'0')} · ${chapters[chapterIndex].title}` : `${String(sceneIndex+1).padStart(2,'0')} / ${String(ir.scenes.length).padStart(2,'0')}`}</div>
    </div> : <><CornerBadge badge={badge}/><StoryObjects scene={scene}/><div style={{position:'absolute', right:140, top:72, color:paper.muted, fontSize:22}}>{chapterIndex >= 0 && chapters.length > 1 ? <>{`${String(chapterIndex+1).padStart(2,'0')} / ${String(chapters.length).padStart(2,'0')}　`}<Tech text={chapters[chapterIndex].title}/></> : `${String(sceneIndex+1).padStart(2,'0')} / ${String(ir.scenes.length).padStart(2,'0')}`}</div></>}
    {scene.component!=='title_card'&&<div style={{position:'absolute',left:140,top:162,width:1640,fontSize:materialScene&&heading.length>40?40:large ? (heading.length > 22 ? S(46) : S(55)) : heading.length > 24 ? 54 : 64,fontWeight:700,lineHeight:1.3,...textStyle,...headingMotion}}><Tech text={heading}/></div>}</>}
    <div style={{position:'absolute',inset:0,display:hideStage?'none':undefined}}>
    {recapSources.length ? <div data-story-recap={scene.id} style={{position:'absolute',left:140,top:300,width:1640,height:560,display:'flex',justifyContent:'center',gap:40,alignItems:'center'}}>
      {recapSources.map((source,i)=>{
        const width=recapSources.length===2?800:1120,scale=(width-48)/1640;
        const at=reveals.find(b=>b.target_id===`line:${i}`)?.frame??scene.start_frame+18+i*24;
        const alpha=ease((frame-at)/revealFrames(scene));
        const identities=source.props.story?.entities.slice(0,3)??[],color=identities[0]?.color??paper.green;
        const takeaway=source.props.takeaway||source.props.title||source.purpose;
        return <div key={source.id} style={{width,flex:'none',padding:22,...panelStyle,borderTop:`4px solid ${color}`,opacity:alpha,transform:`translateY(${(1-alpha)*16}px)`}}>
          <div style={{position:'relative',width:width-48,height:620*scale,overflow:'hidden',borderRadius:12}}>
            <div style={{position:'absolute',left:-140*scale,top:-270*scale,width:1920,height:1080,transform:`scale(${scale})`,transformOrigin:'0 0'}}>
              <InformationVideo ir={ir} sceneOverride={source} frameOverride={source.end_frame-1} stageOnly/>
            </div>
          </div>
          <div style={{fontSize:takeaway.length>45?26:30,fontWeight:650,color,lineHeight:1.4,marginTop:18,...textStyle}}><Tech text={takeaway}/></div>
          {!!identities.length&&<div style={{display:'flex',gap:20,fontSize:24,lineHeight:1.4,marginTop:7}}>{identities.map((entity:any)=><span key={entity.id} style={{color:entity.color}}>{entity.name}</span>)}</div>}
        </div>;
      })}
    </div> : scene.component === 'title_card' ? <div style={{position:'absolute',left:180,top:285,width:1520}}>
      <div style={{...motion('title'), ...textStyle, fontSize:large ? (p.title.length > 20 ? S(66) : S(83)) : p.title.length > 30 ? 76 : 92, lineHeight:1.35,fontWeight:700,letterSpacing:-1,maxWidth:1450}}><Tech text={p.title}/></div>
      <div style={{...motion('title'),width:84,height:5,background:paper.green,marginTop:38,borderRadius:3}}/>
      {p.subtitle && <div style={{...motion('subtitle'),...textStyle,maxWidth:1380,marginTop:40,fontSize:p.subtitle.length > 65 ? S(36) : S(42),lineHeight:1.65,color:paper.muted}}><Tech text={p.subtitle}/></div>}
    </div> : <>
      {materialScene && <DirectedMaterials scene={scene} frame={frame}/>}
      {scene.component === 'concept_reveal' && <div style={{position:'absolute',left:140,top:p.lines.length === 4 ? 330 : 360,width:1640,display:'grid',gridTemplateColumns:`repeat(${p.lines.length === 4 ? 2 : p.lines.length}, 1fr)`,gap:28}}>
        {p.lines.map((line: string,i: number) => {
          const match = line.match(/^([^：:]{1,16})[：:]\s*([\s\S]+)$/);
          const label = match ? match[1] : line, body = match ? match[2] : '';
          const target=`line:${i}`, active = latest === target, compact = p.lines.length === 4, icon=visualFor(scene,target);
          const labelStyle={...textStyle,fontWeight:650,fontSize:S(compact || label.length > 10 ? 36 : 42),lineHeight:1.4};
          return <div key={i} style={{...panelStyle,...motion(target),height:compact ? 250 : 440,padding:compact ? '25px 34px' : '34px 36px',borderTop:`3px solid ${active ? objectColor(scene,target,paper.green) : paper.line}`}}>
            {icon ? compact ? <div style={{display:'flex',alignItems:'center',gap:18}}><VisualObject scene={scene} target={target} frame={frame} size={52} color={objectColor(scene,target)}/><div style={{...labelStyle,flex:1}}><Tech text={label}/></div></div> : <><div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:22}}><VisualObject scene={scene} target={target} frame={frame} size={64} color={objectColor(scene,target)}/><span style={{fontSize:20,color:paper.muted}}>{String(i+1).padStart(2,'0')}</span></div><div style={labelStyle}><Tech text={label}/></div></> : <><div style={{fontSize:22,color:paper.green,fontWeight:600,letterSpacing:2,marginBottom:compact ? 10 : 29}}>{String(i+1).padStart(2,'0')}</div><div style={labelStyle}><Tech text={label}/></div></>}
            {body && <div style={{...textStyle,color:paper.muted,fontSize:S(compact ? 28 : p.lines.length === 3 ? 30 : 34),lineHeight:compact ? 1.5 : 1.6,marginTop:compact ? 10 : 22}}><Tech text={body}/></div>}
          </div>;
        })}
      </div>}
      {scene.component === 'comparison' && <div style={{position:'absolute',left:140,top:330,width:1640,display:'grid',gridTemplateColumns:'1fr 1fr',gap:44}}>
        {(['left','right'] as const).map((side) => {
          const data=p[side], total=data.items.join('').length, bodySize=S(total > 130 ? 30 : 34),color=objectColor(scene,`side:${side}`,side==='right'?paper.green:paper.muted);
          return <div key={side} style={{...panelStyle,...motion(`side:${side}`),height:500,padding:'34px 40px',background:p.story?`${color}08`:side === 'right' ? '#EDF6EF' : '#FFFFFF',borderTop:`3px solid ${p.story?color:side === 'right' ? paper.green : paper.line}`}}>
            <div style={{display:'flex',alignItems:'center',gap:20,marginBottom:30}}><VisualObject scene={scene} target={`side:${side}`} frame={frame} size={64} color={p.story?color:paper.green}/><div style={{...textStyle,flex:1,minWidth:0,color,fontSize:S(data.label.length > 20 ? 34 : 42),lineHeight:1.4,fontWeight:650}}><Tech text={data.label}/></div></div>
            <div style={{display:'flex',flexDirection:'column',gap:20}}>{data.items.map((item: string,i: number) => <div key={i} style={{display:'flex',gap:15,fontSize:bodySize,lineHeight:1.5}}><span style={{color:paper.green}}>·</span><span style={textStyle}><Tech text={item}/></span></div>)}</div>
          </div>;
        })}
      </div>}
      {scene.component === 'flow_diagram' && <ObjectFlow scene={scene} frame={frame}/>}
      {scene.component === 'scatter_chart' && <ScatterChart scene={scene} frame={frame}/>}
      {scene.component === 'data_chart' && <DataChart scene={scene} frame={frame}/>}
      {scene.component === 'big_number' && <div style={{position:'absolute',left:140,top:330,width:1640,height:500,...panelStyle,padding:'0 72px',borderTop:`3px solid ${paper.green}`,display:'flex',flexDirection:'column',justifyContent:'center'}}>
        <div style={{...motion('line:0'),...textStyle,fontSize:p.value.length <= 6 ? 220 : p.value.length <= 9 ? 180 : 140,fontWeight:750,lineHeight:1.1,letterSpacing:-2,color:paper.green}}><Tech text={p.value}/></div>
        <div style={{...motion('line:1'),...textStyle,fontSize:S(46),fontWeight:650,lineHeight:1.35,marginTop:24}}><Tech text={p.label}/></div>
        {p.note && <div style={{...motion('line:2'),...textStyle,fontSize:S(32),lineHeight:1.5,color:paper.muted,marginTop:20}}><Tech text={p.note}/></div>}
      </div>}
      {scene.component === 'price_change' && (() => {
        // Compiler and planner use the same measured geometry. It is derived
        // from the unchanged strings rather than requested from the model.
        const rows = p.rows, layout = scene.price_layout;
        const size = layout?.number_size ?? (rows.length === 1 ? 180 : 128), side = layout?.side_size ?? Math.round(size*.42), badge = layout?.badge_size ?? S(52);
        const Value = ({text, color, opacity = 1}: {text: string; color: string; opacity?: number}) => {
          const m = text.match(/^(\D*?)(\d[\d,]*(?:\.\d+)?)(.*)$/), parts = m ? [m[1], m[2], m[3]] : ['', text, ''];
          return <span style={{color, opacity, whiteSpace:'nowrap', fontWeight:750, letterSpacing:-2, lineHeight:1.1}}><span style={{fontSize:side}}>{parts[0]}</span><span style={{fontSize:size}}>{parts[1]}</span><span style={{fontSize:side,fontWeight:650}}>{parts[2]}</span></span>;
        };
        return <div style={{position:'absolute',left:140,top:330,width:1640,height:500,...panelStyle,padding:'0 56px',borderTop:`3px solid ${paper.green}`,display:'flex',flexDirection:'column',justifyContent:'center',boxSizing:'border-box'}}>
          {p.unit && <div style={{...motion('title'),...textStyle,fontSize:layout?.unit_size ?? S(36),lineHeight:1.4,color:paper.muted,marginBottom:layout?.unit_gap ?? 20}}><Tech text={p.unit}/></div>}
          <div style={{display:'flex',flexDirection:'column',gap:layout?.row_gap ?? 34}}>
            {rows.map((row: any, i: number) => {
              const rowTarget = `line:${p.staged ? i*3 : i}`;
              const beat = reveals.find((b) => b.target_id === rowTarget);
              const oldOpacity = beat ? ease((frame-beat.frame)/14) : 0;
              const later = (delay: number) => {
                const target = p.staged ? reveals.find(b => b.target_id === `line:${i*3+(delay===8?1:2)}`) : beat;
                return target ? ease((frame-target.frame-(p.staged?0:delay))/14) : 0;
              };
              const label = <div style={{...textStyle,width:layout?.stacked_label?undefined:layout?.label_width ?? 400,flex:layout?.stacked_label?1:'none',minWidth:0,fontSize:layout?.label_size ?? S(46),fontWeight:650,lineHeight:1.35}}><Tech text={row.label}/></div>;
              const delta = row.change && <div style={{flex:'none',padding:'6px 26px',borderRadius:18,background:row.direction === 'down' ? '#E3F3E9' : '#FBE8E4',color:row.direction === 'down' ? paper.green : '#B3402E',fontSize:badge,fontWeight:750,lineHeight:1.3,opacity:later(20),whiteSpace:'nowrap'}}>{row.direction === 'down' ? '−' : '+'}{row.change}</div>;
              return <div key={i} data-price-layout={layout?.stacked_label?'stacked':'row'} style={{...motion('title'),display:'flex',flexDirection:layout?.stacked_label?'column':'row',alignItems:layout?.stacked_label?'stretch':'center',gap:layout?.stacked_label?12:36,lineHeight:1.1}}>
                {layout?.stacked_label?<div style={{display:'flex',alignItems:'center',gap:36}}>{label}{delta}</div>:label}
                <div style={{flex:1,display:'flex',alignItems:'center',minWidth:0,justifyContent:layout?.stacked_label?'center':undefined}}>
                  <Value text={row.before} color={paper.muted} opacity={.18+.82*oldOpacity}/>
                  <svg width={Math.round(size*.62)} height={Math.round(size*.5)} viewBox="0 0 62 50" style={{margin:`0 ${Math.round(size*.18)}px`,flex:'none',opacity:.18+.82*Math.min(oldOpacity,later(8))}}><path d="M2 25 H52 M34 5 L56 25 L34 45" fill="none" stroke={paper.green} strokeWidth="6" strokeLinecap="round" strokeLinejoin="round"/></svg>
                  <Value text={row.after} color={objectColor(scene,rowTarget,paper.green)} opacity={.18+.82*later(8)}/>
                </div>
                {!layout?.stacked_label&&delta}
              </div>;
            })}
          </div>
        </div>;
      })()}
      {scene.component === 'source_card' && <div style={{position:'absolute',left:140,top:330,width:1640,height:500,...panelStyle,padding:'0 56px',borderTop:`3px solid ${paper.green}`,display:'flex',flexDirection:'column',justifyContent:'center',gap:26,boxSizing:'border-box'}}>
        {p.sources.map((source: any, i: number) => <div key={i} style={motion(`line:${i}`)}>
          <div style={{display:'flex',justifyContent:'space-between',alignItems:'baseline',gap:40}}>
            <div style={{...textStyle,fontSize:S(34),fontWeight:650,lineHeight:1.3}}><Tech text={source.title}/></div>
            <div style={{flex:'none',fontSize:S(30),color:paper.muted,whiteSpace:'nowrap'}}>{source.date}</div>
          </div>
          <div style={{fontSize:S(28),lineHeight:1.5,color:paper.green}}><Tech text={source.domain}/></div>
        </div>)}
      </div>}
      {scene.component === 'bar_chart' && (() => {
        const top = Math.max(...p.values, 0) || 1, count = p.categories.length;
        const shownValue = (value: number) => `${value.toLocaleString('en-US', {maximumFractionDigits: 3})} ${p.unit}`;
        return <div style={{position:'absolute',left:140,top:330,width:1640,height:500,...panelStyle,padding:'0 56px',borderTop:`3px solid ${paper.green}`,display:'flex',flexDirection:'column',justifyContent:'center',gap:count > 4 ? 22 : 34}}>
          {p.categories.map((category: string, i: number) => {
            const beat = reveals.find((b) => b.target_id === `bar:${i}`);
            const t = beat ? ease((frame-beat.frame)/20) : 0;
            return <div key={i} style={{display:'flex',alignItems:'center',gap:32,opacity:Math.min(1, t*2.2)}}>
              <div style={{...textStyle,width:large ? 440 : 360,flex:'none',fontSize:S(count > 4 ? 30 : 34),fontWeight:600,lineHeight:1.3}}><Tech text={category}/></div>
              <div style={{flex:1,display:'flex',alignItems:'center',gap:20,minWidth:0}}>
                <div style={{height:count > 4 ? 44 : 58,borderRadius:10,background:objectColor(scene,`bar:${i}`,paper.green),width:`${Math.max(1.2, p.values[i]/top*72*t)}%`,flex:'none'}}/>
                <div style={{...textStyle,fontSize:S(count > 4 ? 32 : 38),fontWeight:650,whiteSpace:'nowrap',color:paper.ink}}><Tech text={shownValue(p.values[i])}/></div>
              </div>
            </div>;
          })}
        </div>;
      })()}
    </>}
    </div>
    {!stageOnly&&<><div style={{position:'absolute',bottom:60,left:120,right:120,display:'flex',justifyContent:'center',alignItems:'flex-end',minHeight:80}}>{caption && <div style={{maxWidth:1560,padding:'14px 28px',boxSizing:'border-box',background:'#29392F',color:'#FFFFFF',borderRadius:14,fontSize:large ? (caption.text.length > 32 ? 36 : 44) : caption.text.length > 32 ? 30 : 38,lineHeight:1.45,textAlign:'center',...textStyle}}><Tech text={caption.text}/></div>}</div>
    <div style={{position:'absolute',left:0,bottom:0,height:4,width:`${100*(frame+1)/ir.total_frames}%`,background:paper.green}}/>
    <Audio src={staticFile(ir.audio_public_file)}/></>}
  </AbsoluteFill>;
}

function ExplanationVideo({ir, badge}: {ir: Timeline; badge?: Badge}) {
  const frame = useCurrentFrame();
  const scenes=React.useMemo(()=>linkScenes(ir.scenes),[ir.scenes]);
  const linked={...ir,scenes};
  const scene = scenes.find(s => frame >= s.start_frame && frame < s.end_frame) ?? scenes[scenes.length-1];
  if (['object_showcase', 'material_collage'].includes(scene.component) && !scene.props.shot_plan) {
    const preset = scene.component === 'material_collage' ? 'dynamic_collage' : 'object_showcase';
    return <CreativeVideo ir={{...ir,presentation:{preset}}} badge={badge} showCaptions/>;
  }
  const transition=transitionAt(scene,frame),previous=scene.previous_scene;
  if(!transition.active||!previous)return <InformationVideo ir={linked} badge={badge}/>;
  // Only the picture area dissolves. There is one uninterrupted audio track,
  // caption layer and header; scene timing and chapter boundaries stay intact.
  return <AbsoluteFill>
    <InformationVideo ir={linked} badge={badge} hideStage/>
    <AbsoluteFill style={{clipPath:'inset(280px 80px 150px 80px)'}}>
      <AbsoluteFill style={{opacity:1-transition.progress}}><InformationVideo ir={linked} sceneOverride={previous} frameOverride={previous.end_frame-1} stageOnly/></AbsoluteFill>
      <AbsoluteFill style={{opacity:transition.progress}}><InformationVideo ir={linked} sceneOverride={scene} frameOverride={frame} stageOnly/></AbsoluteFill>
    </AbsoluteFill>
  </AbsoluteFill>;
}

// Hybrid clips contain only the picture. FFmpeg adds one continuous voice and
// caption track after joining them with the still cards.
type FastCardBackgrounds = Record<string,{path:string;still:boolean}>;
function FastCardsVideo({ir, backgrounds}: {ir: Timeline; backgrounds: FastCardBackgrounds}) {
  const frame=useCurrentFrame();
  const scenes=React.useMemo(()=>linkScenes(ir.scenes),[ir.scenes]);
  const linked={...ir,scenes};
  const scene=scenes.find(s=>frame>=s.start_frame&&frame<s.end_frame)??scenes[scenes.length-1];
  const transition=transitionAt(scene,frame),previous=scene.previous_scene;
  const picture=(current:Scene,at:number)=><AbsoluteFill>
    <Img src={staticFile(backgrounds[current.id].path)} style={{width:1920,height:1080}}/>
    {!backgrounds[current.id].still&&<div style={{position:'absolute',left:140,top:275,width:1640,height:531,overflow:'hidden'}}>
      <div style={{position:'absolute',left:13,top:-227,width:1920,height:1080,transform:'scale(.84)',transformOrigin:'0 0'}}>
        <InformationVideo ir={linked} sceneOverride={current} frameOverride={at} stageOnly/>
      </div>
    </div>}
  </AbsoluteFill>;
  return <AbsoluteFill>{picture(scene,frame)}
    {transition.active&&previous&&<AbsoluteFill style={{opacity:1-transition.progress}}>{picture(previous,previous.end_frame-1)}</AbsoluteFill>}
  </AbsoluteFill>;
}

type VideoProps = {ir: Timeline; sceneBackdrops?: Record<string, string>; badge?: Badge; fastCardBackgrounds?:FastCardBackgrounds; contentStyle?:ContentStyle;brandHeader?:BrandHeader};
function Video(props: VideoProps) {
  return <ContentStyleContext.Provider value={props.contentStyle}><BrandHeaderContext.Provider value={props.brandHeader}><VideoContent {...props}/></BrandHeaderContext.Provider></ContentStyleContext.Provider>;
}

function VideoContent(props: VideoProps) {
  if(props.fastCardBackgrounds)return <FastCardsVideo ir={props.ir} backgrounds={props.fastCardBackgrounds}/>;
  if (props.ir.presentation?.preset === 'explanation') return <ExplanationVideo {...props}/>;
  if (['information_cards', 'kinetic_typography', 'image_slideshow', 'chat_dialogue', 'object_showcase', 'dynamic_collage'].includes(props.ir.presentation?.preset || '')) return <CreativeVideo ir={props.ir} badge={props.badge}/>;
  return props.ir.presentation?.preset === 'information_motion' ? <InformationVideo ir={props.ir} badge={props.badge}/> : <LegacyVideo {...props}/>;
}

const defaults: Timeline = {schema_version:'0.2', video:{title:'G1',width:1920,height:1080,fps:30,duration_ms:1000}, narration:{audio_asset_id:'audio',segments:[]}, scenes:[], total_frames:30, audio_public_file:'assets/communications_noise.wav'};

export const Root: React.FC = () => <Composition id="G1CommunicationNoise" component={Video} defaultProps={{ir: defaults, sceneBackdrops: {}}} width={1920} height={1080} fps={30} durationInFrames={30} calculateMetadata={({props}) => ({durationInFrames: (props as any).ir.total_frames, fps:30, width:1920, height:1080})}/>;

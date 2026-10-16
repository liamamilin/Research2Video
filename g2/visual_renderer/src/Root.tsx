import React from 'react';
import {AbsoluteFill, Audio, Composition, interpolate, staticFile, useCurrentFrame} from 'remotion';

type Obj = {id: string; kind: string; x: number; y: number; width?: number; height?: number;
  color?: string; opacity?: number; rotation?: number; parent_id?: string; surface?: string;
  label?: string; label_position?: string; text?: string; motion?: any};
type Scene = {id: string; title: string; takeaway: string; objects: Obj[]; start_frame: number; end_frame: number};
type Plan = {scenes: Scene[]; segments: any[]; total_frames: number; audio_public_file: string};
type State = {x: number; y: number; angle: number; scale: number; opacity: number};

const W = 1560, H = 580;
const palette: Record<string, string> = {blue: '#548DF4', cyan: '#64E4D5', gold: '#FFC76D',
  green: '#75C999', purple: '#BBA5FF', red: '#FF877E', white: '#F2F5FF', slate: '#8397BA'};
const font = '"PingFang SC", "Hiragino Sans GB", sans-serif';
const rotate = (x: number, y: number, angle: number) => {
  const a = angle * Math.PI / 180;
  return {x: x * Math.cos(a) - y * Math.sin(a), y: x * Math.sin(a) + y * Math.cos(a)};
};

function states(objects: Obj[], progress: number): Record<string, State> {
  const result: Record<string, State> = {}, byId = Object.fromEntries(objects.map((o) => [o.id, o]));
  const visit = (o: Obj): State => {
    if (result[o.id]) return result[o.id];
    let x = o.x * W / 100, y = o.y * H / 100, angle = o.rotation || 0, scale = 1, opacity = o.opacity ?? 1;
    const m = o.motion;
    if (m) {
      const t = Math.max(0, Math.min(1, (progress - (m.start ?? 0)) / ((m.end ?? 1) - (m.start ?? 0))));
      if (m.type === 'rotate') angle += (m.degrees ?? 360) * t;
      if (m.type === 'translate') {x += (m.dx ?? 0) * W / 100 * t; y += (m.dy ?? 0) * H / 100 * t;}
      if (m.type === 'orbit') {
        const cx = m.center_x * W / 100, cy = m.center_y * H / 100;
        const p = rotate(x - cx, y - cy, (m.degrees ?? 360) * t);
        x = cx + p.x; y = cy + p.y;
      }
      if (m.type === 'oscillate') {x += (m.dx ?? 0) * W / 100 * Math.sin(t * Math.PI * 2 * (m.cycles ?? 1)); y += (m.dy ?? 0) * H / 100 * Math.sin(t * Math.PI * 2 * (m.cycles ?? 1));}
      if (m.type === 'pulse') scale = 1 + (m.amplitude ?? 0.08) * Math.sin(t * Math.PI * 2 * (m.cycles ?? 2));
      if (m.type === 'fade_in') opacity *= t;
    }
    if (o.parent_id) {
      const parent = visit(byId[o.parent_id]);
      const point = rotate(x * parent.scale, y * parent.scale, parent.angle);
      x = parent.x + point.x; y = parent.y + point.y;
      angle += parent.angle; scale *= parent.scale; opacity *= parent.opacity;
    }
    return result[o.id] = {x, y, angle, scale, opacity};
  };
  objects.forEach(visit);
  return result;
}

function wrap(text: string, count: number) {
  const chars = [...text], rows = [];
  for (let i = 0; i < chars.length; i += count) rows.push(chars.slice(i, i + count).join(''));
  return rows;
}

function Shape({o, progress, prefix}: {o: Obj; progress: number; prefix: string}) {
  const c = palette[o.color || ({earth:'blue',moon:'slate',sun:'gold'} as Record<string,string>)[o.surface || ''] || 'cyan'], w = (o.width ?? 12) * W / 100, h = (o.height ?? 16) * H / 100;
  if (o.kind === 'group') return null;
  if (o.kind === 'sphere') {
    const r = w / 2, id = `${prefix}_${o.id}`, surface = o.surface || 'plain';
    return <g>
      <defs><radialGradient id={`${id}_shade`} cx="32%" cy="25%"><stop offset="0" stopColor={surface === 'sun' ? '#FFEAC0' : '#B9D9FF'}/><stop offset=".45" stopColor={surface === 'sun' ? '#FFBA55' : c}/><stop offset="1" stopColor={surface === 'sun' ? '#CD792C' : '#14263D'}/></radialGradient><clipPath id={`${id}_clip`}><circle r={r}/></clipPath></defs>
      {surface === 'sun' && Array.from({length: 16}, (_, i) => <line key={i} x1={r*1.12} y1={0} x2={r*1.3} y2={0} stroke={c} strokeWidth={4} transform={`rotate(${i*22.5})`} opacity={.5}/>)}
      <circle r={r} fill={`url(#${id}_shade)`} stroke={surface === 'sun' ? '#FFD391' : '#ADCEFB'} strokeWidth={2}/>
      <g clipPath={`url(#${id}_clip)`}>
        {surface === 'earth' && <><path d={`M${-r*.83},${-r*.48} Q${-r*.35},${-r*.86} ${-r*.05},${-r*.45} L${-r*.22},${-r*.03} ${-r*.48},${r*.23} ${-r*.67},${r*.02} Z`} fill="#6DD1A2" opacity=".9"/><path d={`M${r*.28},${-r*.2} L${r*.78},${-r*.05} ${r*.65},${r*.52} ${r*.3},${r*.75} ${r*.08},${r*.27} Z`} fill="#74CEA6" opacity=".85"/><ellipse rx={r*.4} ry={r} fill="none" stroke="#D4F3FF" strokeWidth={1.5} opacity=".25"/><line x1={-r} x2={r} stroke="#D4F3FF" opacity=".25"/></>}
        {surface === 'moon' && [[-.35,-.24,.17],[.27,.32,.22],[.35,-.45,.1],[-.25,.5,.09]].map(([x,y,s], i) => <circle key={i} cx={x*r} cy={y*r} r={s*r} fill="#17243B" opacity=".24"/>)}
      </g>
    </g>;
  }
  if (o.kind === 'ellipse') return <ellipse rx={w/2} ry={h/2} fill={c} fillOpacity={.18} stroke={c} strokeWidth={3}/>;
  if (o.kind === 'rect') return <rect x={-w/2} y={-h/2} width={w} height={h} rx={18} fill={c} fillOpacity={.16} stroke={c} strokeWidth={2}/>;
  if (o.kind === 'marker') return <><circle r={w/2+7} fill={c} opacity=".2"/><circle r={w/2} fill={c} stroke="#FAFFFF" strokeWidth={2}/></>;
  if (o.kind === 'arrow') return <><line x1={-w/2} x2={w/2-15} stroke={c} strokeWidth={5} strokeLinecap="round"/><path d={`M${w/2-22},-11 L${w/2},0 L${w/2-22},11`} fill="none" stroke={c} strokeWidth={5} strokeLinejoin="round"/></>;
  if (o.kind === 'wave') {
    const m = o.motion;
    const t = m ? Math.max(0, Math.min(1, (progress - (m.start ?? 0)) / ((m.end ?? 1) - (m.start ?? 0)))) : 0;
    const phase = m?.type === 'wave' ? t * Math.PI * 2 * (m.cycles ?? 2) : 0;
    const points = Array.from({length: 81}, (_, i) => {const x = -w/2+i*w/80; return `${i ? 'L' : 'M'}${x},${Math.sin(i/80*Math.PI*4-phase)*h/2}`;}).join(' ');
    return <><path d={`${points} L${w/2},${h} L${-w/2},${h} Z`} fill={c} opacity=".1"/><path d={points} fill="none" stroke={c} strokeWidth={5} strokeLinecap="round"/></>;
  }
  if (o.kind === 'text') return <text textAnchor="middle" fill={c} fontSize={32} fontWeight={550}>{wrap(o.text || '', Math.max(4, Math.floor(w/32))).map((row, i) => <tspan key={i} x="0" dy={i ? 44 : 0}>{row}</tspan>)}</text>;
  return null;
}

function Illustration({scene, progress}: {scene: Scene; progress: number}) {
  const map = states(scene.objects, progress);
  return <svg width="1560" height="580" style={{overflow: 'visible', fontFamily: font}}>
    {scene.objects.map((o) => {const s=map[o.id]; return <g key={o.id} opacity={s.opacity} transform={`translate(${s.x},${s.y}) rotate(${s.angle}) scale(${s.scale})`}><Shape o={o} progress={progress} prefix={scene.id}/></g>;})}
    {scene.objects.filter((o) => o.label && o.label_position !== 'none').map((o) => {
      const s = map[o.id], w = (o.width ?? 12)*W/100, h = o.kind === 'sphere' || o.kind === 'marker' ? w : (o.height ?? 16)*H/100;
      const pos = o.label_position || 'below';
      const x = s.x + (pos === 'left' ? -w/2-22 : pos === 'right' ? w/2+22 : 0);
      const y = s.y + (pos === 'above' ? -h/2-24 : pos === 'below' ? h/2+38 : 8);
      const rows = wrap(o.label || '', 14);
      return <text key={o.id} x={x} y={y} fill="#F0F6FF" opacity={s.opacity} fontSize={30} textAnchor={pos==='left'?'end':pos==='right'?'start':'middle'} paintOrder="stroke" stroke="#0B1425" strokeWidth={6} strokeLinejoin="round">{rows.map((row,i)=><tspan key={i} x={x} dy={i?38:0}>{row}</tspan>)}</text>;
    })}
  </svg>;
}

function Video({plan}: {plan: Plan}) {
  const frame = useCurrentFrame();
  const scene = plan.scenes.find((s) => frame >= s.start_frame && frame < s.end_frame) || plan.scenes[plan.scenes.length-1];
  const progress = (frame-scene.start_frame)/Math.max(1,scene.end_frame-scene.start_frame-1);
  const opacity = interpolate(frame-scene.start_frame, [0,10], [0,1], {extrapolateLeft:'clamp',extrapolateRight:'clamp'});
  const caption = plan.segments.find((s) => frame >= Math.round(s.start_ms*.03) && frame < Math.round(s.end_ms*.03));
  return <AbsoluteFill style={{background:'radial-gradient(ellipse at 50% 42%, #172941 0%, #0B1425 62%, #080D19 100%)',color:'#F2F5FF',fontFamily:font}}>
    <div style={{position:'absolute',top:38,left:76,fontSize:23,color:'#72D9CE',letterSpacing:3}}>AUTOVIDEO</div>
    <div style={{position:'absolute',top:40,right:76,fontSize:22,color:'#A7B6CD'}}>概念示意 · 非比例绘制</div>
    <AbsoluteFill style={{opacity}}><div style={{position:'absolute',top:92,left:180,width:1560,fontSize:57,fontWeight:650,textAlign:'center'}}>{scene.title}</div>
      <div style={{position:'absolute',left:180,top:205}}><Illustration scene={scene} progress={progress}/></div>
      <div style={{position:'absolute',top:815,left:245,width:1430,fontSize:31,lineHeight:1.45,textAlign:'center',color:'#B7D1E6'}}>{scene.takeaway}</div>
    </AbsoluteFill>
    <AbsoluteFill style={{justifyContent:'flex-end',alignItems:'center',paddingBottom:48,pointerEvents:'none'}}>{caption && <div style={{maxWidth:1680,padding:'13px 28px',borderRadius:16,background:'rgba(4,8,15,.92)',border:'1px solid #26364E',fontSize:caption.text.length>32?30:38,lineHeight:1.4,textAlign:'center'}}>{caption.text}</div>}</AbsoluteFill>
    <Audio src={staticFile(plan.audio_public_file)}/>
  </AbsoluteFill>;
}

const defaults: Plan = {scenes:[],segments:[],total_frames:30,audio_public_file:'assets/communications_noise.wav'};
export const Root = () => <Composition id="VisualNarrative" component={Video} defaultProps={{plan:defaults}} width={1920} height={1080} fps={30} durationInFrames={30} calculateMetadata={({props}) => ({durationInFrames:props.plan.total_frames})}/>;

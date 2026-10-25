import React from 'react';
import {Lottie} from '@remotion/lottie';
import {Sequence} from 'remotion';
import {FileText, Database, Server, Search, UserRound, UsersRound, Clock3, Coins, Cloud, Code2,
  Network, ChartNoAxesCombined, BrainCircuit, Cpu, Shield, Globe2, BookOpen, Leaf, Atom,
  Building2, Settings2, FolderOpen, ClipboardCheck, ArrowUpRight} from 'lucide-react';
import {objectMotion} from './ObjectMotions';

const icons = {file:FileText,database:Database,server:Server,search:Search,user:UserRound,users:UsersRound,
  clock:Clock3,cost:Coins,cloud:Cloud,code:Code2,network:Network,chart:ChartNoAxesCombined,brain:BrainCircuit,
  cpu:Cpu,shield:Shield,globe:Globe2,book:BookOpen,leaf:Leaf,atom:Atom,building:Building2,settings:Settings2,
  folder:FolderOpen,result:ClipboardCheck};
type Visual = {target:string;icon:keyof typeof icons;action?:string;phrase?:string};
type Scene = {id:string;props:{visuals?:Visual[]};beats:{id:string;target_id:string;action:string;frame:number}[]};
const clamp = (n:number) => Math.max(0,Math.min(1,n));
export const visualFor = (scene:Scene,target:string) => scene.props.visuals?.find(v=>v.target===target);
function actionStart(scene:Scene,target:string) {
  const index = scene.props.visuals?.findIndex(v=>v.target===target);
  const beat = scene.beats.find(b=>b.id===`${scene.id}_action_${index}`)
    ?? scene.beats.find(b=>b.target_id===target && b.action==='reveal');
  return beat?.frame;
}
export function VisualObject({scene,target,frame,size=72,color='#17784B'}:
  {scene:Scene;target:string;frame:number;size?:number;color?:string}) {
  const item = visualFor(scene,target);
  if (!item) return null;
  const Glyph = icons[item.icon], start=actionStart(scene,target), elapsed=frame-(start ?? frame);
  const sending = item.action==='send' && elapsed>=0 && elapsed<42;
  const shift = sending ? Math.sin(clamp(elapsed/42)*Math.PI)*9 : 0;
  return <div data-visual-object={item.icon} style={{width:size,height:size,flex:'none',position:'relative',
    display:'flex',alignItems:'center',justifyContent:'center',borderRadius:size*.23,background:`${color}0D`,color}}>
    <Glyph size={size*.51} strokeWidth={1.8} style={{transform:`translate(${shift}px,${-shift}px)`}}/>
    {sending && <ArrowUpRight size={size*.28} strokeWidth={2} style={{position:'absolute',right:0,top:0}}/>}
    {start!==undefined && ['process','complete','disconnect'].includes(item.action || '') &&
      <Sequence from={start} layout="none"><Lottie animationData={objectMotion(item.action!,color)} loop={false}
        style={{position:'absolute',inset:0,width:size,height:size,pointerEvents:'none'}}/></Sequence>}
  </div>;
}

// SVG diagrams share the same object glyphs. Their small path movements use
// the frame directly so a nested SVG needs no HTML or browser animation clock.
export function SvgVisualObject({scene,target,frame,x,y,size=54,color='#17784B'}:
  {scene:Scene;target:string;frame:number;x:number;y:number;size?:number;color?:string}) {
  const item=visualFor(scene,target);
  if (!item) return null;
  const Glyph=icons[item.icon], start=actionStart(scene,target), elapsed=frame-(start ?? frame);
  const t=clamp(elapsed/42), send=item.action==='send' ? Math.sin(t*Math.PI)*size*.1 : 0;
  return <g transform={`translate(${x} ${y})`} color={color}>
    <rect width={size} height={size} rx={size*.22} fill={color} opacity={.07}/>
    <Glyph x={size*.22+send} y={size*.22-send} width={size*.56} height={size*.56} strokeWidth={1.8}/>
    {elapsed>=0 && item.action==='process' && <circle cx={size/2} cy={size/2} r={size*.44} fill="none"
      stroke={color} strokeWidth={2} strokeDasharray={`${size*2} ${size}`} transform={`rotate(${-90+t*360} ${size/2} ${size/2})`}/>}
    {elapsed>=0 && item.action==='complete' && <g opacity={t}><circle cx={size*.82} cy={size*.8} r={size*.15} fill={color}/>
      <path d={`M${size*.74},${size*.8} l${size*.06},${size*.06} l${size*.12},${-size*.13}`} fill="none" stroke="white" strokeWidth={2}/></g>}
    {elapsed>=0 && item.action==='disconnect' && <line x1={size*.18} y1={size*.82} x2={size*(.18+.64*t)} y2={size*(.82-.64*t)} stroke="#B84A42" strokeWidth={3} strokeLinecap="round"/>}
  </g>;
}

import React from 'react';
import {interpolate} from 'remotion';
import {objectColor} from './StoryDirection';
import {ShotLayer,CameraAxis,cameraAt,visualStart,carriesPicture,type Rect} from './ShotDirector';
import {init, use} from 'echarts/core';
import {ScatterChart as EChartsScatter} from 'echarts/charts';
import {GridComponent} from 'echarts/components';
import {SVGRenderer} from 'echarts/renderers';

use([EChartsScatter, GridComponent, SVGRenderer]);
const W = 1640, H = 500;
const grid = {left: 130, right: 455, top: 88, bottom: 100, outerBoundsMode: 'none' as const};
const font = 'Hiragino Sans GB, PingFang SC, sans-serif';
const palette = ['#17784B', '#557DB2', '#C9813E', '#886CAD', '#408E94'];
const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;
type Point = {name: string; x: number; y: number; group?: string; note?: string};
type FocusStep = {indices: number[]; phrase: string; caption?: string};
type Props = {title: string; x_label: string; x_unit: string; y_label: string; y_unit: string; x_hint?: string; y_hint?: string;
  points: Point[]; takeaway?: string; focus_indices?: number[]; focus_steps?: FocusStep[]};
type Label = {x: number; y: number; width: number; height: number; text: string};
type Layout = {axis: string; dots: string[]; positions: {x: number; y: number}[]; labels: Label[]; colors: string[]};
const cache = new Map<string, Layout>();
const compact = (v: number) => v.toLocaleString('zh-CN', {notation: 'compact', maximumSignificantDigits: 4});
const exact = (v: number) => v.toLocaleString('zh-CN', {maximumSignificantDigits: 8});

function extent(values: number[]): [number, number] {
  const low = Math.min(...values), high = Math.max(...values);
  const span = high-low || Math.max(Math.abs(low)*.3, 1);
  if (low === high) return [low-span, high+span];
  const step = Math.pow(10, Math.floor(Math.log10(span)))/2;
  const min = Math.floor((low-span*.22)/step)*step;
  const max = Math.ceil((high+span*.22)/step)*step;
  return [low >= 0 ? Math.max(0,min) : min, max];
}

function labels(points: Point[], positions: Layout['positions']): Label[] {
  const placed: Label[] = [];
  const right = W-grid.right+24, bottom = H-grid.bottom+10;
  for (let i=0; i<points.length; i++) {
    const text = Array.from(points[i].name).length > 11 ? Array.from(points[i].name).slice(0,10).join('')+'…' : points[i].name;
    const width = Math.max(60, Array.from(text).reduce((n,c)=>n+(c.charCodeAt(0)>255?25:15),0)+18), height = 34;
    const anchor = positions[i];
    const candidates: Label[] = [];
    for (let step=0; step<5; step++) {
      const dy = 40+step*36;
      for (const [dx, y] of [[-width/2,-dy],[20,-height/2+step*36],[-width-20,-height/2+step*36],[-width/2,dy-20]]) {
        candidates.push({x:Math.max(grid.left-18,Math.min(right-width,anchor.x+dx)),
          y:Math.max(grid.top+4,Math.min(bottom-height,anchor.y+y)),width,height,text});
      }
    }
    const score = (box: Label) => {
      const overlaps = placed.reduce((n,b)=>n+Math.max(0,Math.min(box.x+box.width,b.x+b.width+8)-Math.max(box.x,b.x-8))*Math.max(0,Math.min(box.y+box.height,b.y+b.height+5)-Math.max(box.y,b.y-5)),0);
      const onDots = positions.filter(p=>p.x>box.x-15&&p.x<box.x+box.width+15&&p.y>box.y-12&&p.y<box.y+box.height+12).length;
      return overlaps*100+onDots*10000+Math.hypot(box.x+box.width/2-anchor.x,box.y+box.height/2-anchor.y);
    };
    placed.push(candidates.reduce((a,b)=>score(a)<=score(b)?a:b));
  }
  return placed;
}

export function scatterLayout(p: Props, entityColors:(string|undefined)[]=[]): Layout {
  const key = JSON.stringify({p,entityColors});
  const cached = cache.get(key);
  if (cached) return cached;
  const [xmin,xmax] = extent(p.points.map(v=>v.x)), [ymin,ymax] = extent(p.points.map(v=>v.y));
  const groups = Array.from(new Set(p.points.map(v=>v.group||'')));
  const colors = p.points.map((v,i)=>entityColors[i]??palette[groups.indexOf(v.group||'')%palette.length]);
  let positions: Layout['positions'] = [];
  const draw = (index: number) => {
    const chart = init(null, undefined, {renderer:'svg',ssr:true,width:W,height:H});
    try {
      chart.setOption({animation:false,textStyle:{fontFamily:font},grid,
        xAxis:{type:'value',min:xmin,max:xmax,splitNumber:4,show:index<0,axisLine:{onZero:false,lineStyle:{color:'#B8C8BA'}},axisTick:{show:false},
          axisLabel:{fontSize:27,color:'#69756E',margin:17,formatter:compact},splitLine:{lineStyle:{color:'#E8EDE7',type:'dashed'}}},
        yAxis:{type:'value',min:ymin,max:ymax,splitNumber:4,show:index<0,axisLine:{show:false,onZero:false},axisTick:{show:false},
          axisLabel:{fontSize:27,color:'#69756E',formatter:compact},splitLine:{lineStyle:{color:'#E8EDE7',type:'dashed'}}},
        series:[{type:'scatter',silent:true,animation:false,symbol:'circle',symbolSize:24,
          data:index<0?[]:[{value:[p.points[index].x,p.points[index].y],itemStyle:{color:colors[index],borderColor:'#FFFFFF',borderWidth:2}}]}]});
      if (index<0) positions = p.points.map(v=>({x:Number(chart.convertToPixel({xAxisIndex:0},v.x)),y:Number(chart.convertToPixel({yAxisIndex:0},v.y))}));
      return chart.renderToSVGString();
    } finally {chart.dispose();}
  };
  const axis = draw(-1), dots = p.points.map((_,i)=>draw(i));
  const result = {axis,dots,positions,labels:labels(p.points,positions),colors};
  if (cache.size>=32) cache.delete(cache.keys().next().value!);
  cache.set(key,result);
  return result;
}

export function scatterState(scene:any,frame:number):{times:number[];shown:number[];selected:number[];since:number;caption?:string} {
  const p=scene.props as Props;
  const start=scene.start_frame,end=scene.end_frame,count=p.points.length,steps=p.focus_steps||[];
  const at=(index:number,fallback:number)=>scene.beats.find((b:any)=>b.target_id===`line:${index}`)?.frame??fallback;
  const times=p.points.map((_,i)=>visualStart(scene,at(i,start+(end-start)*(i+.3)/(count+steps.length+1)),15));
  const stepTimes=steps.map((_,i)=>visualStart(scene,at(count+i,start+(end-start)*(.65+i*.08)),15));
  const endingAt=visualStart(scene,at(count+steps.length,start+(end-start)*.84),15);
  const hasEnding=Boolean(p.takeaway||p.focus_indices?.length);
  const carried=carriesPicture(scene)?scatterState(scene.previous_scene,scene.previous_scene.end_frame-1):undefined;
  const cues=scene.props.shot_plan?.cues;
  const spoken=(index:number)=>!cues||cues.some((cue:any)=>
    scene.beats.some((beat:any)=>beat.id===cue.beat_id&&beat.target_id===`line:${index}`));
  // Reveals and emphasis share one chronological timeline. A new point can
  // replace an earlier focus step instead of leaving that step active forever.
  const events=[...times.flatMap((time,i)=>spoken(i)?[{time,indices:[i],caption:undefined as string|undefined}]:[]),
    ...stepTimes.flatMap((time,i)=>spoken(count+i)?[{time,indices:steps[i].indices,caption:steps[i].caption}]:[]),
    ...(hasEnding?[{time:endingAt,indices:p.focus_indices||p.points.map((_,i)=>i),caption:p.takeaway}]:[])]
    .sort((a,b)=>a.time-b.time);
  const active=events.filter(event=>event.time<=frame).slice(-1)[0];
  carried?.shown.forEach(i=>{times[i]=scene.start_frame-15;});
  steps.forEach((step,i)=>step.indices.forEach(index=>{times[index]=Math.min(times[index],stepTimes[i]);}));
  if(hasEnding)times.forEach((time,i)=>{times[i]=Math.min(time,endingAt);});
  const shown=times.map((t,i)=>frame>=t?i:-1).filter(i=>i>=0);
  const selected=(active?.indices||carried?.selected||[]).filter(i=>shown.includes(i));
  return {times,shown,selected,since:active?.time??(carried?start-15:start),caption:active?.caption};
}

export function ScatterChart({scene,frame}: {scene:any;frame:number}) {
  const p = scene.props as Props;
  const colors=p.points.map((_,i)=>objectColor(scene,`point:${i}`,'')||undefined);
  const layout = React.useMemo(()=>scatterLayout(p,colors),[p,colors.join()]);
  const {times,shown,selected,since,caption}=scatterState(scene,frame);
  const focus=interpolate(frame-since,[0,15],[0,1],clamp);
  const enter=carriesPicture(scene)?1:interpolate(frame-scene.start_frame,[0,12],[0,1],clamp);
  const unit=(label:string,value:string)=>label+(value?` · ${value}`:'');
  const rects:Record<string,Rect>=Object.fromEntries(layout.positions.map((pos,i)=>{
    const label=layout.labels[i], x=Math.min(pos.x-22,label.x), y=Math.min(pos.y-22,label.y);
    return [`point:${i}`,{x,y,width:Math.max(pos.x+22,label.x+label.width)-x,height:Math.max(pos.y+22,label.y+label.height)-y}];
  }));
  // Leave room for the rightmost tick label before the fixed metric cards.
  const view={x:24,y:70,width:W-grid.right+30,height:H-125};
  const camera=cameraAt(scene,frame,rects,view);
  return <div style={{position:'absolute',left:140,top:270,width:W,height:600,background:'#FFFFFF',border:'1px solid #E1E8E1',borderRadius:22,boxSizing:'border-box',overflow:'hidden',opacity:enter}}>
    <div style={{position:'absolute',left:grid.left,top:20,fontSize:28,color:'#526A60'}}>{unit(p.y_label,p.y_unit)}{p.y_hint&&<span style={{marginLeft:22,fontSize:23,color:'#849288'}}>{p.y_hint}</span>}</div>
    <ShotLayer scene={scene} frame={frame} rects={rects} view={view} width={W} height={H}>
    <CameraAxis sceneId={scene.id} svg={layout.axis} zoom={camera.pose.zoom}/>

    {layout.dots.map((svg,i)=> {
      const reveal=interpolate(frame-times[i],[0,14],[0,1],clamp),pos=layout.positions[i];
      const dim=selected.length&&!selected.includes(i)?.45:1;
      return <div key={i} style={{position:'absolute',left:0,top:0,width:W,height:H,opacity:reveal*dim,transform:`scale(${.7+.3*reveal})`,transformOrigin:`${pos.x}px ${pos.y}px`}} dangerouslySetInnerHTML={{__html:svg}}/>;
    })}
    <svg width={W} height={H} style={{position:'absolute',inset:0,pointerEvents:'none'}}>
      {shown.map(i=> {
        const pos=layout.positions[i],label=layout.labels[i],active=selected.includes(i),reveal=interpolate(frame-times[i],[0,14],[0,1],clamp);
        const anchorX=Math.max(label.x,Math.min(label.x+label.width,pos.x)),anchorY=Math.max(label.y,Math.min(label.y+label.height,pos.y));
        return <g key={i} opacity={reveal*(active?1:.65)}>
          <line x1={pos.x} y1={pos.y} x2={anchorX} y2={anchorY} stroke="#A5B8AB" strokeWidth={1.5}/>
          <rect x={label.x} y={label.y} width={label.width} height={label.height} rx={7} fill="#FFFFFF" fillOpacity={.94}/>
          <text x={label.x+label.width/2} y={label.y+25} textAnchor="middle" fontFamily={font} fontSize={25} fontWeight={active?700:500} fill={active?layout.colors[i]:'#69756E'}>{label.text}</text>
        </g>;
      })}
      {selected.map(i=> {
        const pos=layout.positions[i];
        return <g key={i} opacity={focus}>
          <path d={`M${grid.left} ${pos.y}H${pos.x}V${H-grid.bottom}`} fill="none" stroke={layout.colors[i]} strokeOpacity={.42} strokeWidth={2} strokeDasharray="6 7"/>
          <circle cx={pos.x} cy={pos.y} r={21+4*focus} fill="none" stroke={layout.colors[i]} strokeWidth={3} pathLength={1} strokeDasharray={1} strokeDashoffset={1-focus}/>
        </g>;
      })}
    </svg>
    </ShotLayer>
    <div style={{position:'absolute',left:grid.left,top:H-43,width:W-grid.left-grid.right,textAlign:'center',fontSize:28,color:'#526A60'}}>{unit(p.x_label,p.x_unit)}{p.x_hint&&<div style={{fontSize:22,color:'#849288',marginTop:4}}>{p.x_hint}</div>}</div>
    <div style={{position:'absolute',left:1240,top:82,width:350,display:'flex',flexDirection:'column',gap:14}}>
      <div style={{fontSize:23,color:'#849288',marginBottom:5}}>{selected.length>1?'放在一起比较':'当前讲解对象'}</div>
      {!selected.length&&<div style={{fontSize:30,color:'#849288',lineHeight:1.6}}>跟随讲述<br/>看见每个对象的位置</div>}
      {selected.map(i=> {
        const v=p.points[i],many=selected.length>1;
        return <div key={i} style={{padding:many?'12px 18px':'22px',borderRadius:16,background:'#F5F8F3',borderLeft:`4px solid ${layout.colors[i]}`,boxSizing:'border-box'}}>
          <div style={{fontSize:many?(selected.length===3?24:28):(v.name.length>8?28:34),fontWeight:700,color:layout.colors[i],lineHeight:1.35,overflowWrap:'anywhere',...(many?{whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis'}:{})}}>{v.name}</div>
          {many?<div style={{fontSize:selected.length===3?21:23,lineHeight:1.65,color:'#526A60',marginTop:7}}>{[[p.x_label,v.x,p.x_unit],[p.y_label,v.y,p.y_unit]].map(([name,value,u],j)=><div key={j} style={{display:'flex',gap:10,justifyContent:'space-between'}}><span style={{maxWidth:125,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{name}</span><span style={{whiteSpace:'nowrap'}}>{compact(value as number)}{u}</span></div>)}</div>
            :<div style={{display:'flex',flexDirection:'column',gap:17,marginTop:22}}>{[[p.x_label,v.x,p.x_unit],[p.y_label,v.y,p.y_unit]].map(([name,value,u],j)=><div key={j}><div style={{fontSize:24,color:'#849288'}}>{name}</div><div style={{fontSize:36,fontWeight:650,color:'#213D39',marginTop:3}}>{exact(value as number)}<span style={{fontSize:23,fontWeight:400,marginLeft:8}}>{u}</span></div></div>)}</div>}
          {!many&&v.note&&<div style={{fontSize:23,lineHeight:1.4,color:'#69756E',marginTop:16,display:'-webkit-box',WebkitLineClamp:2,WebkitBoxOrient:'vertical',overflow:'hidden'}}>{v.note}</div>}
        </div>;
      })}
    </div>
    {caption&&<div style={{position:'absolute',left:70,right:70,bottom:18,fontSize:31,lineHeight:1.35,color:'#17784B',fontWeight:650,opacity:focus}}>{caption}</div>}
  </div>;
}

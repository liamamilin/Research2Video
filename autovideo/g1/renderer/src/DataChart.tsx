import React from 'react';
import {interpolate} from 'remotion';
import {ShotLayer,CameraAxis,cameraAt,type Rect} from './ShotDirector';
import {init, use} from 'echarts/core';
import {LineChart, BarChart} from 'echarts/charts';
import {GridComponent} from 'echarts/components';
import {SVGRenderer} from 'echarts/renderers';

use([LineChart, BarChart, GridComponent, SVGRenderer]);
const palette = ['#17784B', '#557DB2', '#E48B48'];
const W = 1640, H = 466, grid = {left: 122, right: 70, top: 88, bottom: 82, outerBoundsMode: 'none' as const};
const font = 'Hiragino Sans GB, PingFang SC, sans-serif';
const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;
type Props = {title: string; kind: 'trend' | 'grouped'; unit: string; categories: string[]; series: {name: string; values: (number | null)[]}[]; takeaway?: string; focus_index?: number; focus_series?: number};
type Layers = {axis: string; series: string[]; focusX: number; positions: ({x:number;y:number}|null)[][]};
const cache = new Map<string, Layers>();

function layers(p: Props): Layers {
  const key = JSON.stringify(p);
  const cached = cache.get(key);
  if (cached) return cached;
  const values = p.series.flatMap(s => s.values).filter((v): v is number => v !== null);
  const low = Math.min(0, ...values), high = Math.max(0, ...values);
  const span = high - low || 1;
  const step = Math.pow(10, Math.floor(Math.log10(span))) / 2;
  const min = low < 0 ? Math.floor((low-span*.12)/step)*step : 0;
  const max = Math.ceil((high+span*.2)/step)*step;
  let focusX = 0, positions:Layers['positions'] = [];
  const draw = (which: number) => {
    const chart = init(null, undefined, {renderer: 'svg', ssr: true, width: W, height: H});
    try {
      chart.setOption({animation: false, textStyle: {fontFamily: font}, grid,
        xAxis: {type: 'category', data: p.categories, boundaryGap: p.kind === 'grouped',
          show: which < 0, axisLine: {lineStyle: {color: '#B8C8BA'}}, axisTick: {show: false},
          axisLabel: {interval: 0, fontSize: 30, color: '#69756E', margin: 20,
            width: (W-grid.left-grid.right)/p.categories.length-14, overflow: 'break', lineHeight: 33}},
        yAxis: {type: 'value', min, max, splitNumber: 4, show: which < 0,
          axisLabel: {fontSize: 28, color: '#69756E', formatter: (v: number) => v.toLocaleString('zh-CN', {notation: 'compact', maximumSignificantDigits: 3})},
          splitLine: {lineStyle: {color: '#E8EDE7', type: 'dashed'}}, axisLine: {show: false}},
        series: p.series.map((s, i) => ({name: s.name, type: p.kind === 'trend' ? 'line' : 'bar',
          data: s.values.map(v => i === which ? v : null), silent: true, animation: false,
          connectNulls: false, smooth: false, symbol: 'circle', symbolSize: 15, showSymbol: true,
          lineStyle: {width: 5, color: palette[i]}, itemStyle: {color: palette[i], borderRadius: [7, 7, 0, 0]},
          barMaxWidth: 64, barGap: '25%', barCategoryGap: '40%',
          label: {show: p.categories.length <= 5, position: 'top', color: palette[i], fontSize: 30, distance: 13}}))});
      if (which < 0) {
        focusX = Number(chart.convertToPixel({xAxisIndex: 0}, p.focus_index ?? p.categories.length - 1));
        positions = p.series.map(s=>s.values.map((v,i)=>{
          if(v===null)return null;
          const point=chart.convertToPixel({xAxisIndex:0,yAxisIndex:0},[i,v]) as number[];
          return {x:point[0],y:point[1]};
        }));
      }
      return chart.renderToSVGString();
    } finally {chart.dispose();}
  };
  const axis = draw(-1), series = p.series.map((_, i) => draw(i));
  const result = {axis, series, focusX, positions};
  if (cache.size >= 32) cache.delete(cache.keys().next().value!);
  cache.set(key, result);
  return result;
}

export function DataChart({scene, frame}: {scene: any; frame: number}) {
  const p = scene.props as Props;
  const chart = React.useMemo(() => layers(p), [p]);
  const at = (target: string, fallback: number) => scene.beats.find((b: any) => b.target_id === target)?.frame ?? fallback;
  const start = scene.start_frame, end = scene.end_frame;
  const count = p.kind === 'trend' ? p.categories.length : p.series.length;
  const times = Array.from({length: count}, (_, i) => at(`line:${i}`, start + (end-start) * (i+.3)/(count+1)));
  const takeawayAt = at(`line:${count}`, start + (end-start)*.78);
  // SVG is static. Every changing property below is a pure function of the video frame.
  const point = p.kind === 'trend' ? Math.max(0, ...times.map((t, i) => frame >= t ? i + (i<count-1 ? interpolate(frame,[t,Math.max(t+1,times[i+1])],[0,1],clamp) : 0) : 0)) : 0;
  const right = W - (grid.left + (W-grid.left-grid.right)*point/Math.max(1,count-1) + 18);
  const focused = interpolate(frame, [takeawayAt, takeawayAt+16], [0,1], clamp);
  const rects:Record<string,Rect>={};
  const bounds=(points:({x:number;y:number}|null)[],base=false):Rect|undefined=>{
    const found=points.filter((v):v is {x:number;y:number}=>v!==null);
    if(!found.length)return undefined;
    const x=Math.min(...found.map(v=>v.x)), y=Math.min(...found.map(v=>v.y));
    const right=Math.max(...found.map(v=>v.x)), bottom=base?H-grid.bottom:Math.max(...found.map(v=>v.y));
    return {x:x-34,y:y-36,width:right-x+68,height:bottom-y+72};
  };
  p.categories.forEach((_,i)=>{const box=bounds(chart.positions.map(s=>s[i]));if(box)rects[`category:${i}`]=box;});
  p.series.forEach((_,i)=>{const box=bounds(chart.positions[i],p.kind==='grouped');if(box)rects[`series:${i}`]=box;});
  const view={x:20,y:70,width:W-40,height:H-70}, camera=cameraAt(scene,frame,rects,view);
  const selectedSeries=camera.shot.targets.filter(t=>t.startsWith('series:')).map(t=>Number(t.split(':')[1]));
  const focusLabel=camera.shot.kind==='focus' ? camera.shot.targets.map(t=>t.startsWith('category:')?p.categories[Number(t.split(':')[1])]:p.series[Number(t.split(':')[1])]?.name).filter(Boolean).join(' → ') : '';
  return <div style={{position:'absolute',left:140,top:300,width:W,height:550,background:'#FFFFFF',border:'1px solid #E1E8E1',borderRadius:22,boxSizing:'border-box',overflow:'hidden'}}>
    <div style={{position:'absolute',left:122,top:23,fontSize:26,color:'#69756E'}}>{p.unit}</div>
    <div style={{position:'absolute',right:70,top:22,display:'flex',gap:35}}>{p.series.map((s,i)=><div key={i} style={{display:'flex',alignItems:'center',gap:12,fontSize:29,color:palette[i],fontWeight:selectedSeries.includes(i)||p.focus_series===i?700:500}}><span style={{width:22,height:7,background:palette[i],borderRadius:4}}/>{s.name}</div>)}</div>
    <ShotLayer scene={scene} frame={frame} rects={rects} view={view} width={W} height={H}>
    <CameraAxis sceneId={scene.id} svg={chart.axis} zoom={camera.pose.zoom}/>
    {chart.series.map((svg,i)=> {
      const reveal = interpolate(frame,[times[p.kind==='trend'?0:i],times[p.kind==='trend'?0:i]+22],[0,1],clamp);
      return <div key={i} style={{position:'absolute',inset:0,opacity:reveal*(selectedSeries.length&&!selectedSeries.includes(i)?.48:p.focus_series!==undefined&&p.focus_series!==i?1-.48*focused:1),clipPath:p.kind==='trend'?`inset(0 ${Math.max(0,right)}px 0 0)`:`inset(${(H-grid.bottom)*(1-reveal)}px 0 0 0)`}} dangerouslySetInnerHTML={{__html:svg}}/>;
    })}
    <svg width={W} height={H} style={{position:'absolute',inset:0,pointerEvents:'none'}}>
      <rect x={chart.focusX-(p.kind==='trend'?24:((W-grid.left-grid.right)/p.categories.length)*.42)} y={grid.top-10}
        width={p.kind==='trend'?48:((W-grid.left-grid.right)/p.categories.length)*.84} height={H-grid.top-grid.bottom+20}
        fill="#EAA64B" fillOpacity={p.focus_series===undefined?.11*focused:0} stroke="#D48A3B" strokeWidth={2} strokeOpacity={p.focus_series===undefined?focused:0} rx={12}/>
    </svg>
    </ShotLayer>
    {(focusLabel||p.takeaway) && <div style={{position:'absolute',left:70,right:70,bottom:18,fontSize:(focusLabel||p.takeaway||'').length>40?29:33,lineHeight:1.35,color:'#17784B',fontWeight:650,opacity:focusLabel?1:focused,transform:`translateY(${focusLabel?0:(1-focused)*8}px)`}}>{focusLabel||p.takeaway}</div>}
  </div>;
}

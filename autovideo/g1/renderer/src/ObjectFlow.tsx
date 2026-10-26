import React from 'react';
import {SvgVisualObject,visualFor} from './VisualObjects';
import {cameraAt,flowRects} from './ShotDirector';

export function ObjectFlow({scene,frame}:{scene:any;frame:number}) {
  const p=scene.props, layout=p.layout, reveal=scene.beats.filter((b:any)=>b.action==='reveal');
  const starts=(target:string)=>reveal.find((b:any)=>b.target_id===target)?.frame;
  const alpha=(target:string)=>{
    const at=starts(target); return at===undefined || frame<at ? .12 : Math.min(1,.2+(frame-at)/14);
  };
  const active = reveal.filter((b:any)=>b.frame<=frame).sort((a:any,b:any)=>a.frame-b.frame).at(-1)?.target_id;
  const ends=new Map(layout.nodes.map((n:any)=>[n.id,n]));
  const green='#17784B', ink='#202D28', soft='#69756E';
  const camera=cameraAt(scene,frame,flowRects(p),{x:0,y:0,width:1560,height:500});
  const clipId=`${scene.id}_camera_clip`;
  const focused=camera.shot.kind==='focus' ? camera.shot.targets : [];
  const focusLabel=focused.map(t=>p.nodes.find((n:any)=>`node:${n.id}`===t)?.label).filter(Boolean).join(' · ');
  const pathPoint=(points:any[],t:number)=>{
    const lengths=points.slice(1).map((b:any,i:number)=>Math.hypot(b.x-points[i].x,b.y-points[i].y));
    let remaining=lengths.reduce((a:number,b:number)=>a+b,0)*t;
    for(let i=0;i<lengths.length;i++){
      if(remaining<=lengths[i]){const f=remaining/Math.max(.001,lengths[i]);return {x:points[i].x+(points[i+1].x-points[i].x)*f,y:points[i].y+(points[i+1].y-points[i].y)*f};}
      remaining-=lengths[i];
    }return points.at(-1);
  };
  return <svg width="1920" height="1080" viewBox="0 0 1920 1080" style={{position:'absolute',inset:0}}>
    <defs><marker id={`${scene.id}_object_arrow`} viewBox="0 0 12 12" refX="11" refY="6" markerWidth="5" markerHeight="5" orient="auto"><path d="M0,0 L12,6 L0,12 z" fill={green}/></marker></defs>
    <g transform="translate(180 305)">
      <defs><clipPath id={clipId}><rect width={1560} height={500}/></clipPath></defs>
      <g clipPath={`url(#${clipId})`}><g transform={camera.transform}>
      {layout.edges.map((edge:any)=>{
        const data=p.edges.find((e:any)=>e.id===edge.id), target=`edge:${edge.id}`, at=starts(target);
        const endpoints=[data.from,data.to].map((id:string)=>ends.get(id) as any);
        const ready=endpoints.every(n=>starts(`node:${n.id}`)!==undefined && starts(`node:${n.id}`)!<=frame);
        const t=at===undefined ? -1 : (frame-at)/54, point=pathPoint(edge.points,Math.max(0,Math.min(1,t)));
        const blocked=data.relation==='blocked', color=blocked ? '#B84A42' : green;
        return <g key={edge.id} opacity={ready ? alpha(target) : .1}>
          <polyline points={edge.points.map((v:any)=>`${v.x},${v.y}`).join(' ')} fill="none" stroke={color} strokeWidth={3} strokeLinejoin="round" strokeDasharray={blocked?'10 7':undefined} markerEnd={blocked?undefined:`url(#${scene.id}_object_arrow)`}/>
          {ready && t>=0 && t<1 && !blocked && <g transform={`translate(${point.x} ${point.y})`}><rect x={-9} y={-11} width={18} height={22} rx={3} fill="white" stroke={green} strokeWidth={2}/><path d="M-4 -3 H4 M-4 3 H4" stroke={green} strokeWidth={1.5}/></g>}
          {data.label && <g><rect x={edge.label_x-data.label.length*layout.edge_font_size/2-10} y={edge.label_y-layout.edge_font_size/2-6} width={data.label.length*layout.edge_font_size+20} height={layout.edge_font_size+12} rx={5} fill="#F7F8F5"/><text x={edge.label_x} y={edge.label_y} dominantBaseline="middle" textAnchor="middle" fontSize={layout.edge_font_size} fill={soft}>{data.label}</text></g>}
        </g>;
      })}
      {layout.nodes.map((node:any)=>{
        const target=`node:${node.id}`, icon=visualFor(scene,target), high=focused.includes(target) || active===target || p.edges.some((e:any)=>active===`edge:${e.id}`&&(e.from===node.id||e.to===node.id));
        const size=58*layout.scale, middle=node.y+node.height/2;
        return <g key={node.id} opacity={alpha(target)}>
          <rect x={node.x} y={node.y} width={node.width} height={node.height} rx={18*layout.scale} fill={high?'#EDF6EF':'white'} stroke={high?green:'#DCE6DC'} strokeWidth={high?2.5:1.5}/>
          {icon && <SvgVisualObject scene={scene} target={target} frame={frame} x={node.x+(node.width-size)/2} y={node.y+22*layout.scale} size={size}/>}
          {node.label_lines.map((line:string,i:number)=><text key={i} x={node.x+node.width/2} y={icon ? node.y+(112+i*46)*layout.scale : middle+(i-(node.label_lines.length-1)/2)*layout.font_size*1.28} dominantBaseline="middle" textAnchor="middle" fontSize={layout.font_size} fontWeight={600} fill={ink}>{line}</text>)}
        </g>;
      })}
      </g></g>
    </g>
    <text x={960} y={856} textAnchor="middle" fontSize={p.takeaway.length>50?26:30} fill={soft} opacity={focusLabel || p.nodes.every((n:any)=>(starts(`node:${n.id}`)??Infinity)<=frame)?1:0}>{focusLabel || p.takeaway}</text>
  </svg>;
}

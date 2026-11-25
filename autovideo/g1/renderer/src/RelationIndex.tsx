import React from 'react';
import {ContentStyleContext,VisualScaleContext} from './ContentStyle';
import {measureText,wrapMeasured} from './TextFit';
import {objectColor} from './StoryDirection';
import {visualStart} from './ShotDirector';
import {type RelationLayout} from './RelationLayout';

const textPieces=(text:string,size:number)=>text.split(/(?<=-)(?=-)/).map((part,i)=><tspan key={i} dx={i?size*.12:0}>{part}</tspan>);

export function RelationIndex({scene,frame,layout}:{scene:any;frame:number;layout:RelationLayout}) {
  const palette=React.useContext(ContentStyleContext),p=scene.props;
  const events=scene.beats.filter((b:any)=>/^(node|edge):/.test(b.target_id??'')&&visualStart(scene,b.frame)<=frame);
  const latest=Math.max(-Infinity,...events.map((b:any)=>b.frame));
  const active=new Set(events.filter((b:any)=>b.frame===latest).map((b:any)=>b.target_id));
  const numbers=new Map(layout.nodes.map(n=>[n.id,n.number]));
  const green=palette?.accent??'#17784B',ink=palette?.text??'#202D28',muted=palette?.muted??'#69756E';
  const activeEdges=new Set(p.edges.filter((e:any)=>active.has(`edge:${e.id}`)||active.has(`node:${e.from}`)||active.has(`node:${e.to}`)).map((e:any)=>e.id));
  return <svg data-relation-presentation="index" width="1920" height="1080" viewBox="0 0 1920 1080" style={{position:'absolute',inset:0}}>
    <g transform="translate(180 300)">
      <rect width={1560} height={590} rx={12} fill={palette?.background??'#F7F8F5'}/>
      {layout.nodes.map(node=>{
        const target=`node:${node.id}`,high=active.has(target),color=objectColor(scene,target,green),badge=layout.edgeFont*1.2;
        return <g key={node.id} data-relation-node={node.id}>
          <rect x={node.x} y={node.y} width={node.width} height={node.height} rx={10} fill={high?`${color}10`:palette?.surface??'#FFFFFF'} stroke={high?color:palette?.line??'#DCE6DC'} strokeWidth={high?2:1}/>
          <rect x={node.x+23-badge/2} y={node.y+(node.height-badge)/2} width={badge} height={badge} rx={7} fill={color}/>
          <text x={node.x+23} y={node.y+node.height/2} dominantBaseline="middle" textAnchor="middle" fontSize={layout.edgeFont} fill="white">{node.number}</text>
          {node.lines.map((line,i)=><text key={i} x={node.x+48} y={node.y+node.height/2+(i-(node.lines.length-1)/2)*layout.nodeFont*1.1} dominantBaseline="middle" fontSize={layout.nodeFont} fontWeight={600} fill={ink}>{textPieces(line,layout.nodeFont)}</text>)}
        </g>;
      })}
      {layout.edges.map(edge=>{
        const high=activeEdges.has(edge.id),blocked=edge.relation==='blocked',color=blocked?'#B84A42':high?green:muted;
        const center=edge.y+edge.height/2;
        return <g key={edge.id} data-relation-edge={edge.id}>
          <rect x={edge.x} y={edge.y} width={edge.width} height={edge.height} rx={4} fill={high?`${color}0B`:'transparent'}/>
          <text x={edge.x+18} y={center} dominantBaseline="middle" textAnchor="middle" fontSize={layout.edgeFont} fontWeight={650} fill={color}>{numbers.get(edge.from)}</text>
          <path d={`M${edge.x+40},${center}h38m-7,-5l7,5l-7,5`} fill="none" stroke={color} strokeWidth={2}/>
          {blocked&&<path d={`M${edge.x+53},${center-6}l10,12m0,-12l-10,12`} stroke={color} strokeWidth={2.5}/>}
          <text x={edge.x+100} y={center} dominantBaseline="middle" textAnchor="middle" fontSize={layout.edgeFont} fontWeight={650} fill={color}>{numbers.get(edge.to)}</text>
          {edge.lines.map((line,i)=><text key={i} x={edge.x+135} y={center+(i-(edge.lines.length-1)/2)*layout.edgeFont*1.1} dominantBaseline="middle" fontSize={layout.edgeFont} fill={color}>{textPieces(line,layout.edgeFont)}</text>)}
        </g>;
      })}
      {layout.footer.map((line,i)=><text key={i} x={780} y={layout.footerY+(i+.5)*layout.footerFont*1.1} dominantBaseline="middle" textAnchor="middle" fontSize={layout.footerFont} fill={muted}>{textPieces(line,layout.footerFont)}</text>)}
    </g>
  </svg>;
}

type Edge={id:string;from:string;to:string;label?:string;relation?:string};
type Group={edges:Edge[];left:string[];right:string[];label:string;blocked:boolean};
export function namedRelationGroups(props:any):Group[] {
  let remaining=[...props.edges] as Edge[];
  const groups:Group[]=[];
  const add=(edges:Edge[])=>groups.push({edges,left:[...new Set(edges.map(e=>e.from))],right:[...new Set(edges.map(e=>e.to))],label:edges[0].label??'',blocked:edges[0].relation==='blocked'});
  for(const side of ['from','to'] as const){
    const buckets=new Map<string,Edge[]>();
    for(const e of remaining){const key=JSON.stringify([e[side],e.label??'',e.relation??'flow']);buckets.set(key,[...(buckets.get(key)??[]),e]);}
    for(const edges of buckets.values())if(edges.length>1){add(edges);const used=new Set(edges.map(e=>e.id));remaining=remaining.filter(e=>!used.has(e.id));}
  }
  for(const edge of remaining)add([edge]);
  const order=new Map(props.edges.map((e:Edge,i:number)=>[e.id,i]));
  return groups.sort((a,b)=>Math.min(...a.edges.map(e=>order.get(e.id) as number))-Math.min(...b.edges.map(e=>order.get(e.id) as number)));
}

export function namedRelationLayout(props:any,scale=1,measure=measureText){
  const groups=namedRelationGroups(props),font=40/scale,relationFont=30/scale;
  const nodes=new Map<string,any>(props.nodes.map((n:any)=>[n.id,n]));
  const side=(ids:string[])=>{
    const columns=ids.length>3?2:1,width=(620-(columns-1)*20)/columns;
    const items=ids.map(id=>({id,lines:wrapMeasured(nodes.get(id).label,width-24,font,600,measure)}));
    const rows=[];for(let i=0;i<items.length;i+=columns)rows.push(Math.max(...items.slice(i,i+columns).map(n=>n.lines.length*font*1.25+16)));
    return {items,columns,rows,height:rows.reduce((a,b)=>a+b,0)+Math.max(0,rows.length-1)*10};
  };
  const rows=groups.map(group=>{const left=side(group.left),right=side(group.right),label=wrapMeasured(group.label,230,relationFont,500,measure);return {...group,left,right,label,height:Math.max(left.height,right.height,label.length*relationFont*1.25+45,groups.length<=2?150:0)};});
  const height=rows.reduce((sum,row)=>sum+row.height,0)+Math.max(0,rows.length-1)*16;
  const covered=new Set(groups.flatMap(g=>[...g.left,...g.right]));
  const footer=props.takeaway?wrapMeasured(props.takeaway,1560,24/scale,400,measure):[];
  const footerHeight=footer.length*24/scale*1.25;
  const bodyHeight=footer.length?Math.min(560,590-footerHeight-16):560;
  return {rows,font,relationFont,height,footer,bodyHeight,footerTop:300+bodyHeight+16,
    fits:height<=bodyHeight&&footer.length<=2&&props.nodes.every((n:any)=>covered.has(n.id))};
}

export function NamedRelations({scene,frame}:{scene:any;frame:number}){
  const palette=React.useContext(ContentStyleContext),scale=React.useContext(VisualScaleContext);
  const layout=React.useMemo(()=>namedRelationLayout(scene.props,scale),[scene,scale]);
  const events=scene.beats.filter((b:any)=>/^(node|edge):/.test(b.target_id??'')&&visualStart(scene,b.frame)<=frame);
  const latest=Math.max(-Infinity,...events.map((b:any)=>b.frame));
  const active=new Set(events.filter((b:any)=>b.frame===latest).map((b:any)=>b.target_id));
  const ink=palette?.text??'#202D28',green=palette?.accent??'#17784B',muted=palette?.muted??'#69756E';
  return <><div data-relation-presentation="named" data-named-fits={layout.fits} style={{position:'absolute',left:180,top:300,width:1560,height:layout.bodyHeight,display:'flex',flexDirection:'column',justifyContent:'center',gap:16,color:ink}}>
    {layout.rows.map(row=>{
      const edgeActive=row.edges.some(e=>active.has(`edge:${e.id}`)),color=row.blocked?'#B84A42':green;
      const side=(data:typeof row.left)=><div style={{display:'grid',gridTemplateColumns:`repeat(${data.columns},1fr)`,gridTemplateRows:data.rows.map(h=>`${h}px`).join(' '),gap:'10px 20px',alignContent:'center'}}>{data.items.map(node=>{
        const high=active.has(`node:${node.id}`)||edgeActive;
        return <div key={node.id} style={{fontSize:layout.font,fontWeight:600,lineHeight:1.25,padding:'8px 12px',boxSizing:'border-box',borderRadius:12,background:high?`${color}12`:palette?.surface??'#FFFFFF',boxShadow:high?`inset 0 0 0 2px ${color}`:undefined}}>{node.lines.map((line,i)=><div key={i}>{line}</div>)}</div>;
      })}</div>;
      return <div key={row.edges.map(e=>e.id).join('|')} style={{display:'grid',gridTemplateColumns:'620px 240px 620px',gap:40,height:row.height,alignItems:'center'}}>
        {side(row.left)}<div style={{textAlign:'center',color:edgeActive?color:muted,fontSize:layout.relationFont,lineHeight:1.25}}>{row.label.map((line,i)=><div key={i}>{line}</div>)}<svg width="150" height="30" viewBox="0 0 150 30" style={{display:'block',margin:'7px auto 0'}}><path d="M5 15 H140 M128 5 L140 15 L128 25" fill="none" stroke={color} strokeWidth="3" strokeDasharray={row.blocked?'8 5':undefined}/>{row.blocked&&<path d="M65 3 L85 27 M85 3 L65 27" stroke={color} strokeWidth="4"/>}</svg></div>{side(row.right)}
      </div>;
    })}
  </div><div style={{position:'absolute',left:180,top:layout.footerTop,width:1560,textAlign:'center',fontSize:24/scale,lineHeight:1.25,color:muted}}>{layout.footer.map((line,i)=><div key={i}>{line}</div>)}</div></>;
}

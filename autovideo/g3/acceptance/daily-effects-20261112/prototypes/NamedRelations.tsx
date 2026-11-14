import React from 'react';
import {ContentStyleContext,VisualScaleContext} from './ContentStyle';
import {measureText,wrapMeasured} from './TextFit';
import {visualStart} from './ShotDirector';

type Edge={id:string;from:string;to:string;label?:string;relation?:string};
type Group={edges:Edge[];left:string[];right:string[];label:string;blocked:boolean};
export function namedRelationGroups(props:any,merge=true):Group[] {
  let remaining=[...props.edges] as Edge[];
  const groups:Group[]=[];
  const add=(edges:Edge[])=>groups.push({edges,left:[...new Set(edges.map(e=>e.from))],right:[...new Set(edges.map(e=>e.to))],label:edges[0].label??'',blocked:edges[0].relation==='blocked'});
  if(merge)for(const side of ['from','to'] as const){
    const buckets=new Map<string,Edge[]>();
    for(const e of remaining){const key=JSON.stringify([e[side],e.label??'',e.relation??'flow']);buckets.set(key,[...(buckets.get(key)??[]),e]);}
    for(const edges of buckets.values())if(edges.length>1){add(edges);const used=new Set(edges.map(e=>e.id));remaining=remaining.filter(e=>!used.has(e.id));}
  }
  for(const edge of remaining)add([edge]);
  const order=new Map(props.edges.map((e:Edge,i:number)=>[e.id,i]));
  return groups.sort((a,b)=>Math.min(...a.edges.map(e=>order.get(e.id) as number))-Math.min(...b.edges.map(e=>order.get(e.id) as number)));
}

export function namedRelationLayout(props:any,scale=1,merge=true,measure=measureText){
  const groups=namedRelationGroups(props,merge),font=40/scale,relationFont=30/scale;
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
  return {rows,font,relationFont,height,footer,fits:height<=560&&footer.length<=2&&props.nodes.every((n:any)=>covered.has(n.id))};
}

export function NamedRelations({scene,frame,merge=true}:{scene:any;frame:number;merge?:boolean}){
  const palette=React.useContext(ContentStyleContext),scale=React.useContext(VisualScaleContext);
  const layout=React.useMemo(()=>namedRelationLayout(scene.props,scale,merge),[scene,scale,merge]);
  const events=scene.beats.filter((b:any)=>/^(node|edge):/.test(b.target_id??'')&&visualStart(scene,b.frame)<=frame);
  const latest=Math.max(-Infinity,...events.map((b:any)=>b.frame));
  const active=new Set(events.filter((b:any)=>b.frame===latest).map((b:any)=>b.target_id));
  const ink=palette?.text??'#202D28',green=palette?.accent??'#17784B',muted=palette?.muted??'#69756E';
  return <><div data-relation-presentation="named" data-named-fits={layout.fits} style={{position:'absolute',left:180,top:300,width:1560,height:560,display:'flex',flexDirection:'column',justifyContent:'center',gap:16,color:ink}}>
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
  </div><div style={{position:'absolute',left:180,top:882,width:1560,textAlign:'center',fontSize:24/scale,lineHeight:1.25,color:muted}}>{layout.footer.map((line,i)=><div key={i}>{line}</div>)}</div></>;
}

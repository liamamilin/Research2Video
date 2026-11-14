import {measureText,wrapMeasured} from './TextFit';
import {flowCameraAt} from './ShotDirector';

type Measure=(text:string,size:number,weight:number)=>number;
type Box={id:string;x:number;y:number;width:number;height:number;lines:string[]};
export type RelationLayout={nodes:(Box&{number:number})[];
  edges:(Box&{from:string;to:string;relation:string})[];
  nodeFont:number;edgeFont:number;footerFont:number;footer:string[];footerY:number;height:number;fits:boolean};

/** One stable identity dictionary and one complete relationship ledger. No page timer. */
export function relationLayout(props:any,scale=1,measure:Measure=measureText):RelationLayout {
  const width=1560,height=590,columnGap=20,column=(width-columnGap)/2;
  let result:RelationLayout;
  for(const physicalSize of [36,34,32,30]){
    const nodeFont=physicalSize/scale,edgeFont=Math.max(24,physicalSize*7/9)/scale,footerFont=24/scale;
    const sourceNodes=[...props.nodes].sort((a:any,b:any)=>a.id.localeCompare(b.id));
    const nodes:RelationLayout['nodes']=[],edges:RelationLayout['edges']=[];
    let y=0;
    for(let start=0;start<sourceNodes.length;start+=2){
      const row=sourceNodes.slice(start,start+2).map((n:any,index:number)=>({id:n.id,number:start+index+1,
        lines:wrapMeasured(n.label,column-62,nodeFont,600,measure),x:index*(column+columnGap),y,width:column,height:0}));
      const rowHeight=Math.max(...row.map(n=>n.lines.length*nodeFont*1.1+8));
      nodes.push(...row.map(n=>({...n,height:rowHeight})));y+=rowHeight+6;
    }
    y+=6;
    for(let start=0;start<props.edges.length;start+=2){
      const row=props.edges.slice(start,start+2).map((e:any,index:number)=>({id:e.id,from:e.from,to:e.to,
        relation:e.relation??'flow',lines:wrapMeasured(e.label??'',column-150,edgeFont,400,measure),
        x:index*(column+columnGap),y,width:column,height:0}));
      const rowHeight=Math.max(...row.map(e=>e.lines.length*edgeFont*1.1));
      edges.push(...row.map(e=>({...e,height:rowHeight})));y+=rowHeight+2;
    }
    y+=6;
    const footer=props.takeaway?wrapMeasured(props.takeaway,width,footerFont,400,measure):[];
    result={nodes,edges,nodeFont,edgeFont,footerFont,footer,footerY:y,height:y+footer.length*footerFont*1.1,
      fits:y+footer.length*footerFont*1.1<=height};
    if(result.fits)return result;
  }
  return result!;
}

/** Decide once for the whole scene, including direct incoming, outgoing and parallel relations. */
export function flowPresentation(scene:any,scale=1,measure:Measure=measureText) {
  const p=scene.props;
  const beats=scene.beats.filter((b:any)=>/^(node|edge):/.test(b.target_id??''));
  const samples=beats.length?beats:[{frame:scene.start_frame,target_id:'scene'}];
  const graphReadable=samples.every((beat:any)=>{
    const camera=flowCameraAt(scene,beat.frame),layout=camera.layout;
    const nodeId=beat.target_id.startsWith('node:')?beat.target_id.slice(5):null;
    const edgeId=beat.target_id.startsWith('edge:')?beat.target_id.slice(5):null;
    const required=p.edges.filter((e:any)=>e.id===edgeId||e.from===nodeId||e.to===nodeId);
    const nodes=new Set(layout.nodes.map((n:any)=>n.id)),edges=new Set((layout.edges??[]).map((e:any)=>e.id));
    return (!nodeId||nodes.has(nodeId))&&camera.effectiveFont*scale>=30 && required.every((e:any)=>nodes.has(e.from)&&nodes.has(e.to)&&edges.has(e.id))
      && (!required.some((e:any)=>e.label)||layout.edge_font_size*camera.pose.zoom*scale>=24);
  });
  if(graphReadable)return {mode:'graph' as const,reason:'readable-relations',index:null};
  const index=relationLayout(p,scale,measure);
  return index.fits?{mode:'index' as const,reason:'spatial-decomposition',index}:
    {mode:'graph' as const,reason:'capacity-unmet',index};
}

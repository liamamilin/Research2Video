import React from 'react';

export type Rect = {x:number;y:number;width:number;height:number};
export type ShotPlan = {version:1;profile:'brief'|'balanced'|'explain';summary_beat_id?:string;
  closing?:'overview'|'hold';cues:{beat_id:string;targets:string[]}[]};
type Scene = {component?:string;start_frame:number;end_frame:number;
  previous_scene?:Scene;props:{shot_plan?:ShotPlan;scene_flow?:{entry:string;profile:keyof typeof PACING};images?:any[];focus_indices?:number[]};
  beats:{id:string;frame:number}[]};
export type Shot = {kind:'overview'|'carry'|'focus'|'summary';frame:number;targets:string[]};
type Pose = {zoom:number;x:number;y:number};
export const PACING = {
  brief:{overview:27,hold:42,summary:30,transition:10,maxZoom:1.65,minScene:150},
  balanced:{overview:39,hold:57,summary:42,transition:13,maxZoom:1.8,minScene:180},
  explain:{overview:48,hold:75,summary:54,transition:17,maxZoom:1.9,minScene:210},
};
const identity:Pose = {zoom:1,x:0,y:0};
const clamp = (x:number,low:number,high:number)=>Math.max(low,Math.min(high,x));

export function carriesPicture(scene:Scene):boolean {
  const previous=scene.previous_scene;
  return !!(scene.props.scene_flow?.entry==='carry' && previous?.props.shot_plan && scene.props.shot_plan
    && ['object_showcase','material_collage'].includes(scene.component??'') && scene.component===previous.component
    && JSON.stringify(scene.props.images)===JSON.stringify(previous.props.images)
    && JSON.stringify(scene.props.focus_indices)===JSON.stringify(previous.props.focus_indices));
}

// Links exist only in the renderer. Beat references keep the carried pose correct
// after voice or speed changes without storing absolute camera timestamps.
export function linkScenes<T extends Scene>(scenes:T[]):T[] {
  const linked:T[]=[];
  scenes.forEach(scene=>linked.push({...scene,previous_scene:linked[linked.length-1]}));
  return linked;
}

export function transitionAt(scene:Scene,frame:number) {
  const previous=scene.previous_scene, flow=scene.props.scene_flow;
  const entry=carriesPicture(scene)?'carry':flow?.entry==='carry'?'dissolve':flow?.entry??'cut';
  const length=previous?Math.max(1,Math.min(PACING[flow?.profile??'balanced'].transition,
    Math.floor((scene.end_frame-scene.start_frame)/4),Math.floor((previous.end_frame-previous.start_frame)/4))):1;
  const t=clamp((frame-scene.start_frame)/length,0,1);
  return {entry,active:!!previous && entry==='dissolve' && t<1,progress:t*t*(3-2*t),length};
}

export function shotsFor(scene:Scene):Shot[] {
  const plan=scene.props.shot_plan, start=scene.start_frame, end=scene.end_frame;
  const carried=carriesPicture(scene)?shotsFor(scene.previous_scene!).slice(-1)[0].targets:undefined;
  const overview:Shot={kind:carried?'carry':'overview',frame:start,targets:carried??[]};
  if(!plan)return [overview];
  const pace=PACING[plan.profile];
  if(end-start<pace.minScene){
    // A short final part already has its context from the previous page. Keep
    // that view, then restore the overview for its closing sentence.
    return carried && plan.closing!=='hold' && end-start>=pace.hold+pace.summary+pace.transition
      ? [overview,{kind:'summary',frame:end-pace.summary,targets:[]}]:[overview];
  }
  const establish=carried?0:pace.overview;
  const beats=new Map(scene.beats.map(b=>[b.id,b.frame]));
  const summaryBeat=plan.summary_beat_id ? beats.get(plan.summary_beat_id) : undefined;
  const finish=plan.closing==='hold'?end:clamp(Math.min(end-pace.summary,summaryBeat??Infinity),start+establish,end-1);
  const candidates=plan.cues.map(c=>({...c,frame:beats.get(c.beat_id)}))
    .filter((c):c is typeof c & {frame:number}=>c.frame!==undefined)
    .sort((a,b)=>a.frame-b.frame);
  const focus:Shot[]=[];
  for(const cue of candidates){
    const at=Math.max(start+establish,cue.frame);
    if(at>finish-pace.hold)continue;
    const previous=focus[focus.length-1];
    if(!previous && carried?.join('|')===cue.targets.join('|'))continue;
    if(previous && previous.targets.join('|')===cue.targets.join('|'))continue;
    if(previous && at-previous.frame<pace.hold){
      // Dense speech gets a sustained view of the related objects, rather than
      // a series of short pans. Their native reveals still follow the voice.
      const union=Array.from(new Set([...previous.targets,...cue.targets]));
      if(union.length<=4)previous.targets=union;
      continue;
    }
    focus.push({kind:'focus',frame:at,targets:[...cue.targets]});
  }
  return [overview,...focus,...(plan.closing==='hold'?[]:[{kind:'summary' as const,frame:finish,targets:[]}])];
}

function poseFor(shot:Shot,rects:Record<string,Rect>,view:Rect,maxZoom:number,margin: {x:number;y:number}):Pose {
  const found=shot.targets.map(t=>rects[t]).filter(Boolean);
  if(!['focus','carry'].includes(shot.kind)||!found.length)return identity;
  const x=Math.min(...found.map(r=>r.x)), y=Math.min(...found.map(r=>r.y));
  const right=Math.max(...found.map(r=>r.x+r.width)), bottom=Math.max(...found.map(r=>r.y+r.height));
  const zoom=Math.max(1,Math.min(maxZoom,view.width/(right-x+margin.x),view.height/(bottom-y+margin.y)));
  return {zoom,
    x:clamp(view.x+view.width/2-(x+right)/2*zoom,(view.x+view.width)*(1-zoom),view.x*(1-zoom)),
    y:clamp(view.y+view.height/2-(y+bottom)/2*zoom,(view.y+view.height)*(1-zoom),view.y*(1-zoom))};
}

export function cameraAt(scene:Scene,frame:number,rects:Record<string,Rect>,view:Rect,margin={x:120,y:90}) {
  const shots=shotsFor(scene), pace=PACING[scene.props.shot_plan?.profile??'balanced'];
  let index=0;
  shots.forEach((shot,i)=>{if(shot.frame<=frame)index=i;});
  const shot=shots[index], target=poseFor(shot,rects,view,pace.maxZoom,margin);
  const previous=poseFor(shots[Math.max(0,index-1)],rects,view,pace.maxZoom,margin);
  const t=clamp((frame-shot.frame)/pace.transition,0,1), ease=t*t*(3-2*t);
  const pose={zoom:previous.zoom+(target.zoom-previous.zoom)*ease,
    x:previous.x+(target.x-previous.x)*ease,y:previous.y+(target.y-previous.y)*ease};
  return {shot,pose,transform:`matrix(${pose.zoom},0,0,${pose.zoom},${pose.x},${pose.y})`};
}

export function materialAt(scene:Scene & {component?:string;props:any},frame:number) {
  const shots=shotsFor(scene), pair=scene.component==='material_collage' && scene.props.images.length===2;
  let index=0;
  shots.forEach((shot,i)=>{if(shot.frame<=frame)index=i;});
  const selection=(until:number)=>{
    let chosen=[scene.props.focus_indices[0]];
    for(const shot of shots.slice(0,until+1)){
      const images=Array.from(new Set(shot.targets.filter(t=>/^(image|region):/.test(t)).map(t=>Number(t.split(':')[1]))));
      if(images.length)chosen=images.slice(-2);
    }
    return pair ? [0,1] : chosen;
  };
  const images=selection(index), previous=selection(Math.max(0,index-1));
  const profile=(scene.props.shot_plan?.profile??'balanced') as keyof typeof PACING;
  const transition=PACING[profile].transition;
  const t=clamp((frame-shots[index].frame)/transition,0,1);
  const ease=t*t*(3-2*t),blend=images.join('|')===previous.join('|')?1:ease;
  const weight=(shot:Shot,image:number)=>{
    const selected=Array.from(new Set(shot.targets.filter(target=>/^(image|region):/.test(target)).map(target=>Number(target.split(':')[1]))));
    return images.length===2 && ['focus','carry'].includes(shot.kind) && selected.length===1 && images.includes(selected[0])
      ? (selected[0]===image ? .65 : .35) : 1/images.length;
  };
  const weights=images.map(image=>weight(shots[Math.max(0,index-1)],image)*(1-ease)+weight(shots[index],image)*ease);
  return {shot:shots[index],images,previous,blend,weights};
}

export function flowRects(props:any):Record<string,Rect> {
  return Object.fromEntries(props.layout.nodes.map((n:any)=>[`node:${n.id}`,
    {x:n.x,y:n.y,width:n.width,height:n.height}]));
}

export function CameraAxis({sceneId,svg,zoom}:{sceneId:string;svg:string;zoom:number}) {
  // At close range the fixed units and metric cards give context. Fade ticks
  // before the camera would crop them halfway through a number.
  const opacity=clamp(1-(zoom-1)/.12,0,1);
  return <div data-shot-axis={sceneId} style={{position:'absolute',inset:0}}>
    <style>{`[data-shot-axis="${sceneId}"] text {opacity:${opacity}}`}</style>
    <div style={{position:'absolute',inset:0}} dangerouslySetInnerHTML={{__html:svg}}/>
  </div>;
}

export function ShotLayer({scene,frame,rects,view,width,height,children}:
  {scene:Scene;frame:number;rects:Record<string,Rect>;view:Rect;width:number;height:number;children:React.ReactNode}) {
  const camera=cameraAt(scene,frame,rects,view);
  if(!scene.props.shot_plan)return <div style={{position:'absolute',inset:0}}>{children}</div>;
  return <div style={{position:'absolute',left:view.x,top:view.y,width:view.width,height:view.height,overflow:'hidden'}}>
    <div style={{position:'absolute',left:-view.x,top:-view.y,width,height,transformOrigin:'0 0',transform:camera.transform}}>{children}</div>
  </div>;
}

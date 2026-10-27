import React from 'react';

export type Story = {version:1;role:'question'|'explain'|'compare'|'result'|'bridge'|'recap';
  importance:'key'|'normal'|'support';density:'light'|'normal'|'dense';
  entities:{id:string;name:string;color:string;targets:string[]}[];recap?:string[]};
type Scene = {props:{story?:Story}};

export function entityFor(scene:Scene,target:string) {
  const entities=scene.props.story?.entities??[];
  return entities.find(e=>e.targets.includes(target))
    ?? (target.startsWith('region:')?entities.find(e=>e.targets.includes(`image:${target.split(':')[1]}`)):undefined);
}

export const objectColor=(scene:Scene,target:string,fallback='#17784B')=>entityFor(scene,target)?.color??fallback;

export function revealFrames(scene:Scene) {
  const story=scene.props.story;
  if(!story)return 16;
  return story.role==='bridge'?10:story.role==='compare'||story.density==='dense'?22:story.importance==='key'?19:16;
}

export function StoryObjects({scene,inline=false}:{scene:Scene;inline?:boolean}) {
  const entities=scene.props.story?.entities.slice(0,3)??[];
  if(!entities.length)return null;
  return <div style={{position:inline?'relative':'absolute',left:inline?undefined:140,top:inline?undefined:116,height:34,display:'flex',gap:16,alignItems:'center',overflow:inline?'hidden':undefined}}>
    {entities.map(e=><div key={e.id} style={{display:'flex',alignItems:'center',gap:10,padding:'3px 14px',
      borderRadius:8,background:`${e.color}0D`,color:e.color,fontSize:23,fontWeight:600,lineHeight:1.25,maxWidth:480}}>
      <span style={{width:8,height:8,borderRadius:4,background:e.color,flex:'none'}}/>
      <span style={{overflow:'hidden',whiteSpace:'nowrap',textOverflow:'ellipsis'}}>{e.name}</span>
    </div>)}
  </div>;
}

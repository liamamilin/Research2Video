import React from 'react';
import {Img,staticFile,interpolate} from 'remotion';
import {cameraAt,carriesPicture,materialAt,pacingFor,type Rect} from './ShotDirector';
import {objectColor} from './StoryDirection';

const green='#17784B', ink='#202D28', muted='#69756E';
const clamp=(value:number)=>Math.max(0,Math.min(1,value));
const picture=(image:any)=> <Img src={staticFile(image.image_path)} style={{width:'100%',height:'100%',objectFit:'contain'}}/>;

export function imageRects(image:any,index:number,width:number,height:number) {
  const focus=image.focus;
  const fit=focus ? Math.min(width/focus.width,height/focus.height) : 1;
  const w=focus ? focus.width*fit : width,h=focus ? focus.height*fit : height;
  const x=(width-w)/2,y=(height-h)/2;
  const rects:Record<string,Rect>={[`image:${index}`]:{x,y,width:w,height:h}};
  focus?.regions.forEach((r:any,j:number)=>{rects[`region:${index}:${j}`]={
    x:x+r.x1*w/1000,y:y+r.y1*h/1000,width:(r.x2-r.x1)*w/1000,height:(r.y2-r.y1)*h/1000};});
  return {rects,x,y,w,h};
}

function MaterialPage({scene,image,index,frame,width,height,layer}:
  {scene:any;image:any;index:number;frame:number;width:number;height:number;layer:string}) {
  const layout=imageRects(image,index,width,height);
  const camera=cameraAt(scene,frame,layout.rects,{x:0,y:0,width,height},{x:width*.07,y:height*.07});
  const regions=(image.focus?.regions||[]).filter((_:any,j:number)=>camera.shot.targets.includes(`region:${index}:${j}`));
  const pace=pacingFor(scene);
  const draw=camera.shot.kind==='carry'?1:clamp((frame-camera.shot.frame-pace.transition*.7)/18);
  const mask=`${scene.id}_${index}_${layer}_shade`;
  return <div style={{position:'relative',width,height,overflow:'hidden',background:'white',borderRadius:12}}>
    <div style={{position:'absolute',inset:0,transformOrigin:'0 0',transform:camera.transform}}>
      <div style={{position:'absolute',left:layout.x,top:layout.y,width:layout.w,height:layout.h}}>
        {picture(image)}
        {!!regions.length&&<svg viewBox="0 0 1000 1000" preserveAspectRatio="none" style={{position:'absolute',inset:0,width:'100%',height:'100%'}}>
          <defs><mask id={mask}><rect width={1000} height={1000} fill="white"/>{regions.map((r:any,j:number)=><rect key={j} x={r.x1} y={r.y1} width={r.x2-r.x1} height={r.y2-r.y1} fill="black"/>)}</mask></defs>
          <rect width={1000} height={1000} fill="#132A23" mask={`url(#${mask})`} opacity={.13*draw}/>
          {regions.map((r:any,j:number)=>{
            const x=r.x1,y=r.y1,w=r.x2-x,h=r.y2-y;
            const regionIndex=image.focus.regions.indexOf(r);
            const stroke={fill:'none',stroke:objectColor(scene,`region:${index}:${regionIndex}`,'#E48B48'),strokeWidth:4,vectorEffect:'non-scaling-stroke' as const,
              pathLength:1,strokeDasharray:1,strokeDashoffset:1-draw,strokeLinecap:'round' as const};
            return r.style==='circle'?<ellipse key={j} {...stroke} cx={x+w/2} cy={y+h/2} rx={w/2} ry={h/2}/>
              :r.style==='underline'?<path key={j} {...stroke} d={`M${x} ${y+h}H${x+w}`}/>
                :r.style==='highlight'?<rect key={j} x={x} y={y} width={w*draw} height={h} fill={stroke.stroke} opacity={.25}/>
                  :<rect key={j} {...stroke} x={x} y={y} width={w} height={h} rx={8}/>;
          })}
        </svg>}
      </div>
    </div>
  </div>;
}

export function DirectedMaterials({scene,frame}:{scene:any;frame:number}) {
  const p=scene.props,state=materialAt(scene,frame);
  const active=state.shot.targets.map(target=>{
    const [kind,i,j]=target.split(':');
    return kind==='region'?p.images[Number(i)].focus.regions[Number(j)].label:p.images[Number(i)].caption;
  }).filter(Boolean);
  const label=state.shot.kind==='overview'?'先看整体':state.shot.kind==='summary'?'回到整体':active.length?active.join(' · '):'当前画面';
  const enter=carriesPicture(scene)?1:interpolate(frame-scene.start_frame,[0,14],[0,1],{extrapolateLeft:'clamp',extrapolateRight:'clamp'});
  const render=(indices:number[],at:number,layer:string,opacity:number)=>{
    const pair=indices.length>1,height=pair?450:530;
    const weights=indices.join('|')===state.images.join('|')?state.weights:indices.map(()=>1/indices.length);
    return <div style={{position:'absolute',inset:0,display:'flex',gap:20,alignItems:'center',opacity}}>
      {indices.map((i,slot)=>{
        const outer=pair?1080*weights[slot]:1100,width=outer-22;
        const image=p.images[i],focused=state.shot.targets.some(t=>Number(t.split(':')[1])===i);
        return <div key={i} style={{width:outer,flex:'none',padding:10,border:`1px solid ${focused?objectColor(scene,`image:${i}`,green):'#E1E8E1'}`,borderRadius:18,background:'white',boxSizing:'border-box'}}>
          <MaterialPage scene={scene} image={image} index={i} frame={at} width={width} height={height} layer={layer}/>
          {pair&&<div style={{fontSize:24,color:muted,lineHeight:1.35,margin:'12px 8px 3px',height:65,overflow:'hidden'}}>{image.caption||`画面 ${i+1}`}</div>}
        </div>;
      })}
    </div>;
  };
  return <div style={{position:'absolute',left:140,top:300,width:1640,height:570,display:'grid',gridTemplateColumns:'1100px 1fr',gap:45,opacity:enter}}>
    <div style={{position:'relative',height:570}}>
      {state.blend<1&&render(state.previous,state.shot.frame-1,'previous',1-state.blend)}
      {render(state.images,frame,'current',state.blend)}
    </div>
    <div style={{display:'flex',flexDirection:'column',justifyContent:'center',gap:24,minWidth:0}}>
      <div style={{fontSize:24,color:green}}>{state.images.length>1?'并列对照':'页面与对象'}</div>
      <div style={{fontSize:active.join('').length>34?28:34,color:ink,lineHeight:1.4,fontWeight:700,overflowWrap:'anywhere'}}>{label}</div>
      <div style={{width:68,height:4,background:green,borderRadius:2}}/>
      <div style={{fontSize:p.text.length>60?30:34,color:muted,lineHeight:1.55,overflowWrap:'anywhere'}}>{p.text}</div>
      {p.images.length>2&&<div style={{display:'flex',gap:10,height:78}}>{p.images.slice(0,6).map((image:any,i:number)=><div key={i} style={{flex:1,minWidth:0,padding:3,background:'white',border:`1px solid ${state.images.includes(i)?green:'#E1E8E1'}`,opacity:state.images.includes(i)?1:.55}}>{picture(image)}</div>)}</div>}
    </div>
  </div>;
}

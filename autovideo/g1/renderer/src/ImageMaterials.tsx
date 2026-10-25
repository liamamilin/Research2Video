import React from 'react';
import {Img, interpolate, staticFile} from 'remotion';

const clamp = {extrapolateLeft:'clamp', extrapolateRight:'clamp'} as const;
const roles: Record<string,string> = {overall:'整体',detail:'细节',comparison:'对比',reference:'参考图片'};
const picture = (item: any, style: React.CSSProperties = {}) => <Img src={staticFile(item.image_path)} style={{width:'100%',height:'100%',objectFit:'contain',...style}}/>;

const unit = (value:number) => Math.max(0,Math.min(1,value));
const smooth = (value:number) => {const t=unit(value);return t*t*(3-2*t);};

/** A deterministic camera over image coordinates, shared by page tours and comparisons. */
export function PageFocus({item,width,height,progress,durationFrames}: {item:any;width:number;height:number;progress:number;durationFrames:number}) {
  const focus=item.focus;
  if (!focus?.regions?.length) return picture(item);
  // Give each target at least three seconds. Short narration uses its first target.
  const regions=focus.regions.slice(0,Math.max(1,Math.floor(durationFrames/90)));
  const time=Math.min(regions.length-.00001,unit(progress)*regions.length), index=Math.floor(time), local=time-index;
  const target=regions[index], previous=index?regions[index-1]:null;
  const fit=Math.min(width/focus.width,height/focus.height), dw=focus.width*fit, dh=focus.height*fit;
  const left=(width-dw)/2, top=(height-dh)/2;
  const camera=(region:any) => {
    if (!region) return {scale:1,x:0,y:0};
    const rw=(region.x2-region.x1)*dw/1000,rh=(region.y2-region.y1)*dh/1000;
    const scale=Math.max(1,Math.min(2,(width-60)/rw,(height-60)/rh));
    return {scale,x:(width/2-left-(region.x1+region.x2)*dw/2000)*scale,
      y:(height/2-top-(region.y1+region.y2)*dh/2000)*scale};
  };
  const before=camera(previous), after=camera(target);
  const move=smooth(index?local/.3:(local-.2)/.35);
  const scale=before.scale+(after.scale-before.scale)*move;
  const tx=before.x+(after.x-before.x)*move,ty=before.y+(after.y-before.y)*move;
  const draw=unit((local-.52)/.25), note=unit((local-.72)/.15);
  const x=target.x1,y=target.y1,w=target.x2-x,h=target.y2-y;
  const stroke={fill:'none',stroke:'#E77B36',strokeWidth:4,vectorEffect:'non-scaling-stroke' as const,
    pathLength:1,strokeDasharray:1,strokeDashoffset:1-draw,strokeLinecap:'round' as const};
  const shade=`M0 0H1000V1000H0Z M${x} ${y}V${y+h}H${x+w}V${y}Z`;
  return <div style={{position:'relative',width,height,overflow:'hidden',background:'#fff',borderRadius:12}}>
    <div style={{position:'absolute',inset:0,transform:`translate(${tx}px,${ty}px) scale(${scale})`,transformOrigin:'50% 50%'}}>
      <div style={{position:'absolute',left,top,width:dw,height:dh}}>
        {picture(item)}
        <svg viewBox="0 0 1000 1000" preserveAspectRatio="none" style={{position:'absolute',inset:0,width:'100%',height:'100%',overflow:'visible'}}>
          <path d={shade} fill="#132A23" fillRule="evenodd" opacity={.12*draw}/>
          {target.style==='circle'?<ellipse {...stroke} cx={x+w/2} cy={y+h/2} rx={w/2} ry={h/2}/>
            :target.style==='underline'?<path {...stroke} d={`M${x} ${y+h}H${x+w}`}/>
              :target.style==='highlight'?<rect x={x} y={y} width={w*draw} height={h} fill="#F5C544" opacity={.22}/>
                :<rect {...stroke} x={x} y={y} width={w} height={h} rx={8}/>}
        </svg>
      </div>
    </div>
    {target.label&&<div style={{position:'absolute',left:14,right:14,bottom:12,background:'#FAFCF5F0',borderLeft:'4px solid #E77B36',padding:'8px 12px',fontSize:18,lineHeight:1.35,color:'#213D39',opacity:note}}>{regions.length>1?`${index+1}/${regions.length} · `:''}{target.label}</div>}
  </div>;
}

export function ImageMaterials({props:p, mode, frame, start, progress,durationFrames}: {props:any;mode:string;frame:number;start:number;progress:number;durationFrames:number}) {
  const position = Math.min(p.focus_indices.length-1,Math.floor(progress*p.focus_indices.length));
  const selected = p.focus_indices[position];
  const hero = p.images[selected];
  const imageProgress=Math.min(.99999,progress*p.focus_indices.length-position);
  const imageDuration=durationFrames/p.focus_indices.length;
  const enter = interpolate(frame-start,[0,14],[0,1],clamp);
  if (mode === 'object_showcase') {
    const from = Math.max(0,Math.min(p.images.length-4,selected-1));
    return <div style={{position:'absolute',left:100,right:100,top:220,bottom:140,display:'grid',gridTemplateColumns:'1060px 1fr',gap:55,opacity:enter}}>
      <div style={{display:'flex',flexDirection:'column',gap:16}}>
        <div style={{height:495,background:'#fff',border:'1px solid #DCE3D9',borderRadius:28,padding:18,boxShadow:'0 14px 35px #29413b0c'}}><PageFocus item={hero} width={1022} height={457} progress={imageProgress} durationFrames={imageDuration}/></div>
        <div style={{display:'flex',gap:14,height:135}}>{p.images.slice(from,from+4).map((item:any,i:number)=><div key={item.image_asset_id} style={{flex:1,minWidth:0,padding:6,border:`2px solid ${from+i===selected?'#237968':'#E0E5DC'}`,borderRadius:14,background:'#fff',opacity:from+i===selected?1:.6}}>{picture(item)}</div>)}</div>
      </div>
      <div style={{display:'flex',flexDirection:'column',justifyContent:'center',gap:24,minWidth:0}}>
        <div style={{fontSize:23,color:'#237968',letterSpacing:3}}>对象观察 / {roles[hero.role]}</div>
        {hero.caption && <div style={{fontSize:hero.caption.length>32?29:37,lineHeight:1.45,fontWeight:800,overflowWrap:'anywhere'}}>{hero.caption}</div>}
        <div style={{width:65,height:4,background:'#237968'}}/>
        <div style={{fontSize:p.text.length>60?34:42,lineHeight:1.65,fontWeight:600,overflowWrap:'anywhere'}}>{p.text}</div>
        <div style={{fontSize:21,color:'#73847B'}}>图片 {selected+1} / {p.images.length} · 说明来自提交素材</div>
      </div>
    </div>;
  }
  const from = Math.max(0,Math.min(p.images.length-3,selected-1));
  const group = p.images.slice(from,from+3);
  if(group.length===2) return <div style={{position:'absolute',left:100,right:100,top:220,bottom:140,display:'grid',gridTemplateColumns:'1060px 1fr',gap:55,opacity:enter}}>
    <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:20,alignItems:'center'}}>{group.map((item:any,i:number)=><div key={item.image_asset_id} style={{padding:14,background:'#fff',border:`2px solid ${from+i===selected?'#237968':'#DCE3D9'}`,borderRadius:18}}>
      <PageFocus item={item} width={484} height={500} progress={progress} durationFrames={durationFrames}/>
      <div style={{fontSize:20,lineHeight:1.4,marginTop:12,color:'#526A60'}}>{item.caption||roles[item.role]}</div>
    </div>)}</div>
    <div style={{display:'flex',flexDirection:'column',justifyContent:'center',gap:24}}><div style={{fontSize:23,color:'#237968'}}>并列对照 / 本段要点</div><div style={{fontSize:p.text.length>60?35:43,lineHeight:1.65,fontWeight:600}}>{p.text}</div></div>
  </div>;
  return <div style={{position:'absolute',left:100,right:100,top:220,bottom:135,display:'grid',gridTemplateColumns:'1060px 1fr',gap:55}}>
    <div style={{position:'relative',height:650}}>{group.map((item:any,i:number)=>{
      const reveal=interpolate(frame-start-i*5,[0,14],[0,1],clamp);
      const count=group.length;
      const width=count===1?960:count===2?550:440;
      const height=count===1?580:count===2?500:420;
      const left=count===1?35:count===2?i*470:[0,570,290][i];
      const top=count===1?20:count===2?i*90:[0,30,205][i];
      const longCaption=item.caption.length>36;
      return <div key={item.image_asset_id} style={{position:'absolute',zIndex:from+i===selected?5:i+1,width,height,left,top,padding:16,paddingBottom:longCaption?110:65,background:'#fff',border:'1px solid #DDE1D7',boxShadow:'0 12px 28px #243d3920',transform:`translateY(${(1-reveal)*20}px) rotate(${count===1?0:[-3,3,-1][i]}deg)`,opacity:reveal}}>
        {picture(item)}<div style={{position:'absolute',bottom:15,left:18,right:18,fontSize:longCaption?16:19,lineHeight:1.35,maxHeight:longCaption?100:65,color:'#526A60',overflowWrap:'anywhere'}}>{String(from+i+1).padStart(2,'0')} / {item.caption || roles[item.role]}</div>
      </div>;
    })}</div>
    <div style={{display:'flex',flexDirection:'column',justifyContent:'center',gap:30,opacity:enter}}>
      <div style={{fontSize:23,color:'#237968',letterSpacing:3}}>材料拼贴 / 本段要点</div>
      <div style={{padding:'30px 26px',borderLeft:'5px solid #237968',background:'#FFFFFFD9',fontSize:p.text.length>60?35:43,lineHeight:1.65,fontWeight:600,overflowWrap:'anywhere'}}>{p.text}</div>
      <div style={{fontSize:21,color:'#73847B'}}>图片说明来自提交素材</div>
    </div>
  </div>;
}

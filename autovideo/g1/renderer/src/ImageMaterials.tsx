import React from 'react';
import {Img, interpolate, staticFile} from 'remotion';

const clamp = {extrapolateLeft:'clamp', extrapolateRight:'clamp'} as const;
const roles: Record<string,string> = {overall:'整体',detail:'细节',comparison:'对比',reference:'参考图片'};
const picture = (item: any, style: React.CSSProperties = {}) => <Img src={staticFile(item.image_path)} style={{width:'100%',height:'100%',objectFit:'contain',...style}}/>;

export function ImageMaterials({props:p, mode, frame, start, progress}: {props:any;mode:string;frame:number;start:number;progress:number}) {
  const position = Math.min(p.focus_indices.length-1,Math.floor(progress*p.focus_indices.length));
  const selected = p.focus_indices[position];
  const hero = p.images[selected];
  const enter = interpolate(frame-start,[0,14],[0,1],clamp);
  if (mode === 'object_showcase') {
    const from = Math.max(0,Math.min(p.images.length-4,selected-1));
    return <div style={{position:'absolute',left:100,right:100,top:220,bottom:140,display:'grid',gridTemplateColumns:'1060px 1fr',gap:55,opacity:enter}}>
      <div style={{display:'flex',flexDirection:'column',gap:16}}>
        <div style={{height:495,background:'#fff',border:'1px solid #DCE3D9',borderRadius:28,padding:18,boxShadow:'0 14px 35px #29413b0c'}}>{picture(hero)}</div>
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
      <div style={{fontSize:21,color:'#73847B'}}>图文按提交顺序编排</div>
    </div>
  </div>;
}

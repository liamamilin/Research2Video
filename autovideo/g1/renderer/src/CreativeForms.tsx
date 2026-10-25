import React from 'react';
import {AbsoluteFill, Audio, Img, interpolate, staticFile, useCurrentFrame} from 'remotion';
import {CornerBadge, type Badge} from './Packaging';
import {ImageMaterials} from './ImageMaterials';

export type CreativePreset = 'information_cards' | 'kinetic_typography' | 'image_slideshow' | 'chat_dialogue' | 'object_showcase' | 'dynamic_collage';
const ink = '#213D39', green = '#237968', cream = '#F5F2E9', muted = '#73847B';
const font = '"Hiragino Sans GB", "PingFang SC", sans-serif';
const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;

function cardTextSize(text: string, width: number, height: number, maximum: number): number {
  const canvas = typeof document === 'undefined' ? null : document.createElement('canvas');
  const measure = canvas?.getContext('2d');
  if (!measure) return 24;
  const units = text.match(/\n|\s+|[A-Za-z0-9_.:/-]+|./gu) || [];
  for (let size = maximum; size >= 24; size -= 2) {
    measure.font = `${size}px ${font}`;
    let line = '', count = 1;
    for (const unit of units) {
      for (const piece of measure.measureText(unit).width <= width ? [unit] : Array.from(unit)) {
        if (piece === '\n' || (line && measure.measureText(line + piece).width > width)) {
          count += 1;
          line = piece === '\n' ? '' : piece;
        } else line += piece;
      }
    }
    if (count * size * 1.4 <= height) return size;
  }
  return 24;
}

function InformationCard({ir, scene, frame, badge}: {ir: any; scene: any; frame: number; badge?: Badge}) {
  const p = scene.props;
  const points = p.points || [{text: p.text}];
  const columns = points.length > 12 ? 3 : p.layout === 'comparison' || points.length > 3 ? 2 : 1;
  const rows = Math.ceil(points.length / columns);
  const panelHeight = (451 - 26 * (rows - 1)) / rows;
  const panelWidth = (1680 - 26 * (columns - 1)) / columns;
  const focus = p.layout === 'focus' && points.length === 1;
  const sizes = React.useMemo(() => points.map((point: any) => cardTextSize(point.text, panelWidth-64,
    panelHeight - (focus ? 80 : 36) - (point.label ? 60 : 0), focus ? 100 : columns === 2 ? 62 : 50)),
  [points, panelWidth, panelHeight, focus, columns]);
  return <AbsoluteFill style={{background:'#f3f5f1', color:ink, fontFamily:font}}>
    <div style={{position:'absolute',left:80,top:45,right:330,fontSize:28,color:muted}}>{ir.video.title}</div>
    <div style={{position:'absolute',left:80,top:145,right:200,fontSize:44,lineHeight:1.3}}>{p.title}</div>
    <div style={{position:'absolute',left:80,top:275,width:1760,height:531,background:'#ffffff',borderRadius:30}}/>
    <div style={{position:'absolute',left:120,top:315,width:1680,height:451,display:'grid',gridTemplateColumns:`repeat(${columns},minmax(0,1fr))`,gridTemplateRows:`repeat(${rows},minmax(0,1fr))`,gap:26}}>
      {points.map((point: any, i: number) => {
        const segment = ir.narration.segments.find((s: any) => s.id === point.segment_id);
        const at = segment ? segment.start_ms * .03 : scene.start_frame;
        const reveal = interpolate(frame-at,[0,10],[0,1],clamp);
        return <div key={i} style={{boxSizing:'border-box',minHeight:0,borderRadius:22,padding:focus ? '40px 32px' : '18px 32px',background:p.layout === 'comparison' ? i === 0 ? '#eaf2ed' : '#f2f4f0' : 'transparent',color:frame >= at ? ink : '#c7d2ca',transform:`translateY(${(1-reveal)*8}px)`,display:'flex',flexDirection:'column',justifyContent:focus ? 'center' : 'flex-start'}}>
          {point.label && <div style={{fontSize:30,color:frame >= at ? green : '#c7d2ca',marginBottom:18}}>{point.label}</div>}
          <div style={{fontSize:sizes[i],lineHeight:1.4,overflowWrap:'anywhere',whiteSpace:'pre-wrap'}}>{point.text}</div>
        </div>;
      })}
    </div>
    {badge?.text && <div style={{position:'absolute',right:80,top:46,fontSize:27,color:badge.color}}>{badge.text}</div>}
    <div style={{position:'absolute',bottom:0,left:0,height:5,width:`${100*(frame+1)/ir.total_frames}%`,background:green}}/>
    <Audio src={staticFile(ir.audio_public_file)}/>
  </AbsoluteFill>;
}

export function CreativeVideo({ir, badge, showCaptions = false}: {ir: any; badge?: Badge; showCaptions?: boolean}) {
  const frame = useCurrentFrame();
  const scene = ir.scenes.find((s: any) => frame >= s.start_frame && frame < s.end_frame) || ir.scenes[ir.scenes.length - 1];
  if (!scene) return <AbsoluteFill style={{background: cream}}/>;
  const p = scene.props;
  const caption = showCaptions && (ir.caption_cues ?? ir.narration.segments).find((s: any) => frame >= Math.round(s.start_ms*.03) && frame < Math.round(s.end_ms*.03));
  if (ir.presentation.preset === 'information_cards') return <InformationCard ir={ir} scene={scene} frame={frame} badge={badge}/>;
  const index = ir.scenes.indexOf(scene);
  const phrase = ir.narration.segments.find((s: any) => s.id === scene.narration_segment_ids[0]);
  const start = phrase.start_ms * .03;
  const lastPhrase = ir.narration.segments.find((s: any) => s.id === scene.narration_segment_ids[scene.narration_segment_ids.length-1]);
  const end = lastPhrase.end_ms * .03;
  const enter = interpolate(frame - scene.start_frame, [0, 12], [0, 1], clamp);
  const progress = interpolate(frame, [start, end], [0, 1], clamp);
  const mode: CreativePreset = scene.component === 'kinetic_text' ? 'kinetic_typography' : ir.presentation.preset;
  // Break by measured length, preserving every codepoint; technical tokens can wrap inside the fixed stage.
  const characters = Array.from(p.text as string);
  const chunks = Array.from({length: Math.ceil(characters.length / 16)}, (_, i) => characters.slice(i * 16, (i + 1) * 16).join(''));
  const imageIndex = mode === 'image_slideshow' ? Math.min(p.images.length - 1, Math.floor(progress * p.images.length)) : 0;
  const speakerPrefix = `${p.speaker}：`;
  const spokenText = mode === 'chat_dialogue' && p.text.startsWith(speakerPrefix) ? p.text.slice(speakerPrefix.length).trimStart() : mode === 'chat_dialogue' && p.text.startsWith(`${p.speaker}:`) ? p.text.slice(p.speaker.length + 1).trimStart() : p.text;
  const footer = <div style={{position:'absolute',left:100,right:100,bottom:50,display:'flex',justifyContent:'space-between',fontSize:24,color:muted}}><span>{mode === 'chat_dialogue' ? '演绎对话 · 统一旁白' : mode === 'image_slideshow' ? '图文讲解 · 图片为辅助表达' : '听见重点，看见内容'}</span><span>{String(index + 1).padStart(2,'0')} / {String(ir.scenes.length).padStart(2,'0')}</span></div>;
  return <AbsoluteFill style={{background:cream,color:ink,fontFamily:font,overflow:'hidden'}}>
    <div style={{position:'absolute',top:0,right:0,width:650,height:1080,background:'#E9EEE4',borderRadius:'55% 0 0 45%'}}/>
    <div style={{position:'absolute',left:100,top:125,right:100,display:'flex',justifyContent:'space-between',alignItems:'center'}}><div style={{fontSize:27,letterSpacing:2,maxWidth:1430,overflowWrap:'anywhere'}}>{p.title}</div></div><CornerBadge badge={badge}/>
    {mode === 'kinetic_typography' && <div style={{position:'absolute',left:110,right:110,top:230,bottom:150,display:'flex',gap:70,alignItems:'center'}}>
      <div style={{alignSelf:'flex-start',fontSize:110,fontWeight:800,color:green,opacity:.23,width:170,flexShrink:0}}>{String(index + 1).padStart(2,'0')}<div style={{height:5,background:green,marginTop:24,width:105}}/></div>
      <div style={{flex:1,display:'flex',flexDirection:'column',gap:16}}>{chunks.map((text: string, i: number) => {
        const at = start + (end - start) * i / chunks.length;
        const reveal = interpolate(frame - at, [0, 10], [0, 1], clamp);
        return <div key={i} style={{fontSize:characters.length > 60 ? 69 : 82,lineHeight:1.45,fontWeight:i === 0 ? 800 : 600,color:frame >= at ? ink : '#9CAAA2',transform:`translateY(${(1-reveal)*12}px)`,opacity:.38 + .62*reveal,overflowWrap:'anywhere'}}>{text}</div>;
      })}</div>
    </div>}
    {mode === 'image_slideshow' && <div style={{position:'absolute',left:100,right:100,top:230,bottom:145,display:'grid',gridTemplateColumns:'1120px 1fr',gap:50,alignItems:'center',opacity:enter,transform:`translateY(${(1-enter)*16}px)`}}>
      <div style={{height:665,borderRadius:30,background:'#E0E4DC',overflow:'hidden',display:'flex',alignItems:'center',justifyContent:'center',boxShadow:'0 18px 45px #29413b15'}}><Img src={staticFile(p.images[imageIndex].image_path)} style={{width:'100%',height:'100%',objectFit:'contain',transform:`scale(${1 + .025*progress})`}}/></div>
      <div><div style={{fontSize:24,letterSpacing:4,color:green,marginBottom:32}}>图文讲解</div><div style={{fontSize:p.text.length > 60 ? 40 : 47,lineHeight:1.65,fontWeight:600,overflowWrap:'anywhere'}}>{p.text}</div><div style={{height:4,marginTop:36,width:90,background:green}}/></div>
    </div>}
    {mode === 'chat_dialogue' && <div style={{position:'absolute',left:190,right:190,top:230,bottom:145,display:'flex',flexDirection:'column',justifyContent:'center',gap:36}}>
      <div style={{alignSelf:'flex-start',maxWidth:1330}}><div style={{fontSize:24,color:muted,marginBottom:12}}>观众 · 本章问题</div><div style={{padding:'24px 36px',background:'#FFFFFF',border:'1px solid #DFE5DC',borderRadius:'8px 30px 30px 30px',fontSize:p.question.length > 90 ? 29 : 36,lineHeight:1.5,overflowWrap:'anywhere'}}>{p.question}</div></div>
      {p.side !== 'left' && <div style={{alignSelf:p.side === 'left' ? 'flex-start' : 'flex-end',maxWidth:1350,opacity:enter,transform:`translateY(${(1-enter)*20}px)`}}><div style={{fontSize:24,color:muted,marginBottom:12,textAlign:p.side === 'left' ? 'left' : 'right'}}>{p.speaker}</div><div style={{padding:'34px 44px',background:green,color:'#FFFFFF',borderRadius:p.side === 'left' ? '8px 30px 30px 30px' : '30px 8px 30px 30px',fontSize:p.text.length > 60 ? 44 : 52,lineHeight:1.65,overflowWrap:'anywhere',boxShadow:'0 12px 30px #23796816'}}>{spokenText}</div></div>}
    </div>}
    {['object_showcase','dynamic_collage'].includes(mode) && <ImageMaterials props={p} mode={mode} frame={frame} start={scene.start_frame} progress={progress} durationFrames={scene.end_frame-scene.start_frame}/>}
    {!showCaptions && footer}
    {caption && <div style={{position:'absolute',left:100,right:100,bottom:60,display:'flex',justifyContent:'center'}}><div style={{padding:'14px 28px',background:'#29392F',color:'#FFFFFF',borderRadius:14,fontSize:32,lineHeight:1.45,textAlign:'center'}}>{caption.text}</div></div>}<div style={{position:'absolute',bottom:0,left:0,height:5,width:`${100*(frame+1)/ir.total_frames}%`,background:green}}/>
    <Audio src={staticFile(ir.audio_public_file)}/>
  </AbsoluteFill>;
}

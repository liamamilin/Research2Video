import React from 'react';
import {interpolate} from 'remotion';
import {ContentStyleContext} from './ContentStyle';
import {visualStart} from './ShotDirector';

// The original code is display data. Syntax colors and focus are frame-driven;
// no evaluator, editor runtime or highlighting package is needed.
type Line = {text:string;phrase?:string;annotation?:string;change?:'keep'|'add'|'remove'};
const clamp = {extrapolateLeft:'clamp',extrapolateRight:'clamp'} as const;
const columns = (text:string) => Array.from(text.replace(/\t/g,'  ')).reduce((n,c)=>n+(c.charCodeAt(0)>255?2:1),0);
export function codeFontSize(lines:Line[]):number {
  const widths=lines.map(line=>columns(line.text));
  const fitting=[72,64,56,48,42,38,34,30,28].filter(size=>{
    const capacity=1400/(size*.62);
    const height=widths.reduce((sum,width)=>sum+Math.max(1,Math.ceil(width/capacity))*size*1.45+24,0)-14;
    return height<=400;
  });
  // Keep short commands and their output arguments on one line while the
  // type is still large. Long code can wrap rather than shrink excessively.
  return fitting.find(size=>size>=48&&widths.every(width=>width<=1400/(size*.62)))??fitting[0]??28;
}
const syntax = (text:string) => text.split(/("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|#[^\n]*|--?[a-zA-Z][\w-]*|\b(?:import|from|return|def|class|const|let|await|async|if|else|true|false|None|True|False|print|export)\b|\b\d+(?:\.\d+)?\b)/g)
  .map((token,i)=><span key={i} style={{color:/^['"]/.test(token)?'#D9BC83':/^#/.test(token)?'#A2B7AC':/^--?/.test(token)?'#88C9E7':/^(?:\d|true$|false$|None$|True$|False$)/.test(token)?'#D8A8EC':/^(?:import|from|return|def|class|const|let|await|async|if|else|print|export)$/.test(token)?'#8DCCB5':undefined}}>{token}</span>);

export function CodeDemo({scene,frame}:{scene:any;frame:number}) {
  const style=React.useContext(ContentStyleContext);
  const accent=style?.accent??'#17784B',lines=scene.props.lines as Line[],size=codeFontSize(lines);
  const events=scene.beats.filter((beat:any)=>/^line:\d+$/.test(beat.target_id))
    .map((beat:any)=>({...beat,index:Number(beat.target_id.split(':')[1]),at:visualStart(scene,beat.frame,12)}))
    .sort((a:any,b:any)=>a.at-b.at);
  const current=events.filter((event:any)=>frame>=event.at).slice(-1)[0];
  const active=current?.index??-1,annotation=active>=0?lines[active].annotation:'';
  const entrance=interpolate(frame-scene.start_frame,[0,12],[0,1],clamp);
  return <div data-code-demo={scene.id} style={{position:'absolute',left:140,top:300,width:1640,height:560,opacity:entrance}}>
    <div style={{height:500,background:'#172B26',border:'1px solid #2F4D42',borderRadius:20,overflow:'hidden',boxShadow:'0 14px 30px rgba(20,40,30,.08)'}}>
      <div style={{height:58,display:'flex',alignItems:'center',gap:10,padding:'0 30px',borderBottom:'1px solid #365044',color:'#BCD0C4',fontSize:23}}>
        {['#C47B74','#D0B47C','#79AD91'].map(color=><span key={color} style={{width:12,height:12,borderRadius:'50%',background:color}}/>)}
        <span style={{marginLeft:20}}>{scene.props.label||(lines.some(line=>line.change&&line.change!=='keep')?'修改前后':'代码与命令')}</span>
      </div>
      <div style={{height:442,padding:'18px 22px',boxSizing:'border-box',display:'flex',flexDirection:'column',justifyContent:'flex-start',gap:14}}>
        {lines.map((line,i)=>{
          const event=events.find((item:any)=>item.index===i),selected=i===active;
          const weight=selected?interpolate(frame-event.at,[0,12],[0,1],clamp):0;
          const change=line.change??'keep',color=change==='add'?'#8CD5AF':change==='remove'?'#EBAFA8':'#E9F1EC';
          return <div key={i} style={{position:'relative',display:'flex',gap:20,padding:'5px 16px',borderRadius:8,background:selected?'#2C4B3E':change==='add'?'#244735':change==='remove'?'#49352F':'transparent',opacity:active<0||selected?1:.64}}>
            <div style={{position:'absolute',left:0,top:0,bottom:0,width:4,borderRadius:4,background:'#8CD5AF',opacity:weight}}/>
            <span style={{width:64,flex:'none',color:change==='keep'?'#A2B7AC':color,textAlign:'right',fontSize:Math.max(22,Math.round(size*.5)),lineHeight:`${size*1.45}px`,fontFamily:'Menlo,monospace'}}>{change==='add'?'+':change==='remove'?'−':String(i+1).padStart(2,'0')}</span>
            <pre style={{margin:0,minWidth:0,flex:1,fontFamily:'Menlo,"Hiragino Sans GB",monospace',fontSize:size,lineHeight:1.45,color,whiteSpace:'pre-wrap',overflowWrap:'anywhere',tabSize:2}}>{syntax(line.text)}</pre>
          </div>;
        })}
      </div>
    </div>
    {annotation&&<div style={{display:'flex',gap:16,alignItems:'center',height:60,color:style?.text??'#202D28',fontSize:annotation.length>45?25:29,lineHeight:1.3}}>
      <span style={{width:6,height:27,background:accent,borderRadius:3,flex:'none'}}/><span>{annotation}</span>
    </div>}
  </div>;
}

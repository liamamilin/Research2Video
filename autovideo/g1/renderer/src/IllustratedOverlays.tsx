import React from 'react';
import {interpolate} from 'remotion';

// Five trusted diagram parts for the illustrated explainer. They are drawn in the
// empty left half of the frame and never point into the picture. The model only
// fills text and a size relation; geometry is fixed here.
const ink = '#16324F', accent = '#E8833A', teal = '#2A8C8C', soft = '#5B6B7A', water = '#8CC4E0';
const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;
const BLOCKS = ['#D6453F', '#F0BE3C', '#3C6EBE', '#3CA064', '#FFFFFF'];

export type Overlay = any;

/** Phrases each element waits for, in display order. */
export function overlayCues(o: Overlay): string[] {
  if (o.kind === 'compare') return o.items.map((i: any) => i.label);
  if (o.kind === 'composition') return [...o.items, o.total];
  if (o.kind === 'formula') return [...o.terms, o.result, ...(o.bars ? [o.bars[0].label] : [])];
  if (o.kind === 'steps') return o.items;
  return [o.source, o.target];
}

/** Each element appears on the first phrase that says it, never before the one above it. */
export function revealFrames(cues: string[], scenes: any[], start: number): number[] {
  const frames: number[] = [];
  cues.forEach((cue, i) => {
    const mention = scenes.find((s: any) => (s.props.text as string).includes(cue));
    const own = mention ? mention.start_frame : start + 30 + i * 40;
    frames.push(i === 0 ? Math.max(own, start + 12) : Math.max(own, frames[i - 1] + 18));
  });
  return frames;
}

function appear(frame: number, at: number) {
  const v = interpolate(frame - at, [0, 14], [0, 1], clamp);
  return {opacity: v, transform: `translateY(${(1 - v) * 18}px)`};
}

function Glyph({glyph, size, color}: {glyph: string; size: number; color: string}) {
  const box = {width: 300, height: 270, position: 'relative' as const};
  if (glyph === 'pipe') {
    const w = Math.max(14, 86 * size);
    return <div style={box}>
      <div style={{position: 'absolute', left: 90, top: 0, width: 120, height: 56, borderRadius: '50%', background: '#FFFFFF', border: `5px solid ${ink}`}}/>
      <div style={{position: 'absolute', left: 150 - w / 2, top: 58, width: w, height: 212, background: color, borderRadius: 6}}/>
    </div>;
  }
  if (glyph === 'pool') {
    const w = 60 + 230 * size, h = 30 + 150 * size;
    return <div style={box}><div style={{position: 'absolute', left: 150 - w / 2, bottom: 0, width: w, height: h, borderRadius: 18, background: water, border: `5px solid ${ink}`}}/></div>;
  }
  if (glyph === 'arrows' || glyph === 'dots') {
    const count = Math.max(2, Math.round(2 + 14 * size));
    const cols = glyph === 'arrows' ? Math.min(count, 5) : 5;
    return <div style={{...box, display: 'grid', gridTemplateColumns: `repeat(${cols}, 1fr)`, alignContent: 'center', justifyItems: 'center', rowGap: 18}}>
      {Array.from({length: glyph === 'arrows' ? Math.min(count, 10) : count}, (_, i) => glyph === 'arrows'
        ? <div key={i} style={{fontSize: 52, lineHeight: 1, color, fontWeight: 900}}>↓</div>
        : <div key={i} style={{width: 26, height: 26, borderRadius: 13, background: color}}/>)}
    </div>;
  }
  if (glyph === 'reading') {
    return <div style={box}><div style={{position: 'absolute', left: 60, top: 30, width: 180, height: 210, borderRadius: 24, background: '#FFFFFF', border: `5px solid ${ink}`, overflow: 'hidden'}}>
      <div style={{position: 'absolute', left: 0, right: 0, bottom: 0, height: `${20 + 70 * size}%`, background: color}}/>
    </div></div>;
  }
  const h = 30 + 230 * size;
  return <div style={box}><div style={{position: 'absolute', left: 95, bottom: 0, width: 110, height: h, borderRadius: 10, background: color}}/></div>;
}

function Compare({o, at, frame}: {o: Overlay; at: number[]; frame: number}) {
  const sizes = o.relation === 'same' ? [1, 1] : o.relation === 'big_small' ? [1, .18] : [.18, 1];
  return <div style={{display: 'flex', gap: 60}}>
    {o.items.map((item: any, i: number) => <div key={i} style={{width: 320, ...appear(frame, at[i])}}>
      <Glyph glyph={o.glyph} size={sizes[i]} color={i === 0 ? ink : accent}/>
      <div style={{marginTop: 18, fontSize: 48, fontWeight: 800, color: ink}}>{item.label}</div>
      {item.note && <div style={{marginTop: 8, fontSize: 30, color: soft}}>{item.note}</div>}
    </div>)}
  </div>;
}

function Composition({o, at, frame}: {o: Overlay; at: number[]; frame: number}) {
  return <div style={{display: 'flex', flexDirection: 'column', gap: 16}}>
    {o.items.map((label: string, i: number) => <div key={i} style={{display: 'flex', alignItems: 'center', gap: 28, ...appear(frame, at[i])}}>
      <div style={{width: 190, height: 64, borderRadius: 12, background: BLOCKS[i % BLOCKS.length], border: `3px solid ${ink}`}}/>
      <div style={{fontSize: 42, fontWeight: 700, color: ink}}>{label}</div>
    </div>)}
    <div style={{marginTop: 12, fontSize: 42, fontWeight: 800, color: accent, ...appear(frame, at[o.items.length])}}>＝ {o.total}</div>
  </div>;
}

function Formula({o, at, frame}: {o: Overlay; at: number[]; frame: number}) {
  const bars = o.bars || [];
  const top = Math.max(...bars.map((b: any) => b.value), 1);
  const resultAt = at[o.terms.length];
  return <div>
    <div style={{display: 'flex', alignItems: 'center', gap: 18, flexWrap: 'wrap'}}>
      {o.terms.map((term: string, i: number) => <React.Fragment key={i}>
        {i > 0 && <div style={{fontSize: 64, fontWeight: 800, color: accent, ...appear(frame, at[i])}}>{o.operators[i - 1]}</div>}
        <div style={{padding: '18px 28px', borderRadius: 18, background: i === 0 ? ink : '#FFFFFF', color: i === 0 ? '#FFFFFF' : ink, border: `3px solid ${ink}`, fontSize: 44, fontWeight: 800, ...appear(frame, at[i])}}>{term}</div>
      </React.Fragment>)}
    </div>
    <div style={{marginTop: 26, fontSize: 44, fontWeight: 800, color: ink, ...appear(frame, resultAt)}}>＝ {o.result}</div>
    {bars.length === 2 && <div style={{marginTop: 30, display: 'flex', gap: 90, alignItems: 'flex-end', height: 190, ...appear(frame, at[o.terms.length + 1])}}>
      {bars.map((b: any, i: number) => <div key={i} style={{display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10}}>
        <div style={{width: 96, height: 20 + 130 * b.value / top, background: i === 0 ? accent : teal, borderRadius: 8}}/>
        <div style={{fontSize: 30, color: ink}}>{b.label}</div>
      </div>)}
    </div>}
  </div>;
}

function Steps({o, at, frame}: {o: Overlay; at: number[]; frame: number}) {
  return <div style={{display: 'flex', flexDirection: 'column', gap: 20}}>
    {o.items.map((text: string, i: number) => {
      const lit = frame >= at[i];
      return <div key={i} style={{display: 'flex', alignItems: 'center', gap: 22, ...appear(frame, at[i])}}>
        <div style={{width: 72, height: 72, borderRadius: 36, background: lit ? accent : '#D9D4C7', color: '#FFFFFF', fontSize: 40, fontWeight: 900, display: 'flex', alignItems: 'center', justifyContent: 'center'}}>{o.style === 'question' ? '?' : i + 1}</div>
        <div style={{fontSize: 44, fontWeight: 800, color: ink}}>{text}</div>
      </div>;
    })}
  </div>;
}

function Flow({o, at, frame}: {o: Overlay; at: number[]; frame: number}) {
  const chip = (text: string, dark: boolean) => <div style={{padding: '20px 30px', borderRadius: 18, background: dark ? ink : '#FFFFFF', color: dark ? '#FFFFFF' : ink, border: `3px solid ${ink}`, fontSize: 46, fontWeight: 800}}>{text}</div>;
  const grow = interpolate(frame - at[1], [0, 20], [0, 1], clamp);
  return <div style={{display: 'flex', alignItems: 'center', gap: 20}}>
    <div style={appear(frame, at[0])}>{chip(o.source, true)}</div>
    <div style={{position: 'relative', width: 220, height: 90}}>
      <div style={{position: 'absolute', left: 0, top: 58, height: 10, width: 190 * grow, background: accent, borderRadius: 5}}/>
      <div style={{position: 'absolute', left: 178 * grow, top: 40, fontSize: 46, lineHeight: 1, color: accent, opacity: grow, fontWeight: 900}}>▶</div>
      <div style={{position: 'absolute', left: 0, top: 0, width: 220, textAlign: 'center', fontSize: 28, color: soft, opacity: grow}}>{o.label}</div>
    </div>
    <div style={appear(frame, at[1])}>{chip(o.target, false)}</div>
  </div>;
}

export function OverlayPart({overlay, scenes, start, frame}: {overlay: Overlay; scenes: any[]; start: number; frame: number}) {
  const at = revealFrames(overlayCues(overlay), scenes, start);
  const props = {o: overlay, at, frame};
  if (overlay.kind === 'compare') return <Compare {...props}/>;
  if (overlay.kind === 'composition') return <Composition {...props}/>;
  if (overlay.kind === 'formula') return <Formula {...props}/>;
  if (overlay.kind === 'steps') return <Steps {...props}/>;
  if (overlay.kind === 'flow') return <Flow {...props}/>;
  return null;
}

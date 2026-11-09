import React from 'react';
import {AbsoluteFill, Audio, Img, interpolate, staticFile, useCurrentFrame} from 'remotion';
import {BrandHeaderContext} from './ContentStyle';
import {CornerBadge, type Badge} from './Packaging';
import {OverlayPart} from './IllustratedOverlays';

// Illustrated explainer: one full-bleed illustration per chapter. The illustration
// keeps its subject on the right; captions and keywords use the empty left half.
// Motion is computed over the whole chapter span, never per phrase, so the push
// does not restart at phrase boundaries.
const ink = '#16324F', accent = '#E8833A', paper = '#F6F1E4';
const font = '"Hiragino Sans GB", "PingFang SC", sans-serif';
const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;
const FADE = 18;

type Span = {index: number; start: number; end: number; image: string; scenes: any[]};

function chapterSpans(ir: any): Span[] {
  const spans: Span[] = [];
  for (const scene of ir.scenes) {
    const index = scene.props.chapter_index;
    const last = spans[spans.length - 1];
    if (last && last.index === index) {
      last.end = scene.end_frame;
      last.scenes.push(scene);
    } else spans.push({index, start: scene.start_frame, end: scene.end_frame, image: scene.props.image.image_path, scenes: [scene]});
  }
  return spans;
}

function pushTransform(frame: number, span: Span): string {
  const t = interpolate(frame, [span.start, Math.max(span.start + 1, span.end)], [0, 1], clamp);
  // Slow push toward the right-hand subject plus a slight drift.
  return `scale(${1.02 + 0.07 * t}) translateX(${-1.2 * t}%)`;
}

function Picture({span, frame, opacity}: {span: Span; frame: number; opacity: number}) {
  return <AbsoluteFill style={{opacity}}>
    <Img src={staticFile(span.image)} style={{width: '100%', height: '100%', objectFit: 'cover', transformOrigin: '72% 50%', transform: pushTransform(frame, span)}}/>
  </AbsoluteFill>;
}

export function IllustratedVideo({ir, badge}: {ir: any; badge?: Badge}) {
  const header = React.useContext(BrandHeaderContext);
  const frame = useCurrentFrame();
  const spans = React.useMemo(() => chapterSpans(ir), [ir]);
  const at = spans.findIndex(s => frame >= s.start && frame < s.end);
  const current = at >= 0 ? at : spans.length - 1;
  const span = spans[current];
  const previous = current > 0 ? spans[current - 1] : undefined;
  const fadeIn = previous ? interpolate(frame - span.start, [0, FADE], [0, 1], clamp) : 1;
  const scene = span.scenes.find((s: any) => frame >= s.start_frame && frame < s.end_frame) || span.scenes[span.scenes.length - 1];
  const p = scene.props;
  const cue = (ir.caption_cues ?? ir.narration.segments).find((s: any) => frame >= Math.round(s.start_ms * .03) && frame < Math.round(s.end_ms * .03));
  const captionText: string = cue ? cue.text : '';
  const textIn = interpolate(frame - span.start, [6, 24], [0, 1], clamp);
  const keywords: string[] = p.keywords || [];
  // Each keyword appears on the first phrase that says it (or at a fixed offset),
  // and never before the keyword listed above it.
  const keywordAt: number[] = [];
  keywords.forEach((word: string, i: number) => {
    const mention = span.scenes.find((s: any) => (s.props.text as string).includes(word));
    const own = mention ? mention.start_frame : span.start + 45 + i * 60;
    keywordAt.push(i === 0 ? own : Math.max(own, keywordAt[i - 1] + 20));
  });
  const top = header ? header.top + 70 : 110;
  return <AbsoluteFill style={{background: paper, fontFamily: font, color: ink, overflow: 'hidden'}}>
    {previous && fadeIn < 1 && <Picture span={previous} frame={frame} opacity={1}/>}
    <Picture span={span} frame={frame} opacity={fadeIn}/>
    <div style={{position: 'absolute', inset: 0, background: 'linear-gradient(90deg, rgba(246,241,228,.94) 0%, rgba(246,241,228,.82) 30%, rgba(246,241,228,0) 52%)'}}/>
    <div style={{position: 'absolute', left: 110, top, width: 760, opacity: textIn, transform: `translateY(${(1 - textIn) * 14}px)`}}>
      <div style={{fontSize: 26, letterSpacing: 3, color: accent, fontWeight: 700}}>{String(current + 1).padStart(2, '0')} / {String(spans.length).padStart(2, '0')}</div>
      <div style={{marginTop: 14, fontSize: 52, lineHeight: 1.3, fontWeight: 800, overflowWrap: 'anywhere'}}>{p.title}</div>
      <div style={{marginTop: 22, height: 5, width: 84, background: accent}}/>
    </div>
    {p.overlay ? <div style={{position: 'absolute', left: 110, top: top + 215, width: 860}}>
      <OverlayPart overlay={p.overlay} scenes={span.scenes} start={span.start} frame={frame}/>
    </div> : <div style={{position: 'absolute', left: 110, top: top + 250, width: 760, display: 'flex', flexDirection: 'column', gap: 22}}>
      {keywords.map((word: string, i: number) => {
        const shown = interpolate(frame - keywordAt[i], [0, 12], [0, 1], clamp);
        return <div key={i} style={{alignSelf: 'flex-start', opacity: shown, transform: `translateX(${(1 - shown) * -24}px)`, padding: '14px 30px', borderRadius: 18, background: i === 0 ? ink : '#FFFFFF', color: i === 0 ? '#FFFFFF' : ink, fontSize: 46, fontWeight: 800, boxShadow: '0 10px 28px rgba(22,50,79,.14)'}}>{word}</div>;
      })}
    </div>}
    {captionText && <div style={{position: 'absolute', left: 110, width: 800, bottom: 92}}>
      <div style={{display: 'inline-block', padding: '16px 26px', background: 'rgba(22,50,79,.88)', color: '#FFFFFF', borderRadius: 16, fontSize: 38, lineHeight: 1.5, overflowWrap: 'anywhere'}}>{captionText}</div>
    </div>}
    {!header && <CornerBadge badge={badge}/>}
    <div style={{position: 'absolute', bottom: 0, left: 0, height: 6, width: `${100 * (frame + 1) / ir.total_frames}%`, background: accent}}/>
    <Audio src={staticFile(ir.audio_public_file)}/>
  </AbsoluteFill>;
}

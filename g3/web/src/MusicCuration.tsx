import React, {useState} from 'react';
import type {MusicAsset} from './MusicOptions';

export type MusicCurationInfo = {
  common:boolean; display_name?:string; purpose?:string; rhythm?:string; recipe_ids?:string[];
  loop_method:string; loop_preview_url?:string; narration_preview_url?:string;
  technical?:{status:string;preview_join_seconds:number;duration_seconds:number};
  review:{status:string;no_unexpected_vocals?:boolean;natural_loop?:boolean;narration_friendly?:boolean;note?:string};
};
const recipes=[['ai_news','快讯'],['how_to','教程'],['science_explainer','科普']];

export function MusicTags({value}:{value?:MusicCurationInfo}) {
  if(!value)return null;
  return <div className="music-tags">{value.rhythm&&<span>节奏方向：{value.rhythm}</span>}<span>交叉淡化循环</span><span>{value.technical?.status==='passed'?'接缝技术检查通过':value.technical?.status==='needs_work'?'技术检查待处理':'技术检查待完成'}</span><span className={value.review.status==='passed'?'review-passed':'review-pending'}>{value.review.status==='passed'?'已试听确认':value.review.status==='needs_work'?'听感需调整':'待试听确认'}</span></div>;
}

export function MusicReview({asset,onRefresh}:{asset:MusicAsset;onRefresh:()=>void}) {
  const c=asset.curation;
  const [flags,setFlags]=useState({no_unexpected_vocals:false,natural_loop:false,narration_friendly:false});
  const [listened,setListened]=useState(false),[note,setNote]=useState(''),[busy,setBusy]=useState(false),[message,setMessage]=useState('');
  if(!c||!asset.available)return null;
  async function action(path:string,body?:object){
    setBusy(true);setMessage('');
    try {
      const response=await fetch(`/api/music-assets/${asset.id}/${path}`,{method:'POST',headers:{'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});
      const result=await response.json();if(!response.ok)throw new Error(result.detail?.message||'无法保存验收记录');
      setMessage(path==='analyze'?'技术检查已完成，听感仍需试听确认。':'试听结论已保存。');onRefresh();
    }catch(e){setMessage(e instanceof Error?e.message:'操作失败');}
    finally{setBusy(false);}
  }
  return <details className="music-review"><summary>试听与验收</summary>
    <p>{c.loop_method}。采样检查不代表节拍、和声衔接自然；以下听感结论由实际试听者填写。</p>
    {c.loop_preview_url&&<><label>循环接缝试听 · 约第 {c.technical?.preview_join_seconds} 秒重复</label><audio controls preload="none" src={c.loop_preview_url} aria-label={`${asset.name}循环接缝试听`}/></>}
    {c.narration_preview_url&&<><label>与实际旁白混音试听</label><audio controls preload="none" src={c.narration_preview_url} aria-label={`${asset.name}旁白配乐试听`}/></>}
    <button type="button" className="secondary-button" disabled={busy} onClick={()=>void action('analyze')}>{c.technical?'重新检查音频':'检查音频并准备接缝试听'}</button>
    {c.technical?.status==='passed'&&<div className="music-review-form">
      <label><input type="checkbox" checked={listened} onChange={e=>setListened(e.target.checked)}/>我已试听原曲和循环接缝</label>
      <label><input type="checkbox" checked={flags.no_unexpected_vocals} onChange={e=>setFlags({...flags,no_unexpected_vocals:e.target.checked})}/>没有意外人声或歌词</label>
      <label><input type="checkbox" checked={flags.natural_loop} onChange={e=>setFlags({...flags,natural_loop:e.target.checked})}/>循环的节奏与和声衔接自然</label>
      <label><input type="checkbox" checked={flags.narration_friendly} onChange={e=>setFlags({...flags,narration_friendly:e.target.checked})}/>当前默认混音下不抢旁白讲解</label>
      <textarea className="text-input" aria-label={`${asset.name}试听备注`} value={note} maxLength={300} placeholder="可选：记录听到的问题或适合的用途" onChange={e=>setNote(e.target.value)}/>
      <button type="button" className="secondary-button" disabled={!listened||busy} onClick={()=>void action('review',{asset_sha256:asset.sha256,listened,...flags,note})}>{busy?'保存中…':'保存试听结论'}</button>
      <small>三项听感均确认才标记通过；有未确认项会记录为需调整，仍保留原音乐。</small>
    </div>}
    {c.review.note&&<p>上次试听备注：{c.review.note}</p>}{message&&<p role="status">{message}</p>}
  </details>;
}

export function CommonMusic({assets,onSelect,onRefresh}:{assets:MusicAsset[];onSelect:(id:string)=>void;onRefresh:()=>void}) {
  const [recipe,setRecipe]=useState('ai_news');
  const curated=assets.filter(a=>a.available&&a.curation?.common&&a.curation.recipe_ids?.includes(recipe));
  return <section className="common-music" aria-label="常用无词配乐"><h2>常用无词配乐</h2><p>按用途整理，每个题材 1–2 首。技术检查与听感确认分别标注，原音乐和生成记录保留。</p>
    <div className="resource-tabs" role="group" aria-label="配乐用途">{recipes.map(([id,label])=><button type="button" key={id} aria-pressed={recipe===id} className={recipe===id?'selected':''} onClick={()=>setRecipe(id)}>{label}</button>)}</div>
    <div className="resource-grid">{curated.map(a=><article className="resource-card" key={a.id}><h3>{a.curation?.display_name||a.name}</h3><p>{a.curation?.purpose}</p><MusicTags value={a.curation}/><audio controls preload="none" src={a.preview_url} aria-label={`${a.name}常用配乐试听`}/><button type="button" className="secondary-button" onClick={()=>onSelect(a.id)}>用作背景配乐</button><MusicReview asset={a} onRefresh={onRefresh}/></article>)}</div>
    {!curated.length&&<p>本题材的常用配乐正在整理，原素材库仍可使用。</p>}
  </section>;
}
